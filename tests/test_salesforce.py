"""Tests for Salesforce Apex and metadata XML extractors.

Covers:
- Apex class extraction (symbols, references, inheritance, annotations)
- Apex trigger extraction
- Apex enum, interface, inner class support
- Apex properties with get/set accessors
- Salesforce XML metadata extraction (CustomObject, fields, etc.)
- LWC metadata extraction
- End-to-end indexing of a Salesforce DX project structure
"""

import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from conftest import roam, git_init


# ============================================================================
# Apex extractor unit tests
# ============================================================================


@pytest.fixture
def apex_extractor():
    from roam.languages.apex_lang import ApexExtractor
    return ApexExtractor()


@pytest.fixture
def apex_parser():
    from tree_sitter_language_pack import get_parser
    return get_parser("apex")


def _parse_apex(parser, code: str):
    source = code.encode("utf-8")
    tree = parser.parse(source)
    return tree, source


class TestApexClassExtraction:
    """Test Apex class symbol extraction."""

    def test_basic_class(self, apex_extractor, apex_parser):
        tree, source = _parse_apex(apex_parser, """
public class AccountHandler {
    public void processAccounts() {
        System.debug('hello');
    }
}
""")
        symbols = apex_extractor.extract_symbols(tree, source, "AccountHandler.cls")
        names = [s["name"] for s in symbols]
        assert "AccountHandler" in names
        assert "processAccounts" in names

        cls = next(s for s in symbols if s["name"] == "AccountHandler")
        assert cls["kind"] == "class"
        assert cls["visibility"] == "public"
        assert cls["is_exported"] is True

        method = next(s for s in symbols if s["name"] == "processAccounts")
        assert method["kind"] == "method"
        assert method["parent_name"] == "AccountHandler"

    def test_class_with_sharing(self, apex_extractor, apex_parser):
        tree, source = _parse_apex(apex_parser, """
public with sharing class SecureHandler {
    public void doWork() {}
}
""")
        symbols = apex_extractor.extract_symbols(tree, source, "SecureHandler.cls")
        cls = next(s for s in symbols if s["name"] == "SecureHandler")
        assert "with sharing" in cls["signature"]

    def test_class_inheritance(self, apex_extractor, apex_parser):
        code = """
public class ChildHandler extends BaseHandler implements IHandler, Schedulable {
    public void execute() {}
}
"""
        tree, source = _parse_apex(apex_parser, code)
        # Need to extract symbols first (populates _pending_inherits)
        symbols = apex_extractor.extract_symbols(tree, source, "ChildHandler.cls")
        refs = apex_extractor.extract_references(tree, source, "ChildHandler.cls")

        cls = next(s for s in symbols if s["name"] == "ChildHandler")
        assert "extends BaseHandler" in cls["signature"]
        assert "implements" in cls["signature"]

        # Check inheritance references
        inherits_refs = [r for r in refs if r["kind"] == "inherits"]
        assert any(r["target_name"] == "BaseHandler" for r in inherits_refs)

        implements_refs = [r for r in refs if r["kind"] == "implements"]
        impl_targets = {r["target_name"] for r in implements_refs}
        assert "IHandler" in impl_targets
        assert "Schedulable" in impl_targets

    def test_annotations(self, apex_extractor, apex_parser):
        tree, source = _parse_apex(apex_parser, """
public class MyController {
    @AuraEnabled(cacheable=true)
    public static List<Account> getAccounts() {
        return [SELECT Id FROM Account];
    }

    @TestVisible
    private void helperMethod() {}
}
""")
        symbols = apex_extractor.extract_symbols(tree, source, "MyController.cls")
        aura_method = next(s for s in symbols if s["name"] == "getAccounts")
        assert "@AuraEnabled" in aura_method["signature"]
        assert "static" in aura_method["signature"]

        test_method = next(s for s in symbols if s["name"] == "helperMethod")
        assert "@TestVisible" in test_method["signature"]
        assert test_method["visibility"] == "private"

    def test_constructor(self, apex_extractor, apex_parser):
        tree, source = _parse_apex(apex_parser, """
public class MyService {
    private String name;
    public MyService(String name) {
        this.name = name;
    }
}
""")
        symbols = apex_extractor.extract_symbols(tree, source, "MyService.cls")
        ctor = next(s for s in symbols if s["kind"] == "constructor")
        assert ctor["name"] == "MyService"
        assert "String name" in ctor["signature"]


