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
        # Sidecar .js-meta.xml files should NOT produce top-level class
        # symbols — the primary .js file already provides the canonical
        # symbol.  This prevents duplicate search results (Issue 8).
        assert len(symbols) == 0


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


# ============================================================================
# Phase 1: LWC → Apex import wiring tests
# ============================================================================


class TestLwcSalesforceImports:
    """Test that @salesforce/* imports produce correct references."""

    def test_salesforce_apex_import(self):
        from tree_sitter_language_pack import get_parser
        from roam.languages.javascript_lang import JavaScriptExtractor

        parser = get_parser("javascript")
        ext = JavaScriptExtractor()
        code = b"import getAccounts from '@salesforce/apex/AccountHandler.getAccounts';\n"
        tree = parser.parse(code)
        refs = ext.extract_references(tree, code, "accountList.js")

        # @salesforce/apex imports produce "call" edges (cross-language RPC)
        call_refs = [r for r in refs if r["kind"] == "call"]
        assert len(call_refs) >= 1
        # The target should be the Apex qualified name, not the JS local binding
        assert any(r["target_name"] == "AccountHandler.getAccounts" for r in call_refs)
        # Should also have a call edge to the class itself
        assert any(r["target_name"] == "AccountHandler" for r in call_refs)

    def test_salesforce_schema_import(self):
        from tree_sitter_language_pack import get_parser
        from roam.languages.javascript_lang import JavaScriptExtractor

        parser = get_parser("javascript")
        ext = JavaScriptExtractor()
        code = b"import ACCOUNT_NAME from '@salesforce/schema/Account.Name';\n"
        tree = parser.parse(code)
        refs = ext.extract_references(tree, code, "test.js")

        import_refs = [r for r in refs if r["kind"] == "import"]
        assert any(r["target_name"] == "Account.Name" for r in import_refs)

    def test_salesforce_label_import(self):
        from tree_sitter_language_pack import get_parser
        from roam.languages.javascript_lang import JavaScriptExtractor

        parser = get_parser("javascript")
        ext = JavaScriptExtractor()
        code = b"import greeting from '@salesforce/label/c.Greeting';\n"
        tree = parser.parse(code)
        refs = ext.extract_references(tree, code, "test.js")

        import_refs = [r for r in refs if r["kind"] == "import"]
        assert any(r["target_name"] == "Greeting" for r in import_refs)

    def test_non_salesforce_import_unchanged(self):
        from tree_sitter_language_pack import get_parser
        from roam.languages.javascript_lang import JavaScriptExtractor

        parser = get_parser("javascript")
        ext = JavaScriptExtractor()
        code = b"import { LightningElement } from 'lwc';\n"
        tree = parser.parse(code)
        refs = ext.extract_references(tree, code, "test.js")

        import_refs = [r for r in refs if r["kind"] == "import"]
        assert any(r["target_name"] == "LightningElement" for r in import_refs)


class TestSalesforceImportResolution:
    """Test that _resolve_salesforce_import works in relations.py."""

    def test_apex_import_resolution(self):
        from roam.index.relations import _resolve_salesforce_import

        candidates = [
            {"file_path": "force-app/main/default/classes/AccountHandler.cls", "name": "AccountHandler"},
            {"file_path": "force-app/main/default/classes/OtherClass.cls", "name": "OtherClass"},
        ]
        matches = _resolve_salesforce_import("@salesforce/apex/AccountHandler.getAccounts", candidates)
        assert len(matches) == 1
        assert matches[0]["name"] == "AccountHandler"

    def test_schema_import_resolution(self):
        from roam.index.relations import _resolve_salesforce_import

        candidates = [
            {"qualified_name": "Account.Industry__c", "name": "Industry__c"},
            {"qualified_name": "Contact.Email__c", "name": "Email__c"},
        ]
        matches = _resolve_salesforce_import("@salesforce/schema/Account.Industry__c", candidates)
        assert len(matches) == 1
        assert matches[0]["name"] == "Industry__c"

    def test_label_import_resolution(self):
        from roam.index.relations import _resolve_salesforce_import

        candidates = [
            {"name": "Greeting", "kind": "constant"},
            {"name": "Farewell", "kind": "constant"},
        ]
        matches = _resolve_salesforce_import("@salesforce/label/c.Greeting", candidates)
        assert len(matches) == 1
        assert matches[0]["name"] == "Greeting"

    def test_non_salesforce_returns_empty(self):
        from roam.index.relations import _resolve_salesforce_import

        matches = _resolve_salesforce_import("./utils/helper", [{"name": "helper"}])
        assert matches == []

    def test_apex_namespace_prefix_resolution(self):
        """Namespace-prefixed imports like retailerhub_BasketController resolve
        to BasketController.cls when the unprefixed file exists."""
        from roam.index.relations import _resolve_salesforce_import

        candidates = [
            {"file_path": "force-app/main/default/classes/BasketController.cls", "name": "getBasketItems"},
            {"file_path": "force-app/main/default/classes/OrderController.cls", "name": "getOrders"},
        ]
        matches = _resolve_salesforce_import(
            "@salesforce/apex/retailerhub_BasketController.getBasketItems",
            candidates,
        )
        assert len(matches) == 1
        assert matches[0]["name"] == "getBasketItems"

    def test_lwc_apex_import_edge_resolution(self):
        """LWC @salesforce/apex imports should resolve to Apex method symbols
        even when target_name is the compound ClassName.methodName."""
        from roam.index.relations import resolve_references

        # Simulate: Apex class with method
        apex_class_sym = {
            "id": 1, "file_id": 10, "file_path": "classes/AccountController.cls",
            "name": "AccountController", "qualified_name": "AccountController",
            "kind": "class", "is_exported": True, "line_start": 1,
        }
        apex_method_sym = {
            "id": 2, "file_id": 10, "file_path": "classes/AccountController.cls",
            "name": "getAccounts", "qualified_name": "AccountController.getAccounts",
            "kind": "method", "is_exported": True, "line_start": 3,
        }
        lwc_class_sym = {
            "id": 3, "file_id": 20, "file_path": "lwc/accountList/accountList.js",
            "name": "AccountList", "qualified_name": "AccountList",
            "kind": "class", "is_exported": True, "line_start": 4,
        }

        symbols_by_name = {
            "AccountController": [apex_class_sym],
            "getAccounts": [apex_method_sym],
            "AccountList": [lwc_class_sym],
        }
        files_by_path = {
            "classes/AccountController.cls": 10,
            "lwc/accountList/accountList.js": 20,
        }

        # LWC import reference (now "call" kind for apex imports)
        references = [{
            "source_name": None,
            "target_name": "AccountController.getAccounts",
            "kind": "call",
            "line": 2,
            "import_path": "@salesforce/apex/AccountController.getAccounts",
            "source_file": "lwc/accountList/accountList.js",
        }]

        edges = resolve_references(references, symbols_by_name, files_by_path)
        assert len(edges) == 1
        assert edges[0]["source_id"] == 3  # AccountList
        assert edges[0]["target_id"] == 2  # getAccounts method

    def test_lwc_apex_import_with_namespace_prefix(self):
        """LWC import with namespace prefix (e.g. retailerhub_BasketController)
        should resolve to the unprefixed Apex class file."""
        from roam.index.relations import resolve_references

        apex_method_sym = {
            "id": 1, "file_id": 10, "file_path": "classes/BasketController.cls",
            "name": "getBasketItems", "qualified_name": "BasketController.getBasketItems",
            "kind": "method", "is_exported": True, "line_start": 3,
        }
        lwc_class_sym = {
            "id": 2, "file_id": 20, "file_path": "lwc/basketView/basketView.js",
            "name": "BasketView", "qualified_name": "BasketView",
            "kind": "class", "is_exported": True, "line_start": 4,
        }

        symbols_by_name = {
            "getBasketItems": [apex_method_sym],
            "BasketView": [lwc_class_sym],
        }
        files_by_path = {
            "classes/BasketController.cls": 10,
            "lwc/basketView/basketView.js": 20,
        }

        references = [{
            "source_name": None,
            "target_name": "retailerhub_BasketController.getBasketItems",
            "kind": "call",
            "line": 2,
            "import_path": "@salesforce/apex/retailerhub_BasketController.getBasketItems",
            "source_file": "lwc/basketView/basketView.js",
        }]

        edges = resolve_references(references, symbols_by_name, files_by_path)
        assert len(edges) == 1
        assert edges[0]["target_id"] == 1  # getBasketItems method


