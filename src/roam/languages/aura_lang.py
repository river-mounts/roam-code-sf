"""Salesforce Aura component extractor.

Handles Aura component files:
  - .cmp  (Lightning components)
  - .app  (Lightning applications)
  - .evt  (Lightning events)
  - .intf (Lightning interfaces)
  - .design (component design files)

Aura files are XML-based.  The root element identifies the type:
  <aura:component>, <aura:application>, <aura:event>, <aura:interface>

Symbols extracted:
  - Component/app/event/interface name (from filename)
  - <aura:attribute> declarations -> fields
  - <aura:handler> declarations -> methods
  - <aura:method> declarations -> methods
  - <aura:registerEvent> declarations -> fields

References extracted:
  - controller="MyController" -> Apex class reference
  - extends="c:BaseComponent" -> inheritance reference
  - <aura:handler event="c:MyEvent"> -> event reference
  - <c:ChildComponent> usage -> component reference
"""

import re

from .base import LanguageExtractor

# Regex to find custom namespace component tags: <c:ComponentName or <ns:ComponentName
_COMPONENT_TAG_RE = re.compile(r'<([a-zA-Z]+):([A-Z]\w+)')
# Regex to find aura attribute values
_ATTR_RE = re.compile(r'(\w+)\s*=\s*"([^"]*)"')


