"""Salesforce XML metadata extractor.

Handles Salesforce metadata XML files such as:
  - CustomObject (.object-meta.xml)
  - CustomField definitions within objects
  - Validation rules, workflows, triggers
  - Lightning Web Component metadata (.js-meta.xml)
  - Profiles, permission sets
  - Flows, approval processes
  - Custom labels, static resources
  - Page layouts
  - Any *-meta.xml sidecar file

Extracts metadata elements as symbols so that roam can track
relationships between Apex code and declarative configuration.
"""

import re

from .base import LanguageExtractor


# XML element names that represent named Salesforce metadata entities.
# Maps element tag -> symbol kind.
_SF_METADATA_ELEMENTS: dict[str, str] = {
    # Object-level
    "CustomObject": "class",
    "CustomField": "field",
    "fields": "field",
    "validationRules": "function",
    "webLinks": "function",
    "listViews": "function",
    "recordTypes": "class",
    "compactLayouts": "class",
    "fieldSets": "class",
    "sharingRules": "function",

    # Automation
    "Flow": "class",
    "Workflow": "class",
    "WorkflowRule": "function",
    "WorkflowFieldUpdate": "function",
    "WorkflowAlert": "function",
    "ApprovalProcess": "class",

    # Security
    "Profile": "class",
    "PermissionSet": "class",
    "fieldPermissions": "field",
    "objectPermissions": "field",
    "classAccesses": "field",
    "pageAccesses": "field",
    "tabVisibilities": "field",

    # UI
    "Layout": "class",
    "FlexiPage": "class",
    "CustomTab": "class",
    "CustomApplication": "class",
    "HomePageComponent": "class",

    # LWC metadata
    "LightningComponentBundle": "class",

    # Labels and resources
    "CustomLabel": "constant",
    "CustomLabels": "class",
    "labels": "constant",
    "StaticResource": "constant",

    # Apex metadata
    "ApexClass": "class",
    "ApexTrigger": "class",
    "ApexPage": "class",
    "ApexComponent": "class",

    # Email templates
    "EmailTemplate": "class",

    # Custom metadata & settings
    "CustomMetadata": "class",
    "CustomSetting": "class",
}