# ============================================================================
# Phase 2: Expanded XML reference extraction tests
# ============================================================================


class TestSfXmlExpandedRefs:
    """Test expanded XML metadata reference extraction."""

    def test_formula_field_references(self, sfxml_extractor, xml_parser):
        tree, source = _parse_xml(xml_parser, """<?xml version="1.0" encoding="UTF-8"?>
<CustomObject xmlns="http://soap.sforce.com/2006/04/metadata">
    <validationRules>
        <fullName>Check_Revenue</fullName>
        <active>true</active>
        <errorConditionFormula>Account.Revenue__c &lt; 0</errorConditionFormula>
    </validationRules>
</CustomObject>
""")
        refs = sfxml_extractor.extract_references(
            tree, source, "objects/Account/Account.object-meta.xml"
        )
        ref_targets = {r["target_name"] for r in refs}
        assert "Revenue__c" in ref_targets

    def test_context_aware_field_permission_refs(self, sfxml_extractor, xml_parser):
        tree, source = _parse_xml(xml_parser, """<?xml version="1.0" encoding="UTF-8"?>
<Profile xmlns="http://soap.sforce.com/2006/04/metadata">
    <fieldPermissions>
        <field>Account.Industry__c</field>
        <readable>true</readable>
    </fieldPermissions>
</Profile>
""")
        refs = sfxml_extractor.extract_references(
            tree, source, "profiles/Admin.profile-meta.xml"
        )
        ref_targets = {r["target_name"] for r in refs}
        assert "Industry__c" in ref_targets

    def test_context_aware_class_access_refs(self, sfxml_extractor, xml_parser):
        tree, source = _parse_xml(xml_parser, """<?xml version="1.0" encoding="UTF-8"?>
<Profile xmlns="http://soap.sforce.com/2006/04/metadata">
    <classAccesses>
        <apexClass>AccountHandler</apexClass>
        <enabled>true</enabled>
    </classAccesses>
</Profile>
""")
        refs = sfxml_extractor.extract_references(
            tree, source, "profiles/Admin.profile-meta.xml"
        )
        ref_targets = {r["target_name"] for r in refs}
        assert "AccountHandler" in ref_targets

    def test_flow_action_references(self, sfxml_extractor, xml_parser):
        tree, source = _parse_xml(xml_parser, """<?xml version="1.0" encoding="UTF-8"?>
<Flow xmlns="http://soap.sforce.com/2006/04/metadata">
    <actionCalls>
        <actionName>AccountHandler</actionName>
        <actionType>apex</actionType>
    </actionCalls>
</Flow>
""")
        refs = sfxml_extractor.extract_references(
            tree, source, "flows/Account_Flow.flow-meta.xml"
        )
        ref_targets = {r["target_name"] for r in refs}
        assert "AccountHandler" in ref_targets

    def test_reference_to_sobject(self, sfxml_extractor, xml_parser):
        tree, source = _parse_xml(xml_parser, """<?xml version="1.0" encoding="UTF-8"?>
<CustomObject xmlns="http://soap.sforce.com/2006/04/metadata">
    <fields>
        <fullName>ParentAccount__c</fullName>
        <type>Lookup</type>
        <referenceTo>Account</referenceTo>
        <relationshipName>ChildAccounts</relationshipName>
    </fields>
</CustomObject>
""")
        refs = sfxml_extractor.extract_references(
            tree, source, "objects/Child__c/Child__c.object-meta.xml"
        )
        ref_targets = {r["target_name"] for r in refs}
        assert "Account" in ref_targets
        assert "ChildAccounts" in ref_targets


# ============================================================================
# Phase 3: Aura component extraction tests
# ============================================================================


@pytest.fixture
def aura_extractor():
    from roam.languages.aura_lang import AuraExtractor
    return AuraExtractor()


class TestAuraComponentExtraction:
    """Test Aura Lightning component extraction."""

    def test_basic_component(self, aura_extractor, xml_parser):
        tree, source = _parse_xml(xml_parser, """<aura:component controller="AccountController" implements="force:appHostable">
    <aura:attribute name="accounts" type="List" description="List of accounts"/>
    <aura:attribute name="searchKey" type="String" default=""/>
    <aura:handler name="init" value="{!this}" action="{!c.doInit}"/>
    <aura:method name="refresh" description="Refreshes the data"/>
</aura:component>
""")
        symbols = aura_extractor.extract_symbols(tree, source, "AccountList.cmp")
        names = [s["name"] for s in symbols]

        assert "AccountList" in names
        assert "accounts" in names
        assert "searchKey" in names
        assert "init" in names
        assert "refresh" in names

        comp = next(s for s in symbols if s["name"] == "AccountList")
        assert comp["kind"] == "class"

        attr = next(s for s in symbols if s["name"] == "accounts")
        assert attr["kind"] == "field"
        assert "List" in attr["signature"]
        assert attr["docstring"] == "List of accounts"

        method = next(s for s in symbols if s["name"] == "refresh")
        assert method["kind"] == "method"

    def test_component_references(self, aura_extractor, xml_parser):
        tree, source = _parse_xml(xml_parser, """<aura:component controller="AccountController" extends="c:BaseComponent" implements="force:appHostable,flexipage:availableForAllPageTypes">
    <aura:handler event="c:AccountUpdated" action="{!c.handleUpdate}"/>
    <aura:registerEvent name="notify" type="c:NotifyEvent"/>
    <c:ChildComponent aura:id="child"/>
</aura:component>
""")
        refs = aura_extractor.extract_references(tree, source, "AccountList.cmp")
        targets = {r["target_name"] for r in refs}

        assert "AccountController" in targets  # controller
        assert "BaseComponent" in targets       # extends
        assert "AccountUpdated" in targets      # event handler
        assert "NotifyEvent" in targets         # registered event
        assert "ChildComponent" in targets      # child component usage

        # Check implements references
        impl_refs = [r for r in refs if r["kind"] == "implements"]
        impl_targets = {r["target_name"] for r in impl_refs}
        assert "force:appHostable" in impl_targets

    def test_aura_application(self, aura_extractor, xml_parser):
        tree, source = _parse_xml(xml_parser, """<aura:application extends="force:slds">
    <c:AccountList/>
</aura:application>
""")
        symbols = aura_extractor.extract_symbols(tree, source, "MyApp.app")
        assert any(s["name"] == "MyApp" and s["kind"] == "class" for s in symbols)

    def test_aura_event(self, aura_extractor, xml_parser):
        tree, source = _parse_xml(xml_parser, """<aura:event type="COMPONENT">
    <aura:attribute name="accountId" type="String"/>
</aura:event>
""")
        symbols = aura_extractor.extract_symbols(tree, source, "AccountUpdated.evt")
        assert any(s["name"] == "AccountUpdated" and s["kind"] == "class" for s in symbols)
        assert any(s["name"] == "accountId" and s["kind"] == "field" for s in symbols)

    def test_aura_interface(self, aura_extractor, xml_parser):
        tree, source = _parse_xml(xml_parser, """<aura:interface>
    <aura:attribute name="title" type="String" required="true"/>
</aura:interface>
""")
        symbols = aura_extractor.extract_symbols(tree, source, "Displayable.intf")
        iface = next(s for s in symbols if s["name"] == "Displayable")
        assert iface["kind"] == "interface"


# ============================================================================
# Phase 3: Visualforce extraction tests
# ============================================================================


@pytest.fixture
def vf_extractor():
    from roam.languages.visualforce_lang import VisualforceExtractor
    return VisualforceExtractor()


