"""Salesforce Visualforce page and component extractor.

Handles Visualforce files:
  - .page      (Visualforce pages)
  - .component (Visualforce components)

Visualforce files are XML/XHTML hybrids.  Key elements:
  <apex:page controller="MyController" extensions="ExtA,ExtB">
  <apex:component controller="CompController">
  <apex:include pageName="SharedHeader"/>
  <c:CustomComponent/>

Symbols extracted:
  - Page/component name (from filename)
  - Controller reference → extracted as part of the signature

References extracted:
  - controller="MyController" → Apex class reference
  - extensions="ExtA,ExtB" → Apex class references
  - <apex:include pageName="X"/> → page reference
  - <c:CustomComponent/> → custom component reference
  - {!$Label.LabelName} → custom label reference
  - {!$Setup.CustomSetting__c.FieldName} → custom setting reference
  - {!controller.property} → controller property reference
"""

import re

from .base import LanguageExtractor

# Regex patterns for VF merge field expressions
_VF_LABEL_RE = re.compile(r'\{\!\s*\$Label\.(\w+)')
_VF_SETUP_RE = re.compile(r'\{\!\s*\$Setup\.(\w+)')
_VF_FIELD_RE = re.compile(r'\{\!\s*(\w+)\.(\w+)')  # {!object.field}


class VisualforceExtractor(LanguageExtractor):
    """Extractor for Salesforce Visualforce .page and .component files."""

    @property
    def language_name(self) -> str:
        return "visualforce"

    @property
    def file_extensions(self) -> list[str]:
        return [".page", ".component"]

    def extract_symbols(self, tree, source: bytes, file_path: str) -> list[dict]:
        symbols: list[dict] = []
        self._walk_symbols(tree.root_node, source, symbols, file_path)
        return symbols

    def extract_references(self, tree, source: bytes, file_path: str) -> list[dict]:
        refs: list[dict] = []
        self._walk_refs(tree.root_node, source, refs, file_path)
        return refs

    # ------------------------------------------------------------------ #
    #  Symbol extraction                                                  #
    # ------------------------------------------------------------------ #

    def _walk_symbols(self, node, source, symbols, file_path):
        if node.type == "element":
            tag = self._get_tag(node, source)
            if tag in ("apex:page", "apex:component"):
                kind = "class"
                comp_name = self._derive_name(file_path)
                sig = f"{tag.split(':')[1]} {comp_name}"

                attrs = self._get_attrs(node, source)
                controller = attrs.get("controller")
                if controller:
                    sig += f" controller={controller}"
                extensions = attrs.get("extensions")
                if extensions:
                    sig += f" extensions={extensions}"

                symbols.append(self._make_symbol(
                    name=comp_name,
                    kind=kind,
                    line_start=node.start_point[0] + 1,
                    line_end=node.end_point[0] + 1,
                    qualified_name=comp_name,
                    signature=sig,
                    visibility="public",
                    is_exported=True,
                ))
                return

        for child in node.children:
            self._walk_symbols(child, source, symbols, file_path)

    # ------------------------------------------------------------------ #
    #  Reference extraction                                               #
    # ------------------------------------------------------------------ #

    def _walk_refs(self, node, source, refs, file_path):
        if node.type == "element":
            tag = self._get_tag(node, source)
            attrs = self._get_attrs(node, source)

            if tag in ("apex:page", "apex:component"):
                # controller="MyController"
                controller = attrs.get("controller")
                if controller:
                    refs.append(self._make_reference(
                        target_name=controller,
                        kind="reference",
                        line=node.start_point[0] + 1,
                    ))
                # extensions="ExtA,ExtB"
                extensions = attrs.get("extensions")
                if extensions:
                    for ext in extensions.split(","):
                        ext = ext.strip()
                        if ext:
                            refs.append(self._make_reference(
                                target_name=ext,
                                kind="reference",
                                line=node.start_point[0] + 1,
                            ))

            elif tag == "apex:include":
                page_name = attrs.get("pageName")
                if page_name:
                    refs.append(self._make_reference(
                        target_name=page_name,
                        kind="reference",
                        line=node.start_point[0] + 1,
                    ))

            # Custom component usage: <c:MyComponent> or <ns:MyComponent>
            elif tag and ":" in tag:
                ns, comp = tag.split(":", 1)
                if ns != "apex" and comp[0:1].isupper():
                    refs.append(self._make_reference(
                        target_name=comp,
                        kind="reference",
                        line=node.start_point[0] + 1,
                    ))

        # Scan attribute values for merge field expressions
        if node.type == "element":
            self._extract_merge_fields(node, source, refs)

        for child in node.children:
            self._walk_refs(child, source, refs, file_path)

    def _extract_merge_fields(self, node, source, refs):
        """Extract references from VF merge field expressions in attribute values."""
        attrs = self._get_attrs(node, source)
        for val in attrs.values():
            if "{!" not in val:
                continue
            line = node.start_point[0] + 1
            # {!$Label.MyLabel}
            for m in _VF_LABEL_RE.finditer(val):
                refs.append(self._make_reference(
                    target_name=m.group(1),
                    kind="reference",
                    line=line,
                ))
            # {!$Setup.CustomSetting__c.Field}
            for m in _VF_SETUP_RE.finditer(val):
                refs.append(self._make_reference(
                    target_name=m.group(1),
                    kind="reference",
                    line=line,
                ))

    # ------------------------------------------------------------------ #
    #  Helpers                                                            #
    # ------------------------------------------------------------------ #

    def _get_tag(self, element_node, source: bytes) -> str | None:
        """Get the full tag name from an element."""
        for child in element_node.children:
            if child.type in ("STag", "EmptyElemTag"):
                for sub in child.children:
                    if sub.type == "Name":
                        return self.node_text(sub, source)
        return None

    def _get_attrs(self, element_node, source: bytes) -> dict[str, str]:
        """Get all attributes as a dict from an element's start tag."""
        attrs: dict[str, str] = {}
        for child in element_node.children:
            if child.type in ("STag", "EmptyElemTag"):
                for sub in child.children:
                    if sub.type == "Attribute":
                        name_node = None
                        value_node = None
                        for attr_child in sub.children:
                            if attr_child.type == "Name":
                                name_node = attr_child
                            elif attr_child.type == "AttValue":
                                value_node = attr_child
                        if name_node and value_node:
                            k = self.node_text(name_node, source)
                            v = self.node_text(value_node, source).strip('"\'')
                            attrs[k] = v
        return attrs

    def _derive_name(self, file_path: str) -> str:
        """Derive page/component name from file path."""
        import os
        basename = os.path.basename(file_path)
        name, _ = os.path.splitext(basename)
        return name