class SalesforceXmlExtractor(LanguageExtractor):
    """Extractor for Salesforce XML metadata files.

    Walks the XML tree-sitter AST to find Salesforce metadata elements
    and their <fullName> or <apiName> children, emitting them as symbols.
    """

    @property
    def language_name(self) -> str:
        return "sfxml"

    @property
    def file_extensions(self) -> list[str]:
        return [
            ".object-meta.xml", ".field-meta.xml", ".layout-meta.xml",
            ".profile-meta.xml", ".permissionset-meta.xml",
            ".flow-meta.xml", ".flexipage-meta.xml",
            ".labels-meta.xml", ".tab-meta.xml",
            ".app-meta.xml", ".cls-meta.xml", ".trigger-meta.xml",
            ".page-meta.xml", ".component-meta.xml",
            ".resource-meta.xml", ".js-meta.xml",
            ".email-meta.xml", ".workflow-meta.xml",
            ".customMetadata-meta.xml",
        ]

    def extract_symbols(self, tree, source: bytes, file_path: str) -> list[dict]:
        symbols: list[dict] = []
        self._root_type: str | None = None
        self._walk_xml(tree.root_node, source, symbols, parent_name=None, file_path=file_path)
        return symbols

    # Tags whose text always references another metadata entity
    _ALWAYS_REF_TAGS = frozenset({
        # Apex class/trigger references
        "apexClass", "apexPage", "apexComponent", "apexTrigger",
        "triggerType", "template",
        # Object/field cross-references
        "customObject", "referenceTo", "relatedList", "relationshipName",
        "lookupFilter",
        # Automation cross-references
        "actionName", "flowName", "targetWorkflow",
    })

    # Tags that are references only inside specific parent contexts
    _CONTEXT_REF_PARENTS = {
        "field": frozenset({
            "fieldPermissions", "layoutItems", "columns",
            "WorkflowFieldUpdate", "sortField", "searchResultsAdditionalFields",
            "displayedFields", "filterItems",
        }),
        "object": frozenset({
            "fieldPermissions", "objectPermissions", "listViews",
            "searchLayouts",
        }),
        "class": frozenset({
            "classAccesses",
        }),
        "name": frozenset({
            "actionOverrides",
        }),
    }

    # Regex for extracting Object.Field__c references from formula expressions
    _FORMULA_FIELD_RE = re.compile(r'\b([A-Z]\w+)\.([A-Za-z_]\w+__[cr])\b')

    def extract_references(self, tree, source: bytes, file_path: str) -> list[dict]:
        refs: list[dict] = []
        self._walk_xml_refs(tree.root_node, source, refs, file_path=file_path, parent_tag=None)
        return refs

    # ------------------------------------------------------------------ #
    #  XML tree walking                                                   #
    # ------------------------------------------------------------------ #

    def _walk_xml(self, node, source: bytes, symbols: list[dict], parent_name, file_path: str):
        """Walk XML AST and extract Salesforce metadata elements as symbols."""
        if node.type == "element":
            tag_name = self._get_element_tag(node, source)
            if tag_name and tag_name in _SF_METADATA_ELEMENTS:
                kind = _SF_METADATA_ELEMENTS[tag_name]
                # Try to find the name of this metadata element
                elem_name = self._get_child_text(node, source, "fullName")
                if not elem_name:
                    elem_name = self._get_child_text(node, source, "apiName")
                if not elem_name:
                    elem_name = self._get_child_text(node, source, "label")
                if not elem_name:
                    elem_name = self._get_child_text(node, source, "masterLabel")
                if not elem_name:
                    # Use tag name as a fallback only for root-level types
                    if parent_name is None:
                        elem_name = self._derive_name_from_path(file_path)
                    else:
                        elem_name = tag_name

                qualified = f"{parent_name}.{elem_name}" if parent_name else elem_name

                # Get description/help text if available
                description = self._get_child_text(node, source, "description")
                if not description:
                    description = self._get_child_text(node, source, "inlineHelpText")

                # Build a useful signature
                sig = f"{tag_name}: {elem_name}"
                field_type = self._get_child_text(node, source, "type")
                if field_type:
                    sig += f" ({field_type})"
                required = self._get_child_text(node, source, "required")
                if required and required.lower() == "true":
                    sig += " [required]"

                symbols.append(self._make_symbol(
                    name=elem_name,
                    kind=kind,
                    line_start=node.start_point[0] + 1,
                    line_end=node.end_point[0] + 1,
                    qualified_name=qualified,
                    signature=sig,
                    docstring=description,
                    visibility="public",
                    is_exported=True,
                    parent_name=parent_name,
                ))

                # For container elements, recurse with this as parent
                if kind == "class":
                    content = self._get_content_node(node)
                    if content:
                        for child in content.children:
                            self._walk_xml(child, source, symbols, parent_name=qualified, file_path=file_path)
                    return  # Don't double-recurse

        # Recurse into children
        for child in node.children:
            self._walk_xml(child, source, symbols, parent_name=parent_name, file_path=file_path)

    def _walk_xml_refs(self, node, source: bytes, refs: list[dict], file_path: str,
                       parent_tag: str | None = None):
        """Extract references from XML metadata with context-aware tag resolution.

        Always-reference tags (apexClass, referenceTo, etc.) are extracted unconditionally.
        Context-dependent tags (field, object, class, name) are only extracted when
        they appear inside specific parent elements (e.g., <field> inside <fieldPermissions>).
        Formula text is scanned for Object.Field__c patterns.
        """
        if node.type == "element":
            tag_name = self._get_element_tag(node, source)

            if tag_name:
                # Always-reference tags
                if tag_name in self._ALWAYS_REF_TAGS:
                    text = self._get_element_text(node, source)
                    if text:
                        refs.append(self._make_reference(
                            target_name=text,
                            kind="reference",
                            line=node.start_point[0] + 1,
                        ))

                # Context-dependent tags
                elif tag_name in self._CONTEXT_REF_PARENTS:
                    valid_parents = self._CONTEXT_REF_PARENTS[tag_name]
                    if parent_tag and parent_tag in valid_parents:
                        text = self._get_element_text(node, source)
                        if text:
                            # "Account.Industry__c" → reference to Industry__c
                            target = text.split(".")[-1] if "." in text else text
                            refs.append(self._make_reference(
                                target_name=target,
                                kind="reference",
                                line=node.start_point[0] + 1,
                            ))

                # Formula fields — scan for Object.Field__c patterns
                elif tag_name in ("formula", "formulaText", "errorConditionFormula"):
                    text = self._get_element_text(node, source)
                    if text:
                        self._extract_formula_refs(text, node.start_point[0] + 1, refs)

            # Recurse, passing current tag as parent context
            for child in node.children:
                self._walk_xml_refs(child, source, refs, file_path=file_path,
                                    parent_tag=tag_name)
            return

        for child in node.children:
            self._walk_xml_refs(child, source, refs, file_path=file_path,
                               parent_tag=parent_tag)

    def _extract_formula_refs(self, formula_text: str, line: int, refs: list[dict]):
        """Extract Object.Field__c references from Salesforce formula syntax."""
        for m in self._FORMULA_FIELD_RE.finditer(formula_text):
            refs.append(self._make_reference(
                target_name=m.group(2),  # Field API name
                kind="reference",
                line=line,
            ))

    # ------------------------------------------------------------------ #
    #  XML helpers                                                        #
    # ------------------------------------------------------------------ #

    def _get_element_tag(self, element_node, source: bytes) -> str | None:
        """Get the tag name from an XML element node."""
        # The element has STag (start tag) or EmptyElemTag children
        for child in element_node.children:
            if child.type in ("STag", "EmptyElemTag"):
                for sub in child.children:
                    if sub.type == "Name":
                        return self.node_text(sub, source)
        return None

    def _get_content_node(self, element_node):
        """Get the content node from an XML element."""
        for child in element_node.children:
            if child.type == "content":
                return child
        return None

    def _get_element_text(self, element_node, source: bytes) -> str | None:
        """Get the text content of a simple XML element like <fullName>Foo</fullName>.

        Concatenates all CharData children to handle XML entity references
        (e.g., &gt; splitting text into multiple CharData nodes).
        """
        content = self._get_content_node(element_node)
        if content:
            parts = []
            for child in content.children:
                if child.type == "CharData":
                    parts.append(self.node_text(child, source))
            text = "".join(parts).strip()
            if text:
                return text
        return None

    def _get_child_text(self, parent_node, source: bytes, child_tag: str) -> str | None:
        """Find a child element by tag name and return its text content."""
        content = self._get_content_node(parent_node)
        if not content:
            return None
        for child in content.children:
            if child.type == "element":
                tag = self._get_element_tag(child, source)
                if tag == child_tag:
                    return self._get_element_text(child, source)
        return None

    def _derive_name_from_path(self, file_path: str) -> str:
        """Derive a metadata name from the file path.

        E.g., 'force-app/main/default/objects/Account/Account.object-meta.xml' -> 'Account'
              'force-app/main/default/classes/MyClass.cls-meta.xml' -> 'MyClass'
        """
        import os
        basename = os.path.basename(file_path)
        # Strip all meta.xml suffixes
        for suffix in ("-meta.xml", ".meta.xml"):
            if basename.endswith(suffix):
                basename = basename[: -len(suffix)]
                break
        # Strip remaining extension
        name, _ = os.path.splitext(basename)
        return name if name else basename