class TestVisualforceExtraction:
    """Test Visualforce page and component extraction."""

    def test_vf_page_with_controller(self, vf_extractor, xml_parser):
        tree, source = _parse_xml(xml_parser, """<apex:page controller="AccountController" extensions="ExtensionA,ExtensionB">
    <apex:form>
        <apex:inputField value="{!account.Name}"/>
    </apex:form>
</apex:page>
""")
        symbols = vf_extractor.extract_symbols(tree, source, "AccountPage.page")
        page = next(s for s in symbols if s["name"] == "AccountPage")
        assert page["kind"] == "class"
        assert "controller=AccountController" in page["signature"]

        refs = vf_extractor.extract_references(tree, source, "AccountPage.page")
        targets = {r["target_name"] for r in refs}
        assert "AccountController" in targets
        assert "ExtensionA" in targets
        assert "ExtensionB" in targets

    def test_vf_component_refs(self, vf_extractor, xml_parser):
        tree, source = _parse_xml(xml_parser, """<apex:page>
    <apex:include pageName="SharedHeader"/>
    <c:CustomWidget title="Test"/>
</apex:page>
""")
        refs = vf_extractor.extract_references(tree, source, "TestPage.page")
        targets = {r["target_name"] for r in refs}
        assert "SharedHeader" in targets
        assert "CustomWidget" in targets

    def test_vf_component(self, vf_extractor, xml_parser):
        tree, source = _parse_xml(xml_parser, """<apex:component controller="WidgetController">
    <apex:attribute name="title" type="String" description="Widget title"/>
</apex:component>
""")
        symbols = vf_extractor.extract_symbols(tree, source, "CustomWidget.component")
        assert any(s["name"] == "CustomWidget" for s in symbols)

        refs = vf_extractor.extract_references(tree, source, "CustomWidget.component")
        targets = {r["target_name"] for r in refs}
        assert "WidgetController" in targets


# ============================================================================
# Phase 4: Language detection and path heuristic tests
# ============================================================================


class TestSalesforcePathDetection:
    """Test Salesforce path heuristics and extensionless metadata."""

    def test_aura_extension_detection(self):
        from roam.languages.registry import get_language_for_file
        assert get_language_for_file("MyComponent.cmp") == "aura"
        assert get_language_for_file("MyApp.app") == "aura"
        assert get_language_for_file("MyEvent.evt") == "aura"
        assert get_language_for_file("MyInterface.intf") == "aura"
        assert get_language_for_file("MyDesign.design") == "aura"

    def test_visualforce_extension_detection(self):
        from roam.languages.registry import get_language_for_file
        assert get_language_for_file("AccountPage.page") == "visualforce"
        assert get_language_for_file("CustomWidget.component") == "visualforce"

    def test_extensionless_sf_metadata(self):
        from roam.languages.registry import get_language_for_file
        assert get_language_for_file("CustomLabels.labels") == "sfxml"
        assert get_language_for_file("Account.workflow") == "sfxml"
        assert get_language_for_file("Account.object") == "sfxml"

    def test_xml_in_force_app_detected_as_sfxml(self):
        from roam.index.parser import detect_language
        assert detect_language("force-app/main/default/package.xml") == "sfxml"

    def test_xml_outside_sf_stays_xml(self):
        from roam.index.parser import detect_language
        assert detect_language("config/settings.xml") == "xml"

    def test_parser_aura_detection(self):
        from roam.index.parser import detect_language
        assert detect_language("aura/MyComponent/MyComponent.cmp") == "aura"

    def test_parser_vf_detection(self):
        from roam.index.parser import detect_language
        assert detect_language("pages/AccountPage.page") == "visualforce"

    def test_extractor_factory_aura(self):
        from roam.languages.registry import get_extractor
        from roam.languages.aura_lang import AuraExtractor
        assert isinstance(get_extractor("aura"), AuraExtractor)

    def test_extractor_factory_visualforce(self):
        from roam.languages.registry import get_extractor
        from roam.languages.visualforce_lang import VisualforceExtractor
        assert isinstance(get_extractor("visualforce"), VisualforceExtractor)


# ============================================================================
# Extended E2E: Full Salesforce project with Aura + Visualforce
# ============================================================================


@pytest.fixture(scope="module")
def full_sf_project(tmp_path_factory):
    """Create a full SF DX project with Apex + LWC + Aura + Visualforce."""
    proj = tmp_path_factory.mktemp("full_sf_project")

    # Apex class
    classes_dir = proj / "force-app" / "main" / "default" / "classes"
    classes_dir.mkdir(parents=True)
    (classes_dir / "AccountController.cls").write_text(
        'public with sharing class AccountController {\n'
        '    @AuraEnabled\n'
        '    public static List<Account> getAccounts() {\n'
        '        return [SELECT Id, Name FROM Account];\n'
        '    }\n'
        '}\n'
    )
    (classes_dir / "AccountController.cls-meta.xml").write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<ApexClass xmlns="http://soap.sforce.com/2006/04/metadata">\n'
        '    <apiVersion>58.0</apiVersion>\n'
        '    <status>Active</status>\n'
        '</ApexClass>\n'
    )

    # LWC that imports from Apex
    lwc_dir = proj / "force-app" / "main" / "default" / "lwc" / "accountList"
    lwc_dir.mkdir(parents=True)
    (lwc_dir / "accountList.js").write_text(
        "import { LightningElement, wire } from 'lwc';\n"
        "import getAccounts from '@salesforce/apex/AccountController.getAccounts';\n"
        "import ACCOUNT_NAME from '@salesforce/schema/Account.Name';\n"
        "\n"
        "export default class AccountList extends LightningElement {\n"
        "    accounts;\n"
        "    @wire(getAccounts)\n"
        "    wiredAccounts({ data }) {\n"
        "        if (data) this.accounts = data;\n"
        "    }\n"
        "}\n"
    )
    (lwc_dir / "accountList.js-meta.xml").write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<LightningComponentBundle xmlns="http://soap.sforce.com/2006/04/metadata">\n'
        '    <apiVersion>58.0</apiVersion>\n'
        '    <isExposed>true</isExposed>\n'
        '</LightningComponentBundle>\n'
    )

    # Aura component
    aura_dir = proj / "force-app" / "main" / "default" / "aura" / "AccountCard"
    aura_dir.mkdir(parents=True)
    (aura_dir / "AccountCard.cmp").write_text(
        '<aura:component controller="AccountController" implements="force:appHostable">\n'
        '    <aura:attribute name="accountId" type="String"/>\n'
        '    <aura:handler name="init" value="{!this}" action="{!c.doInit}"/>\n'
        '    <c:accountList/>\n'
        '</aura:component>\n'
    )

    # Visualforce page
    pages_dir = proj / "force-app" / "main" / "default" / "pages"
    pages_dir.mkdir(parents=True)
    (pages_dir / "AccountPage.page").write_text(
        '<apex:page controller="AccountController">\n'
        '    <apex:form>\n'
        '        <apex:pageBlock title="Accounts">\n'
        '        </apex:pageBlock>\n'
        '    </apex:form>\n'
        '</apex:page>\n'
    )
    (pages_dir / "AccountPage.page-meta.xml").write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<ApexPage xmlns="http://soap.sforce.com/2006/04/metadata">\n'
        '    <apiVersion>58.0</apiVersion>\n'
        '</ApexPage>\n'
    )

    # Custom labels
    labels_dir = proj / "force-app" / "main" / "default" / "labels"
    labels_dir.mkdir(parents=True)
    (labels_dir / "CustomLabels.labels-meta.xml").write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<CustomLabels xmlns="http://soap.sforce.com/2006/04/metadata">\n'
        '    <labels>\n'
        '        <fullName>Greeting</fullName>\n'
        '        <language>en_US</language>\n'
        '        <value>Hello</value>\n'
        '    </labels>\n'
        '</CustomLabels>\n'
    )

    git_init(proj)
    out, rc = roam("index", cwd=str(proj))
    assert rc == 0, f"Index failed: {out}"
    return proj