class AuraExtractor(LanguageExtractor):
    """Extractor for Salesforce Aura/Lightning component XML files."""

    @property
    def language_name(self) -> str:
        return "aura"

    @property
    def file_extensions(self) -> list[str]:
        return [".cmp", ".app", ".evt", ".intf", ".design"]

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
            if tag in ("aura:component", "aura:application", "aura:event", "aura:interface"):
                kind_map = {
                    "aura:component": "class",
                    "aura:application": "class",
                    "aura:event": "class",
                    "aura:interface": "interface",
                }
                kind = kind_map[tag]
                comp_name = self._derive_name(file_path)
                sig = f"{tag.split(':')[1]} {comp_name}"

                # Check for extends/implements
                attrs = self._get_attrs(node, source)
                if "extends" in attrs:
                    sig += f" extends {attrs['extends']}"
                if "implements" in attrs:
                    sig += f" implements {attrs['implements']}"

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

                # Walk children for attributes/methods/handlers
                self._walk_aura_members(node, source, symbols, comp_name)
                return

        for child in node.children:
            self._walk_symbols(child, source, symbols, file_path)

    def _walk_aura_members(self, node, source, symbols, parent_name):
        """Walk an Aura component body for attribute/method/handler declarations."""
        for child in node.children:
            if child.type == "element":
                tag = self._get_tag(child, source)
                attrs = self._get_attrs(child, source)

                if tag == "aura:attribute":
                    name = attrs.get("name", "")
                    if name:
                        atype = attrs.get("type", "")
                        sig = f"attribute {name}"
                        if atype:
                            sig += f" : {atype}"
                        default = attrs.get("default")
                        if default:
                            sig += f" = {default}"
                        symbols.append(self._make_symbol(
                            name=name,
                            kind="field",
                            line_start=child.start_point[0] + 1,
                            line_end=child.end_point[0] + 1,
                            qualified_name=f"{parent_name}.{name}",
                            signature=sig,
                            docstring=attrs.get("description"),
                            visibility="public",
                            is_exported=True,
                            parent_name=parent_name,
                        ))

                elif tag == "aura:method":
                    name = attrs.get("name", "")
                    if name:
                        sig = f"method {name}"
                        symbols.append(self._make_symbol(
                            name=name,
                            kind="method",
                            line_start=child.start_point[0] + 1,
                            line_end=child.end_point[0] + 1,
                            qualified_name=f"{parent_name}.{name}",
                            signature=sig,
                            docstring=attrs.get("description"),
                            visibility="public",
                            is_exported=True,
                            parent_name=parent_name,
                        ))

                elif tag == "aura:handler":
                    name = attrs.get("name", "")
                    if name:
                        sig = f"handler {name}"
                        action = attrs.get("action")
                        if action:
                            sig += f" -> {action}"
                        symbols.append(self._make_symbol(
                            name=name,
                            kind="method",
                            line_start=child.start_point[0] + 1,
                            line_end=child.end_point[0] + 1,
                            qualified_name=f"{parent_name}.{name}",
                            signature=sig,
                            visibility="public",
                            is_exported=True,
                            parent_name=parent_name,
                        ))

                elif tag == "aura:registerEvent":
                    name = attrs.get("name", "")
                    if name:
                        etype = attrs.get("type", "")
                        sig = f"registerEvent {name}"
                        if etype:
                            sig += f" : {etype}"
                        symbols.append(self._make_symbol(
                            name=name,
                            kind="field",
                            line_start=child.start_point[0] + 1,
                            line_end=child.end_point[0] + 1,
                            qualified_name=f"{parent_name}.{name}",
                            signature=sig,
                            visibility="public",
                            is_exported=True,
                            parent_name=parent_name,
                        ))

            # Recurse into child elements
            self._walk_aura_members(child, source, symbols, parent_name)

    # ------------------------------------------------------------------ #
    #  Reference extraction                                               #
    # ------------------------------------------------------------------ #

    def _walk_refs(self, node, source, refs, file_path):
        if node.type == "element":
            tag = self._get_tag(node, source)
            attrs = self._get_attrs(node, source)

            # Root component attributes
            if tag in ("aura:component", "aura:application"):
                # controller="MyApexController" -> reference
                controller = attrs.get("controller")
                if controller:
                    refs.append(self._make_reference(
                        target_name=controller,
                        kind="reference",
                        line=node.start_point[0] + 1,
                    ))
                # extends="c:BaseComponent" -> reference
                extends = attrs.get("extends")
                if extends:
                    name = extends.split(":")[-1] if ":" in extends else extends
                    refs.append(self._make_reference(
                        target_name=name,
                        kind="inherits",
                        line=node.start_point[0] + 1,
                    ))
                # implements="force:appHostable,flexipage:availableForAllPageTypes"
                implements = attrs.get("implements")
                if implements:
                    for iface in implements.split(","):
                        iface = iface.strip()
                        if iface:
                            refs.append(self._make_reference(
                                target_name=iface,
                                kind="implements",
                                line=node.start_point[0] + 1,
                            ))

            # <aura:handler event="c:MyEvent"> -> event reference
            elif tag == "aura:handler":
                event = attrs.get("event")
                if event:
                    name = event.split(":")[-1] if ":" in event else event
                    refs.append(self._make_reference(
                        target_name=name,
                        kind="reference",
                        line=node.start_point[0] + 1,
                    ))

            # <aura:registerEvent type="c:MyEvent"> -> event reference
            elif tag == "aura:registerEvent":
                etype = attrs.get("type")
                if etype:
                    name = etype.split(":")[-1] if ":" in etype else etype
                    refs.append(self._make_reference(
                        target_name=name,
                        kind="reference",
                        line=node.start_point[0] + 1,
                    ))

            # Custom component usage: <c:MyChild> or <ns:MyChild>
            elif tag and ":" in tag:
                ns, comp = tag.split(":", 1)
                # Skip aura: namespace (already handled above)
                if ns != "aura" and comp[0:1].isupper():
                    refs.append(self._make_reference(
                        target_name=comp,
                        kind="reference",
                        line=node.start_point[0] + 1,
                    ))

        for child in node.children:
            self._walk_refs(child, source, refs, file_path)

    # ------------------------------------------------------------------ #
    #  Helpers                                                            #
    # ------------------------------------------------------------------ #

    def _get_tag(self, element_node, source: bytes) -> str | None:
        """Get the full tag name (e.g., 'aura:component') from an element."""
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
        """Derive component name from file path."""
        import os
        basename = os.path.basename(file_path)
        name, _ = os.path.splitext(basename)
        return name