class TestApexFieldsAndProperties:
    """Test Apex field and property extraction."""

    def test_fields(self, apex_extractor, apex_parser):
        tree, source = _parse_apex(apex_parser, """
public class Config {
    private static final String API_KEY = 'abc123';
    public Integer retryCount;
}
""")
        symbols = apex_extractor.extract_symbols(tree, source, "Config.cls")
        api_key = next(s for s in symbols if s["name"] == "API_KEY")
        assert api_key["kind"] == "constant"
        assert "static" in api_key["signature"]
        assert "final" in api_key["signature"]

        retry = next(s for s in symbols if s["name"] == "retryCount")
        assert retry["kind"] == "field"

    def test_properties_with_accessors(self, apex_extractor, apex_parser):
        tree, source = _parse_apex(apex_parser, """
public class MyClass {
    public String name { get; set; }
    public Integer count { get; private set; }
}
""")
        symbols = apex_extractor.extract_symbols(tree, source, "MyClass.cls")
        name_prop = next(s for s in symbols if s["name"] == "name")
        assert name_prop["kind"] == "property"
        assert "get" in name_prop["signature"]
        assert "set" in name_prop["signature"]


class TestApexEnumAndInterface:
    """Test Apex enum and interface extraction."""

    def test_enum(self, apex_extractor, apex_parser):
        tree, source = _parse_apex(apex_parser, """
public class Outer {
    public enum Season { WINTER, SPRING, SUMMER, FALL }
}
""")
        symbols = apex_extractor.extract_symbols(tree, source, "Outer.cls")
        enum = next(s for s in symbols if s["name"] == "Season")
        assert enum["kind"] == "enum"
        assert enum["parent_name"] == "Outer"

        constants = [s for s in symbols if s["kind"] == "constant"]
        const_names = {s["name"] for s in constants}
        assert {"WINTER", "SPRING", "SUMMER", "FALL"} <= const_names

    def test_interface(self, apex_extractor, apex_parser):
        tree, source = _parse_apex(apex_parser, """
public interface IHandler {
    void process(List<Account> records);
    Boolean validate(Account record);
}
""")
        symbols = apex_extractor.extract_symbols(tree, source, "IHandler.cls")
        iface = next(s for s in symbols if s["name"] == "IHandler")
        assert iface["kind"] == "interface"

        methods = [s for s in symbols if s["kind"] == "method"]
        method_names = {s["name"] for s in methods}
        assert "process" in method_names
        assert "validate" in method_names

    def test_inner_class(self, apex_extractor, apex_parser):
        tree, source = _parse_apex(apex_parser, """
public class Outer {
    public class Inner {
        public String value;
        public void doWork() {}
    }
}
""")
        symbols = apex_extractor.extract_symbols(tree, source, "Outer.cls")
        inner = next(s for s in symbols if s["name"] == "Inner")
        assert inner["kind"] == "class"
        assert inner["parent_name"] == "Outer"
        assert inner["qualified_name"] == "Outer.Inner"

        method = next(s for s in symbols if s["name"] == "doWork")
        assert method["parent_name"] == "Outer.Inner"