class TestFullSalesforceE2E:
    """E2E tests for a full Salesforce project with all file types."""

    def test_index_succeeds(self, full_sf_project):
        out, rc = roam("index", cwd=str(full_sf_project))
        assert rc == 0

    def test_map_shows_all_languages(self, full_sf_project):
        out, rc = roam("map", cwd=str(full_sf_project))
        assert rc == 0
        assert "apex" in out
        assert "javascript" in out
        assert "aura" in out

    def test_apex_class_symbol(self, full_sf_project):
        out, rc = roam("symbol", "AccountController", cwd=str(full_sf_project))
        assert rc == 0
        assert "AccountController" in out

    def test_aura_component_symbol(self, full_sf_project):
        out, rc = roam("symbol", "AccountCard", cwd=str(full_sf_project))
        assert rc == 0
        assert "AccountCard" in out

    def test_visualforce_page_symbol(self, full_sf_project):
        out, rc = roam("symbol", "AccountPage", cwd=str(full_sf_project))
        assert rc == 0
        assert "AccountPage" in out

    def test_aura_references_apex_controller(self, full_sf_project):
        """Aura component referencing controller should create a dep edge."""
        out, rc = roam("deps",
                       "force-app/main/default/aura/AccountCard/AccountCard.cmp",
                       cwd=str(full_sf_project))
        assert rc == 0
        # The Aura component references AccountController — should appear as a dep
        assert "AccountController" in out

    def test_lwc_references_apex_controller(self, full_sf_project):
        """LWC @salesforce/apex import should create a dep edge to Apex class."""
        out, rc = roam("deps",
                       "force-app/main/default/lwc/accountList/accountList.js",
                       cwd=str(full_sf_project))
        assert rc == 0
        assert "AccountController" in out


# ============================================================================
# Edge-case and negative tests
# ============================================================================


class TestApexEdgeCases:
    """Test Apex extractor handles edge cases gracefully."""

    def test_empty_class(self, apex_extractor, apex_parser):
        """An empty class should still produce a class symbol."""
        tree, source = _parse_apex(apex_parser, "public class Empty {}")
        symbols = apex_extractor.extract_symbols(tree, source, "Empty.cls")
        assert any(s["name"] == "Empty" and s["kind"] == "class" for s in symbols)
        # No methods or fields
        assert all(s["kind"] == "class" for s in symbols)

    def test_global_visibility(self, apex_extractor, apex_parser):
        """global keyword should map to public visibility."""
        tree, source = _parse_apex(apex_parser, """
global class ApiEndpoint {
    global static void doPost() {}
}
""")
        symbols = apex_extractor.extract_symbols(tree, source, "ApiEndpoint.cls")
        cls = next(s for s in symbols if s["name"] == "ApiEndpoint")
        assert cls["visibility"] == "public"
        assert cls["is_exported"] is True

    def test_without_sharing(self, apex_extractor, apex_parser):
        tree, source = _parse_apex(apex_parser, """
public without sharing class Insecure {
    public void doWork() {}
}
""")
        symbols = apex_extractor.extract_symbols(tree, source, "Insecure.cls")
        cls = next(s for s in symbols if s["name"] == "Insecure")
        assert "without sharing" in cls["signature"]

    def test_multiple_methods_distinct(self, apex_extractor, apex_parser):
        """All method names should appear with correct parent_name."""
        tree, source = _parse_apex(apex_parser, """
public class Multi {
    public void a() {}
    private String b() { return ''; }
    public static Integer c(Integer x) { return x; }
}
""")
        symbols = apex_extractor.extract_symbols(tree, source, "Multi.cls")
        methods = [s for s in symbols if s["kind"] == "method"]
        assert len(methods) == 3
        assert {m["name"] for m in methods} == {"a", "b", "c"}
        for m in methods:
            assert m["parent_name"] == "Multi"

    def test_trigger_no_body(self, apex_extractor, apex_parser):
        """A trigger with an empty body should still produce a trigger symbol."""
        tree, source = _parse_apex(apex_parser, """
trigger EmptyTrigger on Contact (before insert) {
}
""")
        symbols = apex_extractor.extract_symbols(tree, source, "EmptyTrigger.trigger")
        assert any(s["name"] == "EmptyTrigger" and s["kind"] == "trigger" for s in symbols)


class TestSfXmlEdgeCases:
    """Test XML extractor handles edge cases."""

    def test_empty_custom_object(self, sfxml_extractor, xml_parser):
        """A CustomObject with no fields should still produce a root symbol."""
        tree, source = _parse_xml(xml_parser, """<?xml version="1.0" encoding="UTF-8"?>
<CustomObject xmlns="http://soap.sforce.com/2006/04/metadata">
</CustomObject>
""")
        symbols = sfxml_extractor.extract_symbols(
            tree, source, "objects/Bare__c/Bare__c.object-meta.xml"
        )
        assert any(s["name"] == "Bare__c" and s["kind"] == "class" for s in symbols)

    def test_formula_with_multiple_refs(self, sfxml_extractor, xml_parser):
        """Formula with multiple Object.Field__c patterns extracts all."""
        tree, source = _parse_xml(xml_parser, """<?xml version="1.0" encoding="UTF-8"?>
<CustomObject xmlns="http://soap.sforce.com/2006/04/metadata">
    <validationRules>
        <fullName>Budget_Check</fullName>
        <active>true</active>
        <errorConditionFormula>Account.Revenue__c &gt; Contact.Budget__c</errorConditionFormula>
    </validationRules>
</CustomObject>
""")
        refs = sfxml_extractor.extract_references(
            tree, source, "objects/Opportunity/Opportunity.object-meta.xml"
        )
        ref_targets = {r["target_name"] for r in refs}
        assert "Revenue__c" in ref_targets
        assert "Budget__c" in ref_targets

    def test_non_ref_field_not_extracted(self, sfxml_extractor, xml_parser):
        """<field> outside a known parent context is not treated as a reference."""
        tree, source = _parse_xml(xml_parser, """<?xml version="1.0" encoding="UTF-8"?>
<CustomObject xmlns="http://soap.sforce.com/2006/04/metadata">
    <fields>
        <fullName>SomeField__c</fullName>
        <type>Text</type>
        <field>this_is_not_a_ref</field>
    </fields>
</CustomObject>
""")
        refs = sfxml_extractor.extract_references(
            tree, source, "objects/Account/Account.object-meta.xml"
        )
        ref_targets = {r["target_name"] for r in refs}
        # <field> inside <fields> is not in _CONTEXT_REF_PARENTS for "fields"
        assert "this_is_not_a_ref" not in ref_targets


class TestAuraEdgeCases:
    """Test Aura extractor handles edge cases."""

    def test_component_with_no_attributes(self, xml_parser):
        """An Aura component with just the root tag and no attributes/members."""
        from roam.languages.aura_lang import AuraExtractor
        ext = AuraExtractor()
        tree, source = _parse_xml(xml_parser, """<aura:component>
</aura:component>
""")
        symbols = ext.extract_symbols(tree, source, "Minimal.cmp")
        assert len(symbols) == 1
        assert symbols[0]["name"] == "Minimal"
        assert symbols[0]["kind"] == "class"

    def test_lowercase_custom_component_ignored(self, xml_parser):
        """<c:lowerCase> should not produce a reference (only PascalCase)."""
        from roam.languages.aura_lang import AuraExtractor
        ext = AuraExtractor()
        tree, source = _parse_xml(xml_parser, """<aura:component>
    <c:lowerCase attr="val"/>
</aura:component>
""")
        refs = ext.extract_references(tree, source, "Test.cmp")
        comp_refs = [r for r in refs if r["target_name"] == "lowerCase"]
        assert len(comp_refs) == 0

    def test_controller_case_insensitive(self, xml_parser):
        """Controller= (capital C) should be resolved the same as controller=."""
        from roam.languages.aura_lang import AuraExtractor
        ext = AuraExtractor()
        tree, source = _parse_xml(xml_parser, """<aura:component Controller="BasketController">
    <aura:attribute name="items" type="List"/>
</aura:component>
""")
        refs = ext.extract_references(tree, source, "BasketItem.cmp")
        targets = {r["target_name"] for r in refs}
        assert "BasketController" in targets


class TestVisualforceEdgeCases:
    """Test Visualforce extractor edge cases."""

    def test_vf_page_controller_in_signature(self, vf_extractor, xml_parser):
        """Controller and extensions should appear in the page signature."""
        tree, source = _parse_xml(xml_parser, """<apex:page controller="MyCtrl" extensions="ExtA">
</apex:page>
""")
        symbols = vf_extractor.extract_symbols(tree, source, "MyPage.page")
        page = next(s for s in symbols if s["name"] == "MyPage")
        assert "controller=MyCtrl" in page["signature"]
        assert "extensions=ExtA" in page["signature"]

    def test_vf_page_no_controller(self, vf_extractor, xml_parser):
        """A VF page with no controller still produces a symbol."""
        tree, source = _parse_xml(xml_parser, """<apex:page>
    <h1>Hello</h1>
</apex:page>
""")
        symbols = vf_extractor.extract_symbols(tree, source, "SimplePage.page")
        assert any(s["name"] == "SimplePage" for s in symbols)
        refs = vf_extractor.extract_references(tree, source, "SimplePage.page")
        # No controller means no controller reference
        assert len([r for r in refs if r["target_name"] == "SimplePage"]) == 0


class TestPathHeuristicEdgeCases:
    """Additional path detection edge cases."""

    def test_src_dir_xml_is_sfxml(self):
        """src/ is in the SF heuristic dirs, so .xml under it → sfxml."""
        from roam.index.parser import detect_language
        assert detect_language("src/objects/Account.xml") == "sfxml"

    def test_non_sf_xml_with_different_parent(self):
        """Plain .xml outside all SF dirs stays as xml."""
        from roam.index.parser import detect_language
        assert detect_language("app/templates/layout.xml") == "xml"

    def test_meta_xml_case_insensitive(self):
        """Verify -meta.xml detection is case-insensitive on the suffix."""
        from roam.languages.registry import get_language_for_file
        assert get_language_for_file("MyClass.cls-META.XML") == "sfxml"

    def test_double_ext_meta_xml(self):
        """Compound extension like .field-meta.xml still detected as sfxml."""
        from roam.languages.registry import get_language_for_file
        assert get_language_for_file("Industry__c.field-meta.xml") == "sfxml"


# ============================================================================
# Phase 3: Gap-closure tests
# ============================================================================


class TestApexSoqlReferences:
    """Test SOQL/SOSL reference extraction from Apex code."""

    def test_soql_from_clause_sobject(self, apex_extractor, apex_parser):
        """SOQL FROM clause should extract SObject reference."""
        tree, source = _parse_apex(apex_parser, """
public class AccountService {
    public List<Account> getAccounts() {
        return [SELECT Id, Name FROM Account WHERE IsActive = true];
    }
}
""")
        apex_extractor.extract_symbols(tree, source, "AccountService.cls")
        refs = apex_extractor.extract_references(tree, source, "AccountService.cls")
        targets = {r["target_name"] for r in refs}
        assert "Account" in targets

    def test_soql_relationship_field(self, apex_extractor, apex_parser):
        """SOQL relationship traversal should extract field references."""
        tree, source = _parse_apex(apex_parser, """
public class ContactService {
    public void query() {
        List<Contact> c = [SELECT Account.Name FROM Contact];
    }
}
""")
        apex_extractor.extract_symbols(tree, source, "ContactService.cls")
        refs = apex_extractor.extract_references(tree, source, "ContactService.cls")
        targets = {r["target_name"] for r in refs}
        assert "Contact" in targets
        assert "Name" in targets


class TestApexSystemLabel:
    """Test System.Label.X custom label references in Apex."""

    def test_system_label_reference(self, apex_extractor, apex_parser):
        """System.Label.MyLabel should be extracted as a reference."""
        tree, source = _parse_apex(apex_parser, """
public class LabelUser {
    public String getLabel() {
        return System.Label.Welcome_Message;
    }
}
""")
        apex_extractor.extract_symbols(tree, source, "LabelUser.cls")
        refs = apex_extractor.extract_references(tree, source, "LabelUser.cls")
        targets = {r["target_name"] for r in refs}
        assert "Welcome_Message" in targets


class TestApexTypeReferences:
    """Test generic type parameter extraction from Apex declarations."""

    def test_list_type_parameter(self, apex_extractor, apex_parser):
        """List<Account> should extract Account as a type reference."""
        tree, source = _parse_apex(apex_parser, """
public class AccountService {
    public List<Account> accounts;
}
""")
        apex_extractor.extract_symbols(tree, source, "AccountService.cls")
        refs = apex_extractor.extract_references(tree, source, "AccountService.cls")
        ref_targets = {r["target_name"] for r in refs}
        assert "Account" in ref_targets

    def test_map_type_parameters(self, apex_extractor, apex_parser):
        """Map<String, Contact> should extract Contact but skip String (builtin)."""
        tree, source = _parse_apex(apex_parser, """
public class ContactService {
    public Map<String, Contact> contactMap;
}
""")
        apex_extractor.extract_symbols(tree, source, "ContactService.cls")
        refs = apex_extractor.extract_references(tree, source, "ContactService.cls")
        ref_targets = {r["target_name"] for r in refs}
        assert "Contact" in ref_targets
        assert "String" not in ref_targets

    def test_method_return_type_reference(self, apex_extractor, apex_parser):
        """Method return type should extract type reference."""
        tree, source = _parse_apex(apex_parser, """
public class OrderService {
    public List<Order__c> getOrders() { return null; }
}
""")
        apex_extractor.extract_symbols(tree, source, "OrderService.cls")
        refs = apex_extractor.extract_references(tree, source, "OrderService.cls")
        ref_targets = {r["target_name"] for r in refs}
        assert "Order__c" in ref_targets


class TestLmsChannelImport:
    """Test Lightning Message Service channel import resolution."""

    def test_lms_channel_import_target(self):
        """@salesforce/messageChannel should be resolved to the channel name."""
        from roam.languages.javascript_lang import JavaScriptExtractor
        ext = JavaScriptExtractor()
        result = ext._resolve_salesforce_import_target(
            "@salesforce/messageChannel/Record_Selected__c"
        )
        assert result == "Record_Selected__c"

    def test_lms_import_resolution(self):
        """LMS channel import should resolve to matching symbol."""
        from roam.index.relations import _resolve_salesforce_import
        candidates = [
            {"name": "Record_Selected__c", "file_path": "messageChannels/Record_Selected__c.messageChannel-meta.xml"},
            {"name": "Other__c", "file_path": "messageChannels/Other__c.messageChannel-meta.xml"},
        ]
        matches = _resolve_salesforce_import(
            "@salesforce/messageChannel/Record_Selected__c",
            candidates,
        )
        assert len(matches) == 1
        assert matches[0]["name"] == "Record_Selected__c"


class TestSfXmlExpandedCoverage:
    """Test expanded XML metadata reference extraction for permission sets, flows, etc."""

    def test_flow_object_type_reference(self, sfxml_extractor, xml_parser):
        """Flow objectType tag should be extracted as a reference."""
        tree, source = _parse_xml(xml_parser, """<?xml version="1.0" encoding="UTF-8"?>
<Flow xmlns="http://soap.sforce.com/2006/04/metadata">
    <recordLookups>
        <name>Get_Accounts</name>
        <objectType>Account</objectType>
    </recordLookups>
</Flow>
""")
        refs = sfxml_extractor.extract_references(tree, source, "MyFlow.flow-meta.xml")
        targets = {r["target_name"] for r in refs}
        assert "Account" in targets

    def test_flow_record_type_reference(self, sfxml_extractor, xml_parser):
        """Flow recordType tag should be extracted as a reference."""
        tree, source = _parse_xml(xml_parser, """<?xml version="1.0" encoding="UTF-8"?>
<Flow xmlns="http://soap.sforce.com/2006/04/metadata">
    <recordCreates>
        <name>Create_Case</name>
        <recordType>Support_Case</recordType>
    </recordCreates>
</Flow>
""")
        refs = sfxml_extractor.extract_references(tree, source, "MyFlow.flow-meta.xml")
        targets = {r["target_name"] for r in refs}
        assert "Support_Case" in targets

    def test_flow_input_output_references(self, sfxml_extractor, xml_parser):
        """Flow inputReference and outputReference should be extracted."""
        tree, source = _parse_xml(xml_parser, """<?xml version="1.0" encoding="UTF-8"?>
<Flow xmlns="http://soap.sforce.com/2006/04/metadata">
    <assignments>
        <name>Set_Values</name>
        <assignmentItems>
            <inputReference>varAccountId</inputReference>
            <outputReference>recordId</outputReference>
        </assignmentItems>
    </assignments>
</Flow>
""")
        refs = sfxml_extractor.extract_references(tree, source, "MyFlow.flow-meta.xml")
        targets = {r["target_name"] for r in refs}
        assert "varAccountId" in targets
        assert "recordId" in targets

    def test_permission_set_tab_visibility(self, sfxml_extractor, xml_parser):
        """Permission set tabVisibilities should extract tab reference."""
        tree, source = _parse_xml(xml_parser, """<?xml version="1.0" encoding="UTF-8"?>
<PermissionSet xmlns="http://soap.sforce.com/2006/04/metadata">
    <label>My Perm Set</label>
    <tabVisibilities>
        <tab>standard-Account</tab>
        <visibility>Visible</visibility>
    </tabVisibilities>
</PermissionSet>
""")
        refs = sfxml_extractor.extract_references(tree, source, "MyPermSet.permissionset-meta.xml")
        targets = {r["target_name"] for r in refs}
        assert "standard-Account" in targets

    def test_named_credential_reference(self, sfxml_extractor, xml_parser):
        """Named credential tag should be extracted as a reference."""
        tree, source = _parse_xml(xml_parser, """<?xml version="1.0" encoding="UTF-8"?>
<Flow xmlns="http://soap.sforce.com/2006/04/metadata">
    <actionCalls>
        <name>Call_External</name>
        <namedCredential>My_API_Credential</namedCredential>
    </actionCalls>
</Flow>
""")
        refs = sfxml_extractor.extract_references(tree, source, "MyFlow.flow-meta.xml")
        targets = {r["target_name"] for r in refs}
        assert "My_API_Credential" in targets