class TestApexTrigger:
    """Test Apex trigger extraction."""

    def test_trigger_basic(self, apex_extractor, apex_parser):
        tree, source = _parse_apex(apex_parser, """
trigger AccountTrigger on Account (before insert, after update) {
    AccountHandler.handleBeforeInsert(Trigger.new);
}
""")
        symbols = apex_extractor.extract_symbols(tree, source, "AccountTrigger.trigger")
        trigger = next(s for s in symbols if s["name"] == "AccountTrigger")
        assert trigger["kind"] == "trigger"
        assert "on Account" in trigger["signature"]
        assert "before insert" in trigger["signature"]
        assert "after update" in trigger["signature"]

    def test_trigger_references(self, apex_extractor, apex_parser):
        code = """
trigger AccountTrigger on Account (before insert) {
    AccountHandler.handleBeforeInsert(Trigger.new);
}
"""
        tree, source = _parse_apex(apex_parser, code)
        symbols = apex_extractor.extract_symbols(tree, source, "AccountTrigger.trigger")
        refs = apex_extractor.extract_references(tree, source, "AccountTrigger.trigger")

        # Should reference the SObject
        sobject_refs = [r for r in refs if r["target_name"] == "Account"]
        assert len(sobject_refs) > 0

        # Should reference the handler method
        call_refs = [r for r in refs if r["kind"] == "call"]
        call_targets = {r["target_name"] for r in call_refs}
        assert any("handleBeforeInsert" in t for t in call_targets)


class TestApexReferences:
    """Test Apex reference extraction."""

    def test_method_calls(self, apex_extractor, apex_parser):
        code = """
public class MyClass {
    public void doWork() {
        System.debug('starting');
        String result = helper();
        Database.insert(records, false);
    }
    private String helper() { return 'ok'; }
}
"""
        tree, source = _parse_apex(apex_parser, code)
        refs = apex_extractor.extract_references(tree, source, "MyClass.cls")

        call_refs = [r for r in refs if r["kind"] == "call"]
        targets = {r["target_name"] for r in call_refs}
        assert any("System.debug" in t for t in targets)
        assert any("Database.insert" in t for t in targets)

    def test_dml_references(self, apex_extractor, apex_parser):
        code = """
public class DmlExample {
    public void dmlOps() {
        Account a = new Account(Name='Test');
        insert a;
        a.Name = 'Updated';
        update a;
        delete a;
    }
}
"""
        tree, source = _parse_apex(apex_parser, code)
        refs = apex_extractor.extract_references(tree, source, "DmlExample.cls")

        dml_refs = [r for r in refs if "DML." in r["target_name"]]
        dml_ops = {r["target_name"] for r in dml_refs}
        assert "DML.insert" in dml_ops
        assert "DML.update" in dml_ops
        assert "DML.delete" in dml_ops


class TestApexDocstring:
    """Test Apex docstring extraction."""

    def test_apexdoc(self, apex_extractor, apex_parser):
        tree, source = _parse_apex(apex_parser, """
/**
 * Service class for Account operations.
 * @author admin
 */
public class AccountService {
    /**
     * Finds accounts by name.
     * @param term Search term
     * @return List of matching accounts
     */
    public List<Account> find(String term) {
        return null;
    }
}
""")
        symbols = apex_extractor.extract_symbols(tree, source, "AccountService.cls")
        cls = next(s for s in symbols if s["name"] == "AccountService")
        assert cls["docstring"] is not None
        assert "Account operations" in cls["docstring"]

        method = next(s for s in symbols if s["name"] == "find")
        assert method["docstring"] is not None
        assert "Finds accounts" in method["docstring"]


# ============================================================================
# Salesforce XML metadata extractor tests
# ============================================================================


@pytest.fixture
def sfxml_extractor():
    from roam.languages.sfxml_lang import SalesforceXmlExtractor
    return SalesforceXmlExtractor()


@pytest.fixture
def xml_parser():
    from tree_sitter_language_pack import get_parser
    return get_parser("xml")


def _parse_xml(parser, code: str):
    source = code.encode("utf-8")
    tree = parser.parse(source)
    return tree, source