class TestVisualforceMergeFields:
    """Test Visualforce merge field expression extraction."""

    def test_vf_label_reference(self, vf_extractor, xml_parser):
        """VF {!$Label.MyLabel} should extract the label name."""
        tree, source = _parse_xml(xml_parser, """<apex:page>
    <apex:outputText value="{!$Label.Welcome_Message}"/>
</apex:page>
""")
        refs = vf_extractor.extract_references(tree, source, "MyPage.page")
        targets = {r["target_name"] for r in refs}
        assert "Welcome_Message" in targets

    def test_vf_custom_setting_reference(self, vf_extractor, xml_parser):
        """VF {!$Setup.MySetting__c.Field} should extract the custom setting."""
        tree, source = _parse_xml(xml_parser, """<apex:page>
    <apex:outputText value="{!$Setup.AppConfig__c.Endpoint}"/>
</apex:page>
""")
        refs = vf_extractor.extract_references(tree, source, "MyPage.page")
        targets = {r["target_name"] for r in refs}
        assert "AppConfig__c" in targets


class TestAuraLabelAndDataService:
    """Test Aura $Label and force:recordData references."""

    def test_aura_label_reference(self, xml_parser):
        """$Label.c.MyLabel in Aura attribute value should be extracted."""
        from roam.languages.aura_lang import AuraExtractor
        ext = AuraExtractor()
        tree, source = _parse_xml(xml_parser, """<aura:component>
    <lightning:button label="{!$Label.c.Save_Button}"/>
</aura:component>
""")
        refs = ext.extract_references(tree, source, "MyComp.cmp")
        targets = {r["target_name"] for r in refs}
        assert "Save_Button" in targets

    def test_force_record_data(self, xml_parser):
        """force:recordData with sObjectType should extract SObject reference."""
        from roam.languages.aura_lang import AuraExtractor
        ext = AuraExtractor()
        tree, source = _parse_xml(xml_parser, """<aura:component>
    <force:recordData aura:id="record" sObjectType="Account" fields="Name,Industry"/>
</aura:component>
""")
        refs = ext.extract_references(tree, source, "RecordView.cmp")
        targets = {r["target_name"] for r in refs}
        assert "Account" in targets


# ============================================================================
# Priority 1: Missing cross-language edge tests
# ============================================================================


class TestP1A_LwcApexCallEdges:
    """P1A: LWC @salesforce/apex imports should create 'call' edges
    to both the Apex method and the Apex class."""

    def test_apex_import_creates_call_edges(self):
        """@salesforce/apex import should create 'call' (not 'import') edges."""
        from tree_sitter_language_pack import get_parser
        from roam.languages.javascript_lang import JavaScriptExtractor

        parser = get_parser("javascript")
        ext = JavaScriptExtractor()
        code = b"import uploadImage from '@salesforce/apex/CloudinaryService.uploadImage';\n"
        tree = parser.parse(code)
        refs = ext.extract_references(tree, code, "cloudinaryUpload.js")

        call_refs = [r for r in refs if r["kind"] == "call"]
        # Should have call edges to both method and class
        targets = {r["target_name"] for r in call_refs}
        assert "CloudinaryService.uploadImage" in targets, "Missing call edge to method"
        assert "CloudinaryService" in targets, "Missing call edge to class"

    def test_apex_import_not_import_kind(self):
        """@salesforce/apex imports should NOT produce 'import' kind edges."""
        from tree_sitter_language_pack import get_parser
        from roam.languages.javascript_lang import JavaScriptExtractor

        parser = get_parser("javascript")
        ext = JavaScriptExtractor()
        code = b"import getAccounts from '@salesforce/apex/AccountHandler.getAccounts';\n"
        tree = parser.parse(code)
        refs = ext.extract_references(tree, code, "accountList.js")

        import_refs = [r for r in refs if r["kind"] == "import"
                       and r.get("import_path", "").startswith("@salesforce/apex/")]
        assert len(import_refs) == 0, "Apex imports should be 'call' kind, not 'import'"

    def test_apex_import_class_edge_resolution(self):
        """LWC @salesforce/apex import should resolve to both the Apex method
        AND the Apex class, enabling `roam symbol ClassName` to show the LWC."""
        from roam.index.relations import resolve_references

        apex_class_sym = {
            "id": 1, "file_id": 10, "file_path": "classes/CloudinaryService.cls",
            "name": "CloudinaryService", "qualified_name": "CloudinaryService",
            "kind": "class", "is_exported": True, "line_start": 1,
        }
        apex_method_sym = {
            "id": 2, "file_id": 10, "file_path": "classes/CloudinaryService.cls",
            "name": "uploadImage", "qualified_name": "CloudinaryService.uploadImage",
            "kind": "method", "is_exported": True, "line_start": 3,
        }
        lwc_class_sym = {
            "id": 3, "file_id": 20, "file_path": "lwc/cloudinaryUpload/cloudinaryUpload.js",
            "name": "CloudinaryUpload", "qualified_name": "CloudinaryUpload",
            "kind": "class", "is_exported": True, "line_start": 4,
        }

        symbols_by_name = {
            "CloudinaryService": [apex_class_sym],
            "uploadImage": [apex_method_sym],
            "CloudinaryUpload": [lwc_class_sym],
        }
        files_by_path = {
            "classes/CloudinaryService.cls": 10,
            "lwc/cloudinaryUpload/cloudinaryUpload.js": 20,
        }

        # Simulate the two references created by the JS extractor for apex imports
        references = [
            {
                "source_name": None,
                "target_name": "CloudinaryService.uploadImage",
                "kind": "call",
                "line": 1,
                "import_path": "@salesforce/apex/CloudinaryService.uploadImage",
                "source_file": "lwc/cloudinaryUpload/cloudinaryUpload.js",
            },
            {
                "source_name": None,
                "target_name": "CloudinaryService",
                "kind": "call",
                "line": 1,
                "import_path": "@salesforce/apex/CloudinaryService.uploadImage",
                "source_file": "lwc/cloudinaryUpload/cloudinaryUpload.js",
            },
        ]

        edges = resolve_references(references, symbols_by_name, files_by_path)
        target_ids = {e["target_id"] for e in edges}
        # Should have edges to BOTH the method and the class
        assert 1 in target_ids, "Missing edge to CloudinaryService class"
        assert 2 in target_ids, "Missing edge to uploadImage method"

    def test_multiple_apex_imports(self):
        """Multiple @salesforce/apex imports in one file should each produce edges."""
        from tree_sitter_language_pack import get_parser
        from roam.languages.javascript_lang import JavaScriptExtractor

        parser = get_parser("javascript")
        ext = JavaScriptExtractor()
        code = (
            b"import getResults from '@salesforce/apex/ers_DatatableController.getReturnResults';\n"
            b"import getMerged from '@salesforce/apex/DesignAliasDomain.getMergedDesignAliasAndGridRefs';\n"
            b"import createRecords from '@salesforce/apex/DesignAliasDomain.createAliasRecords';\n"
        )
        tree = parser.parse(code)
        refs = ext.extract_references(tree, code, "myComponent.js")

        call_refs = [r for r in refs if r["kind"] == "call"]
        targets = {r["target_name"] for r in call_refs}

        # Method-level edges
        assert "ers_DatatableController.getReturnResults" in targets
        assert "DesignAliasDomain.getMergedDesignAliasAndGridRefs" in targets
        assert "DesignAliasDomain.createAliasRecords" in targets

        # Class-level edges
        assert "ers_DatatableController" in targets
        assert "DesignAliasDomain" in targets

    def test_non_apex_salesforce_import_stays_import(self):
        """@salesforce/schema and @salesforce/label should remain 'import' kind."""
        from tree_sitter_language_pack import get_parser
        from roam.languages.javascript_lang import JavaScriptExtractor

        parser = get_parser("javascript")
        ext = JavaScriptExtractor()
        code = (
            b"import ACCOUNT_NAME from '@salesforce/schema/Account.Name';\n"
            b"import greeting from '@salesforce/label/c.Greeting';\n"
        )
        tree = parser.parse(code)
        refs = ext.extract_references(tree, code, "test.js")

        # These should be 'import' kind, not 'call'
        for ref in refs:
            assert ref["kind"] == "import", (
                f"Non-apex SF import should be 'import' kind, got '{ref['kind']}' "
                f"for target '{ref['target_name']}'"
            )


class TestP1C_FlowApexInvocable:
    """P1C: Flow actionCalls with actionType=apex should create 'call' edges."""

    def test_flow_apex_action_creates_call_edge(self, sfxml_extractor, xml_parser):
        """Flow actionCalls with actionType=apex should produce a 'call' edge."""
        tree, source = _parse_xml(xml_parser, """<?xml version="1.0" encoding="UTF-8"?>
<Flow xmlns="http://soap.sforce.com/2006/04/metadata">
    <actionCalls>
        <name>Invoke_Handler</name>
        <actionName>OrderProcessor</actionName>
        <actionType>apex</actionType>
    </actionCalls>
</Flow>
""")
        refs = sfxml_extractor.extract_references(
            tree, source, "flows/Process_Order.flow-meta.xml"
        )
        call_refs = [r for r in refs if r["kind"] == "call"]
        assert any(r["target_name"] == "OrderProcessor" for r in call_refs), \
            "Flow Apex actionCalls should create a 'call' edge"

    def test_flow_non_apex_action_creates_reference(self, sfxml_extractor, xml_parser):
        """Flow actionCalls with non-apex actionType should produce 'reference' edges."""
        tree, source = _parse_xml(xml_parser, """<?xml version="1.0" encoding="UTF-8"?>
<Flow xmlns="http://soap.sforce.com/2006/04/metadata">
    <actionCalls>
        <name>Send_Email</name>
        <actionName>emailSimple</actionName>
        <actionType>emailSimple</actionType>
    </actionCalls>
</Flow>
""")
        refs = sfxml_extractor.extract_references(
            tree, source, "flows/Send_Notification.flow-meta.xml"
        )
        # Should NOT have a 'call' edge for non-apex action
        call_refs = [r for r in refs if r["kind"] == "call" and r["target_name"] == "emailSimple"]
        assert len(call_refs) == 0
        # Should have a 'reference' edge
        ref_refs = [r for r in refs if r["kind"] == "reference" and r["target_name"] == "emailSimple"]
        assert len(ref_refs) >= 1

    def test_flow_multiple_apex_actions(self, sfxml_extractor, xml_parser):
        """Multiple Apex actionCalls in one Flow should each produce call edges."""
        tree, source = _parse_xml(xml_parser, """<?xml version="1.0" encoding="UTF-8"?>
<Flow xmlns="http://soap.sforce.com/2006/04/metadata">
    <actionCalls>
        <name>Validate</name>
        <actionName>ValidationService</actionName>
        <actionType>apex</actionType>
    </actionCalls>
    <actionCalls>
        <name>Process</name>
        <actionName>ProcessingEngine</actionName>
        <actionType>apex</actionType>
    </actionCalls>
    <actionCalls>
        <name>Notify</name>
        <actionName>emailAlert</actionName>
        <actionType>emailAlert</actionType>
    </actionCalls>
</Flow>
""")
        refs = sfxml_extractor.extract_references(
            tree, source, "flows/Complex_Flow.flow-meta.xml"
        )
        call_refs = [r for r in refs if r["kind"] == "call"]
        call_targets = {r["target_name"] for r in call_refs}
        assert "ValidationService" in call_targets
        assert "ProcessingEngine" in call_targets
        assert "emailAlert" not in call_targets


class TestP1D_ApexLabelReference:
    """P1D: Apex Label.X (without System prefix) should create reference edges."""

    def test_bare_label_reference(self, apex_extractor, apex_parser):
        """Label.MyLabel (without System.) should be extracted as a reference."""
        tree, source = _parse_apex(apex_parser, """
public class LabelUser {
    public String getLabel() {
        return Label.Welcome_Message;
    }
}
""")
        apex_extractor.extract_symbols(tree, source, "LabelUser.cls")
        refs = apex_extractor.extract_references(tree, source, "LabelUser.cls")
        targets = {r["target_name"] for r in refs}
        assert "Welcome_Message" in targets

    def test_both_system_label_and_bare_label(self, apex_extractor, apex_parser):
        """Both System.Label.X and Label.X should produce references."""
        tree, source = _parse_apex(apex_parser, """
public class MultiLabel {
    public void labels() {
        String a = System.Label.Label_A;
        String b = Label.Label_B;
    }
}
""")
        apex_extractor.extract_symbols(tree, source, "MultiLabel.cls")
        refs = apex_extractor.extract_references(tree, source, "MultiLabel.cls")
        targets = {r["target_name"] for r in refs}
        assert "Label_A" in targets, "System.Label.X should extract label name"
        assert "Label_B" in targets, "Label.X should extract label name"


class TestP1F_TriggerHandlerMetadata:
    """P1F: Custom metadata Trigger_Handler records should create edges
    to handler Apex classes."""

    def test_handler_class_reference(self, sfxml_extractor, xml_parser):
        """Trigger_Handler metadata with Handler_Class__c should reference the Apex class."""
        tree, source = _parse_xml(xml_parser, """<?xml version="1.0" encoding="UTF-8"?>
<CustomMetadata xmlns="http://soap.sforce.com/2006/04/metadata">
    <label>Account Trigger Handler</label>
    <values>
        <field>Handler_Class__c</field>
        <value>AccountTriggerHandler</value>
    </values>
    <values>
        <field>Object__c</field>
        <value>Account</value>
    </values>
</CustomMetadata>
""")
        refs = sfxml_extractor.extract_references(
            tree, source,
            "customMetadata/Trigger_Handler.AccountTriggerHandler.md-meta.xml"
        )
        ref_targets = {r["target_name"] for r in refs}
        assert "AccountTriggerHandler" in ref_targets, \
            "Handler_Class__c value should create a reference to the handler class"

    def test_non_class_field_not_extracted(self, sfxml_extractor, xml_parser):
        """Custom metadata fields not matching class patterns should not produce refs."""
        tree, source = _parse_xml(xml_parser, """<?xml version="1.0" encoding="UTF-8"?>
<CustomMetadata xmlns="http://soap.sforce.com/2006/04/metadata">
    <label>Some Config</label>
    <values>
        <field>Enabled__c</field>
        <value>true</value>
    </values>
    <values>
        <field>Max_Retries__c</field>
        <value>3</value>
    </values>
</CustomMetadata>
""")
        refs = sfxml_extractor.extract_references(
            tree, source, "customMetadata/Config.SomeConfig.md-meta.xml"
        )
        ref_targets = {r["target_name"] for r in refs}
        assert "true" not in ref_targets
        assert "3" not in ref_targets

    def test_apex_class_field_reference(self, sfxml_extractor, xml_parser):
        """Fields with 'Class' in the name should extract class references."""
        tree, source = _parse_xml(xml_parser, """<?xml version="1.0" encoding="UTF-8"?>
<CustomMetadata xmlns="http://soap.sforce.com/2006/04/metadata">
    <label>Apex Config</label>
    <values>
        <field>Apex_Class__c</field>
        <value>OrderProcessor</value>
    </values>
</CustomMetadata>
""")
        refs = sfxml_extractor.extract_references(
            tree, source, "customMetadata/Apex_Config.Order.md-meta.xml"
        )
        ref_targets = {r["target_name"] for r in refs}
        assert "OrderProcessor" in ref_targets