class TestSfXmlCustomObject:
    """Test Salesforce CustomObject XML metadata extraction."""

    def test_custom_object_fields(self, sfxml_extractor, xml_parser):
        tree, source = _parse_xml(xml_parser, """<?xml version="1.0" encoding="UTF-8"?>
<CustomObject xmlns="http://soap.sforce.com/2006/04/metadata">
    <fields>
        <fullName>Industry__c</fullName>
        <label>Industry</label>
        <type>Picklist</type>
        <required>true</required>
    </fields>
    <fields>
        <fullName>Revenue__c</fullName>
        <label>Revenue</label>
        <type>Currency</type>
    </fields>
    <validationRules>
        <fullName>Name_Required</fullName>
        <active>true</active>
    </validationRules>
</CustomObject>
""")
        symbols = sfxml_extractor.extract_symbols(
            tree, source, "objects/Account/Account.object-meta.xml"
        )
        names = [s["name"] for s in symbols]

        # Root object should be extracted
        assert "Account" in names

        # Fields
        assert "Industry__c" in names
        assert "Revenue__c" in names

        industry = next(s for s in symbols if s["name"] == "Industry__c")
        assert industry["kind"] == "field"
        assert "Picklist" in industry["signature"]
        assert "[required]" in industry["signature"]

        # Validation rule
        assert "Name_Required" in names
        vr = next(s for s in symbols if s["name"] == "Name_Required")
        assert vr["kind"] == "function"

    def test_custom_object_references(self, sfxml_extractor, xml_parser):
        tree, source = _parse_xml(xml_parser, """<?xml version="1.0" encoding="UTF-8"?>
<CustomObject xmlns="http://soap.sforce.com/2006/04/metadata">
    <fields>
        <fullName>ParentAccount__c</fullName>
        <type>Lookup</type>
        <referenceTo>Account</referenceTo>
    </fields>
</CustomObject>
""")
        refs = sfxml_extractor.extract_references(
            tree, source, "objects/Child__c/Child__c.object-meta.xml"
        )
        ref_targets = {r["target_name"] for r in refs}
        assert "Account" in ref_targets


class TestSfXmlLwcMetadata:
    """Test Lightning Web Component metadata extraction."""

    def test_lwc_meta(self, sfxml_extractor, xml_parser):
        tree, source = _parse_xml(xml_parser, """<?xml version="1.0" encoding="UTF-8"?>
<LightningComponentBundle xmlns="http://soap.sforce.com/2006/04/metadata">
    <apiVersion>58.0</apiVersion>
    <isExposed>true</isExposed>
    <masterLabel>Account List</masterLabel>
</LightningComponentBundle>
""")
        symbols = sfxml_extractor.extract_symbols(
            tree, source, "lwc/accountList/accountList.js-meta.xml"
        )
        names = [s["name"] for s in symbols]
        # Should derive name from file path or masterLabel
        assert len(symbols) > 0
        # The LightningComponentBundle should be extracted
        bundle = next(s for s in symbols if s["kind"] == "class")
        assert bundle is not None


class TestSfXmlProfilePermissions:
    """Test Profile/PermissionSet metadata extraction."""

    def test_profile(self, sfxml_extractor, xml_parser):
        tree, source = _parse_xml(xml_parser, """<?xml version="1.0" encoding="UTF-8"?>
<Profile xmlns="http://soap.sforce.com/2006/04/metadata">
    <classAccesses>
        <apexClass>AccountHandler</apexClass>
        <enabled>true</enabled>
    </classAccesses>
    <fieldPermissions>
        <field>Account.Industry__c</field>
        <readable>true</readable>
        <editable>false</editable>
    </fieldPermissions>
</Profile>
""")
        symbols = sfxml_extractor.extract_symbols(
            tree, source, "profiles/Admin.profile-meta.xml"
        )
        assert len(symbols) > 0

        refs = sfxml_extractor.extract_references(
            tree, source, "profiles/Admin.profile-meta.xml"
        )
        ref_targets = {r["target_name"] for r in refs}
        assert "AccountHandler" in ref_targets


# ============================================================================
# Language detection tests
# ============================================================================


class TestSalesforceLanguageDetection:
    """Test that Salesforce file extensions are correctly detected."""

    def test_apex_class_detection(self):
        from roam.languages.registry import get_language_for_file
        assert get_language_for_file("MyClass.cls") == "apex"

    def test_apex_trigger_detection(self):
        from roam.languages.registry import get_language_for_file
        assert get_language_for_file("AccountTrigger.trigger") == "apex"

    def test_meta_xml_detection(self):
        from roam.languages.registry import get_language_for_file
        assert get_language_for_file("Account.object-meta.xml") == "sfxml"
        assert get_language_for_file("MyClass.cls-meta.xml") == "sfxml"
        assert get_language_for_file("myComponent.js-meta.xml") == "sfxml"

    def test_parser_detect_language(self):
        from roam.index.parser import detect_language
        assert detect_language("classes/MyClass.cls") == "apex"
        assert detect_language("triggers/MyTrigger.trigger") == "apex"
        assert detect_language("objects/Account/Account.object-meta.xml") == "sfxml"

    def test_extractor_factory(self):
        from roam.languages.registry import get_extractor
        from roam.languages.apex_lang import ApexExtractor
        from roam.languages.sfxml_lang import SalesforceXmlExtractor

        assert isinstance(get_extractor("apex"), ApexExtractor)
        assert isinstance(get_extractor("sfxml"), SalesforceXmlExtractor)


# ============================================================================
# End-to-end integration test: Salesforce DX project
# ============================================================================


@pytest.fixture(scope="module")
def salesforce_project(tmp_path_factory):
    """Create a Salesforce DX project structure and index it."""
    proj = tmp_path_factory.mktemp("sfdx_project")

    # Create Salesforce DX directory structure
    classes_dir = proj / "force-app" / "main" / "default" / "classes"
    classes_dir.mkdir(parents=True)

    triggers_dir = proj / "force-app" / "main" / "default" / "triggers"
    triggers_dir.mkdir(parents=True)

    objects_dir = proj / "force-app" / "main" / "default" / "objects" / "Account" / "fields"
    objects_dir.mkdir(parents=True)

    lwc_dir = proj / "force-app" / "main" / "default" / "lwc" / "accountList"
    lwc_dir.mkdir(parents=True)

    # Apex class
    (classes_dir / "AccountHandler.cls").write_text(
        '/**\n'
        ' * Handler for Account trigger operations.\n'
        ' */\n'
        'public with sharing class AccountHandler {\n'
        '\n'
        '    public static void handleBeforeInsert(List<Account> newAccounts) {\n'
        '        for (Account acc : newAccounts) {\n'
        '            if (acc.Name == null) {\n'
        '                acc.addError(\'Name is required\');\n'
        '            }\n'
        '        }\n'
        '    }\n'
        '\n'
        '    @AuraEnabled(cacheable=true)\n'
        '    public static List<Account> getAccounts(String searchKey) {\n'
        '        String key = \'%\' + searchKey + \'%\';\n'
        '        return [SELECT Id, Name FROM Account WHERE Name LIKE :key];\n'
        '    }\n'
        '}\n'
    )

    (classes_dir / "AccountHandler.cls-meta.xml").write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<ApexClass xmlns="http://soap.sforce.com/2006/04/metadata">\n'
        '    <apiVersion>58.0</apiVersion>\n'
        '    <status>Active</status>\n'
        '</ApexClass>\n'
    )

    # Apex trigger
    (triggers_dir / "AccountTrigger.trigger").write_text(
        'trigger AccountTrigger on Account (before insert, before update) {\n'
        '    if (Trigger.isBefore && Trigger.isInsert) {\n'
        '        AccountHandler.handleBeforeInsert(Trigger.new);\n'
        '    }\n'
        '}\n'
    )

    (triggers_dir / "AccountTrigger.trigger-meta.xml").write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<ApexTrigger xmlns="http://soap.sforce.com/2006/04/metadata">\n'
        '    <apiVersion>58.0</apiVersion>\n'
        '    <status>Active</status>\n'
        '</ApexTrigger>\n'
    )

    # Custom field metadata
    (objects_dir / "Industry__c.field-meta.xml").write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<CustomField xmlns="http://soap.sforce.com/2006/04/metadata">\n'
        '    <fullName>Industry__c</fullName>\n'
        '    <label>Industry</label>\n'
        '    <type>Picklist</type>\n'
        '    <required>true</required>\n'
        '</CustomField>\n'
    )

    # LWC component
    (lwc_dir / "accountList.js").write_text(
        "import { LightningElement, wire } from 'lwc';\n"
        "import getAccounts from '@salesforce/apex/AccountHandler.getAccounts';\n"
        "\n"
        "export default class AccountList extends LightningElement {\n"
        "    accounts;\n"
        "    searchKey = '';\n"
        "\n"
        "    @wire(getAccounts, { searchKey: '$searchKey' })\n"
        "    wiredAccounts({ data, error }) {\n"
        "        if (data) {\n"
        "            this.accounts = data;\n"
        "        }\n"
        "    }\n"
        "}\n"
    )

    (lwc_dir / "accountList.html").write_text(
        '<template>\n'
        '    <lightning-card title="Account List">\n'
        '        <template for:each={accounts} for:item="acc">\n'
        '            <p key={acc.Id}>{acc.Name}</p>\n'
        '        </template>\n'
        '    </lightning-card>\n'
        '</template>\n'
    )

    (lwc_dir / "accountList.js-meta.xml").write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<LightningComponentBundle xmlns="http://soap.sforce.com/2006/04/metadata">\n'
        '    <apiVersion>58.0</apiVersion>\n'
        '    <isExposed>true</isExposed>\n'
        '    <masterLabel>Account List</masterLabel>\n'
        '</LightningComponentBundle>\n'
    )

    # sfdx-project.json
    (proj / "sfdx-project.json").write_text(
        '{\n'
        '  "packageDirectories": [{"path": "force-app", "default": true}],\n'
        '  "namespace": "",\n'
        '  "sfdcLoginUrl": "https://login.salesforce.com",\n'
        '  "sourceApiVersion": "58.0"\n'
        '}\n'
    )

    git_init(proj)

    # Index the project
    out, rc = roam("index", cwd=str(proj))
    assert rc == 0, f"Index failed: {out}"

    return proj