class TestP1_E2E_CrossLanguageEdges:
    """End-to-end test: full Salesforce project with all cross-language edge types."""

    @pytest.fixture(scope="class")
    def cross_lang_project(self, tmp_path_factory):
        """Create a Salesforce project with all P1 cross-language patterns."""
        proj = tmp_path_factory.mktemp("cross_lang_project")

        # Apex class with @AuraEnabled method
        classes_dir = proj / "force-app" / "main" / "default" / "classes"
        classes_dir.mkdir(parents=True)
        (classes_dir / "CloudinaryService.cls").write_text(
            'public class CloudinaryService {\n'
            '    @AuraEnabled\n'
            '    public static String uploadImage(String base64Data) {\n'
            '        String label = Label.Upload_Success;\n'
            '        return \'ok\';\n'
            '    }\n'
            '}\n'
        )
        (classes_dir / "CloudinaryService.cls-meta.xml").write_text(
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<ApexClass xmlns="http://soap.sforce.com/2006/04/metadata">\n'
            '    <apiVersion>58.0</apiVersion>\n'
            '    <status>Active</status>\n'
            '</ApexClass>\n'
        )

        # Apex invocable class
        (classes_dir / "OrderProcessor.cls").write_text(
            'public class OrderProcessor {\n'
            '    @InvocableMethod(label=\'Process Order\')\n'
            '    public static void processOrders(List<Id> orderIds) {\n'
            '        System.debug(\'processing\');\n'
            '    }\n'
            '}\n'
        )
        (classes_dir / "OrderProcessor.cls-meta.xml").write_text(
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<ApexClass xmlns="http://soap.sforce.com/2006/04/metadata">\n'
            '    <apiVersion>58.0</apiVersion>\n'
            '    <status>Active</status>\n'
            '</ApexClass>\n'
        )

        # Trigger handler class
        (classes_dir / "AccountTriggerHandler.cls").write_text(
            'public class AccountTriggerHandler {\n'
            '    public void run() {\n'
            '        System.debug(\'handler\');\n'
            '    }\n'
            '}\n'
        )
        (classes_dir / "AccountTriggerHandler.cls-meta.xml").write_text(
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<ApexClass xmlns="http://soap.sforce.com/2006/04/metadata">\n'
            '    <apiVersion>58.0</apiVersion>\n'
            '    <status>Active</status>\n'
            '</ApexClass>\n'
        )

        # LWC that imports Apex method and label
        lwc_dir = proj / "force-app" / "main" / "default" / "lwc" / "cloudinaryUpload"
        lwc_dir.mkdir(parents=True)
        (lwc_dir / "cloudinaryUpload.js").write_text(
            "import { LightningElement } from 'lwc';\n"
            "import uploadImage from '@salesforce/apex/CloudinaryService.uploadImage';\n"
            "import SUCCESS_LABEL from '@salesforce/label/c.Upload_Success';\n"
            "\n"
            "export default class CloudinaryUpload extends LightningElement {\n"
            "    async handleUpload() {\n"
            "        await uploadImage({ base64Data: this.data });\n"
            "    }\n"
            "}\n"
        )
        (lwc_dir / "cloudinaryUpload.js-meta.xml").write_text(
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<LightningComponentBundle xmlns="http://soap.sforce.com/2006/04/metadata">\n'
            '    <apiVersion>58.0</apiVersion>\n'
            '    <isExposed>true</isExposed>\n'
            '</LightningComponentBundle>\n'
        )

        # Custom Labels
        labels_dir = proj / "force-app" / "main" / "default" / "labels"
        labels_dir.mkdir(parents=True)
        (labels_dir / "CustomLabels.labels-meta.xml").write_text(
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<CustomLabels xmlns="http://soap.sforce.com/2006/04/metadata">\n'
            '    <labels>\n'
            '        <fullName>Upload_Success</fullName>\n'
            '        <language>en_US</language>\n'
            '        <value>Upload Successful</value>\n'
            '    </labels>\n'
            '</CustomLabels>\n'
        )

        # Flow that calls Apex invocable
        flows_dir = proj / "force-app" / "main" / "default" / "flows"
        flows_dir.mkdir(parents=True)
        (flows_dir / "Process_Order.flow-meta.xml").write_text(
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<Flow xmlns="http://soap.sforce.com/2006/04/metadata">\n'
            '    <label>Process Order</label>\n'
            '    <actionCalls>\n'
            '        <name>Invoke_Processor</name>\n'
            '        <actionName>OrderProcessor</actionName>\n'
            '        <actionType>apex</actionType>\n'
            '    </actionCalls>\n'
            '</Flow>\n'
        )

        # Custom Metadata: Trigger_Handler
        cmd_dir = proj / "force-app" / "main" / "default" / "customMetadata"
        cmd_dir.mkdir(parents=True)
        (cmd_dir / "Trigger_Handler.AccountTriggerHandler.md-meta.xml").write_text(
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<CustomMetadata xmlns="http://soap.sforce.com/2006/04/metadata">\n'
            '    <label>Account Trigger Handler</label>\n'
            '    <values>\n'
            '        <field>Handler_Class__c</field>\n'
            '        <value>AccountTriggerHandler</value>\n'
            '    </values>\n'
            '</CustomMetadata>\n'
        )

        # Aura component with $Label reference
        aura_dir = proj / "force-app" / "main" / "default" / "aura" / "UploadCard"
        aura_dir.mkdir(parents=True)
        (aura_dir / "UploadCard.cmp").write_text(
            '<aura:component>\n'
            '    <lightning:button label="{!$Label.c.Upload_Success}"/>\n'
            '</aura:component>\n'
        )

        git_init(proj)
        out, rc = roam("index", cwd=str(proj))
        assert rc == 0, f"Index failed: {out}"
        return proj

    def test_lwc_apex_call_edge(self, cross_lang_project):
        """LWC should appear as a caller of the Apex class."""
        out, rc = roam("symbol", "CloudinaryService", cwd=str(cross_lang_project))
        assert rc == 0
        assert "cloudinaryUpload" in out.lower() or "CloudinaryUpload" in out, \
            f"LWC should be a caller of CloudinaryService. Output:\n{out}"

    def test_lwc_apex_method_edge(self, cross_lang_project):
        """LWC should appear as a caller of the Apex method."""
        out, rc = roam("symbol", "uploadImage", cwd=str(cross_lang_project))
        assert rc == 0
        assert "cloudinaryUpload" in out.lower() or "CloudinaryUpload" in out, \
            f"LWC should be a caller of uploadImage. Output:\n{out}"

    def test_flow_apex_call_edge(self, cross_lang_project):
        """Flow should have a callee edge to the Apex invocable class."""
        out, rc = roam("symbol", "Process Order", cwd=str(cross_lang_project))
        assert rc == 0
        assert "OrderProcessor" in out, \
            f"Flow should have OrderProcessor as a callee. Output:\n{out}"

    def test_impact_includes_lwc(self, cross_lang_project):
        """Impact analysis of CloudinaryService should include the LWC file."""
        out, rc = roam("impact", "CloudinaryService", cwd=str(cross_lang_project))
        assert rc == 0
        assert "cloudinaryUpload" in out, \
            f"Impact should include cloudinaryUpload.js. Output:\n{out}"