class TestSalesforceE2E:
    """End-to-end tests for Salesforce project indexing."""

    def test_index_succeeds(self, salesforce_project):
        """Verify that the Salesforce project indexes without errors."""
        out, rc = roam("index", cwd=str(salesforce_project))
        assert rc == 0

    def test_apex_class_in_map(self, salesforce_project):
        """Verify Apex files are counted in the project map."""
        out, rc = roam("map", cwd=str(salesforce_project))
        assert rc == 0
        # Map shows language stats — apex files should be counted
        assert "apex" in out

    def test_apex_trigger_symbol(self, salesforce_project):
        """Verify Apex trigger can be looked up as a symbol."""
        out, rc = roam("symbol", "AccountTrigger", cwd=str(salesforce_project))
        assert rc == 0
        assert "AccountTrigger" in out

    def test_apex_symbols_lookup(self, salesforce_project):
        """Verify Apex symbols can be looked up."""
        out, rc = roam("symbol", "AccountHandler", cwd=str(salesforce_project))
        assert rc == 0
        assert "AccountHandler" in out

    def test_lwc_js_indexed(self, salesforce_project):
        """Verify LWC JavaScript files are indexed."""
        out, rc = roam("map", cwd=str(salesforce_project))
        assert rc == 0
        # Map shows javascript language in the stats
        assert "javascript" in out

    def test_sfxml_metadata_indexed(self, salesforce_project):
        """Verify Salesforce metadata XML files are indexed."""
        out, rc = roam("map", cwd=str(salesforce_project))
        assert rc == 0
        assert "sfxml" in out

    def test_deps_command(self, salesforce_project):
        """Verify deps command works on Apex files."""
        out, rc = roam("deps", "force-app/main/default/classes/AccountHandler.cls",
                       cwd=str(salesforce_project))
        # Should not error (may or may not show deps depending on resolution)
        assert rc == 0
