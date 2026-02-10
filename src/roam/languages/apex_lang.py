"""Salesforce Apex symbol and reference extractor.

Handles Apex classes (.cls) and triggers (.trigger).
Apex syntax is very close to Java, so the AST node types are largely shared.
Salesforce-specific constructs handled here:
  - trigger_declaration (trigger ... on SObject (...) { ... })
  - Sharing modifiers: with sharing, without sharing, inherited sharing
  - Apex properties with accessor_list (get; set; or get { ... } set { ... })
  - Annotations: @AuraEnabled, @IsTest, @TestVisible, @InvocableMethod, etc.
  - DML expressions: insert, update, delete, upsert, undelete, merge
  - SOQL/SOSL inline queries (detected as references to SObject types)
"""

from .base import LanguageExtractor


class ApexExtractor(LanguageExtractor):
    """Apex symbol and reference extractor for Salesforce .cls and .trigger files."""

    @property
    def language_name(self) -> str:
        return "apex"

    @property
    def file_extensions(self) -> list[str]:
        return [".cls", ".trigger"]

    def extract_symbols(self, tree, source: bytes, file_path: str) -> list[dict]:
        symbols: list[dict] = []
        self._pending_inherits: list[dict] = []
        self._walk_symbols(tree.root_node, source, symbols, parent_name=None)
        return symbols

    def extract_references(self, tree, source: bytes, file_path: str) -> list[dict]:
        refs: list[dict] = []
        self._walk_refs(tree.root_node, source, refs, scope_name=None)
        # Collect inheritance refs accumulated during extract_symbols
        refs.extend(getattr(self, "_pending_inherits", []))
        self._pending_inherits = []
        return refs

    # ------------------------------------------------------------------ #
    #  Docstrings                                                         #
    # ------------------------------------------------------------------ #

    def get_docstring(self, node, source: bytes) -> str | None:
        """ApexDoc: /** ... */ block comment before a declaration."""
        prev = node.prev_sibling
        if prev and prev.type in ("block_comment", "comment"):
            text = self.node_text(prev, source).strip()
            if text.startswith("/**"):
                text = text[3:]
                if text.endswith("*/"):
                    text = text[:-2]
                return text.strip()
        return None

    # ------------------------------------------------------------------ #
    #  Modifier helpers                                                   #
    # ------------------------------------------------------------------ #

    def _get_visibility(self, node, source: bytes) -> str:
        for child in node.children:
            if child.type == "modifiers":
                text = self.node_text(child, source).lower()
                if "private" in text:
                    return "private"
                if "protected" in text:
                    return "protected"
                if "public" in text:
                    return "public"
                if "global" in text:
                    return "public"  # global ≈ public (cross-namespace)
        return "private"  # Apex default is private

    def _get_annotations(self, node, source: bytes) -> list[str]:
        annotations: list[str] = []
        for child in node.children:
            if child.type == "modifiers":
                for sub in child.children:
                    if sub.type in ("annotation", "marker_annotation"):
                        annotations.append(self.node_text(sub, source))
        return annotations

    def _has_modifier(self, node, source: bytes, modifier: str) -> bool:
        for child in node.children:
            if child.type == "modifiers":
                return modifier in self.node_text(child, source).lower()
        return False

    def _get_sharing_modifier(self, node, source: bytes) -> str | None:
        """Return sharing keyword if present: 'with sharing', 'without sharing', 'inherited sharing'."""
        for child in node.children:
            if child.type == "modifiers":
                text = self.node_text(child, source)
                if "with sharing" in text:
                    if "without sharing" in text:
                        return "without sharing"
                    if "inherited sharing" in text:
                        return "inherited sharing"
                    return "with sharing"
        return None

    # ------------------------------------------------------------------ #
    #  Symbol extraction                                                  #
    # ------------------------------------------------------------------ #

    def _walk_symbols(self, node, source: bytes, symbols: list[dict], parent_name):
        for child in node.children:
            if child.type == "class_declaration":
                self._extract_class(child, source, symbols, parent_name, kind="class")
            elif child.type == "interface_declaration":
                self._extract_class(child, source, symbols, parent_name, kind="interface")
            elif child.type == "enum_declaration":
                self._extract_enum(child, source, symbols, parent_name)
            elif child.type == "trigger_declaration":
                self._extract_trigger(child, source, symbols)
            elif child.type == "method_declaration":
                self._extract_method(child, source, symbols, parent_name)
            elif child.type == "constructor_declaration":
                self._extract_constructor(child, source, symbols, parent_name)
            elif child.type == "field_declaration":
                self._extract_field(child, source, symbols, parent_name)

    def _extract_class(self, node, source, symbols, parent_name, kind="class"):
        name_node = node.child_by_field_name("name")
        if name_node is None:
            # Fallback: find first identifier child
            for child in node.children:
                if child.type == "identifier":
                    name_node = child
                    break
        if name_node is None:
            return

        name = self.node_text(name_node, source)
        vis = self._get_visibility(node, source)
        annotations = self._get_annotations(node, source)
        sharing = self._get_sharing_modifier(node, source)

        qualified = f"{parent_name}.{name}" if parent_name else name
        sig = f"{kind} {name}"

        type_params = node.child_by_field_name("type_parameters")
        if type_params:
            sig += self.node_text(type_params, source)

        # Check superclass
        superclass = node.child_by_field_name("superclass")
        if superclass:
            sig += f" {self.node_text(superclass, source)}"
            for child in superclass.children:
                if child.type == "type_identifier":
                    self._pending_inherits.append(self._make_reference(
                        target_name=self.node_text(child, source),
                        kind="inherits",
                        line=node.start_point[0] + 1,
                        source_name=qualified,
                    ))
                    break

        # Check interfaces
        interfaces = node.child_by_field_name("interfaces")
        if interfaces:
            sig += f" {self.node_text(interfaces, source)}"
            self._collect_type_refs(interfaces, source, "implements", node.start_point[0] + 1, qualified)

        if sharing:
            sig = f"{sharing} {sig}"

        if annotations:
            sig = "\n".join(annotations) + "\n" + sig

        symbols.append(self._make_symbol(
            name=name,
            kind=kind,
            line_start=node.start_point[0] + 1,
            line_end=node.end_point[0] + 1,
            qualified_name=qualified,
            signature=sig,
            docstring=self.get_docstring(node, source),
            visibility=vis,
            is_exported=(vis == "public"),
            parent_name=parent_name,
        ))

        # Walk class body
        body = node.child_by_field_name("body")
        if body:
            self._walk_symbols(body, source, symbols, qualified)

    def _extract_trigger(self, node, source, symbols):
        """Extract a trigger declaration as a top-level symbol.

        trigger MyTrigger on Account (before insert, after update) { ... }
        """
        trigger_name = None
        sobject_name = None
        events: list[str] = []

        for child in node.children:
            if child.type == "identifier":
                if trigger_name is None:
                    trigger_name = self.node_text(child, source)
                elif sobject_name is None:
                    sobject_name = self.node_text(child, source)
            elif child.type == "trigger_event":
                events.append(self.node_text(child, source).strip())

        if not trigger_name:
            return

        sig = f"trigger {trigger_name}"
        if sobject_name:
            sig += f" on {sobject_name}"
        if events:
            sig += f" ({', '.join(events)})"

        symbols.append(self._make_symbol(
            name=trigger_name,
            kind="trigger",
            line_start=node.start_point[0] + 1,
            line_end=node.end_point[0] + 1,
            qualified_name=trigger_name,
            signature=sig,
            docstring=self.get_docstring(node, source),
            visibility="public",
            is_exported=True,
        ))

        # Emit a reference to the SObject the trigger is on
        if sobject_name:
            self._pending_inherits.append(self._make_reference(
                target_name=sobject_name,
                kind="call",
                line=node.start_point[0] + 1,
                source_name=trigger_name,
            ))

        # Walk trigger body for method calls etc.
        for child in node.children:
            if child.type == "trigger_body":
                self._walk_symbols(child, source, symbols, trigger_name)

    def _extract_enum(self, node, source, symbols, parent_name):
        name_node = node.child_by_field_name("name")
        if name_node is None:
            for child in node.children:
                if child.type == "identifier":
                    name_node = child
                    break
        if name_node is None:
            return

        name = self.node_text(name_node, source)
        vis = self._get_visibility(node, source)
        qualified = f"{parent_name}.{name}" if parent_name else name
        sig = f"enum {name}"

        symbols.append(self._make_symbol(
            name=name,
            kind="enum",
            line_start=node.start_point[0] + 1,
            line_end=node.end_point[0] + 1,
            qualified_name=qualified,
            signature=sig,
            docstring=self.get_docstring(node, source),
            visibility=vis,
            is_exported=(vis == "public"),
            parent_name=parent_name,
        ))

        # Walk enum body for constants
        body = node.child_by_field_name("body")
        if body:
            for child in body.children:
                if child.type == "enum_constant":
                    cn = child.child_by_field_name("name")
                    if cn is None:
                        for sub in child.children:
                            if sub.type == "identifier":
                                cn = sub
                                break
                    if cn:
                        const_name = self.node_text(cn, source)
                        symbols.append(self._make_symbol(
                            name=const_name,
                            kind="constant",
                            line_start=child.start_point[0] + 1,
                            line_end=child.end_point[0] + 1,
                            qualified_name=f"{qualified}.{const_name}",
                            parent_name=qualified,
                            visibility=vis,
                            is_exported=(vis == "public"),
                        ))

    def _extract_method(self, node, source, symbols, parent_name):
        name_node = node.child_by_field_name("name")
        if name_node is None:
            for child in node.children:
                if child.type == "identifier":
                    name_node = child
                    break
        if name_node is None:
            return

        name = self.node_text(name_node, source)
        vis = self._get_visibility(node, source)
        annotations = self._get_annotations(node, source)

        # Build signature
        ret_type = node.child_by_field_name("type")
        params = node.child_by_field_name("parameters")
        type_params = node.child_by_field_name("type_parameters")

        sig = ""
        if type_params:
            sig += self.node_text(type_params, source) + " "
        if ret_type:
            sig += self.node_text(ret_type, source) + " "
        else:
            # Check for void_type child
            for child in node.children:
                if child.type == "void_type":
                    sig += "void "
                    break
        sig += f"{name}({self._params_text(params, source)})"

        if self._has_modifier(node, source, "static"):
            sig = "static " + sig

        if annotations:
            sig = "\n".join(annotations) + "\n" + sig

        qualified = f"{parent_name}.{name}" if parent_name else name
        symbols.append(self._make_symbol(
            name=name,
            kind="method",
            line_start=node.start_point[0] + 1,
            line_end=node.end_point[0] + 1,
            qualified_name=qualified,
            signature=sig,
            docstring=self.get_docstring(node, source),
            visibility=vis,
            is_exported=(vis == "public"),
            parent_name=parent_name,
        ))

    def _extract_constructor(self, node, source, symbols, parent_name):
        name_node = node.child_by_field_name("name")
        if name_node is None:
            for child in node.children:
                if child.type == "identifier":
                    name_node = child
                    break
        if name_node is None:
            return

        name = self.node_text(name_node, source)
        vis = self._get_visibility(node, source)
        params = node.child_by_field_name("parameters")
        sig = f"{name}({self._params_text(params, source)})"

        qualified = f"{parent_name}.{name}" if parent_name else name
        symbols.append(self._make_symbol(
            name=name,
            kind="constructor",
            line_start=node.start_point[0] + 1,
            line_end=node.end_point[0] + 1,
            qualified_name=qualified,
            signature=sig,
            docstring=self.get_docstring(node, source),
            visibility=vis,
            is_exported=(vis == "public"),
            parent_name=parent_name,
        ))

    def _extract_field(self, node, source, symbols, parent_name):
        """Extract field declarations, including Apex properties with accessor_list."""
        vis = self._get_visibility(node, source)
        type_node = node.child_by_field_name("type")
        type_text = self.node_text(type_node, source) if type_node else ""
        # Fallback: look for type_identifier child directly
        if not type_text:
            for child in node.children:
                if child.type in ("type_identifier", "generic_type", "boolean_type",
                                  "void_type", "scoped_type_identifier"):
                    type_text = self.node_text(child, source)
                    break

        is_static = self._has_modifier(node, source, "static")
        is_final = self._has_modifier(node, source, "final")
        has_accessor = any(child.type == "accessor_list" for child in node.children)

        for child in node.children:
            if child.type == "variable_declarator":
                name_node = child.child_by_field_name("name")
                if name_node is None:
                    for sub in child.children:
                        if sub.type == "identifier":
                            name_node = sub
                            break
                if name_node:
                    name = self.node_text(name_node, source)
                    if has_accessor:
                        kind = "property"
                    elif is_static and is_final:
                        kind = "constant"
                    else:
                        kind = "field"

                    sig = f"{type_text} {name}"
                    if is_static:
                        sig = "static " + sig
                    if is_final:
                        sig = "final " + sig
                    if has_accessor:
                        # Summarize accessors
                        accessor_parts = []
                        for ac in node.children:
                            if ac.type == "accessor_list":
                                for ad in ac.children:
                                    if ad.type == "accessor_declaration":
                                        accessor_parts.append(self.node_text(ad, source).split("{")[0].strip().rstrip(";").strip())
                        if accessor_parts:
                            sig += " { " + "; ".join(accessor_parts) + " }"

                    qualified = f"{parent_name}.{name}" if parent_name else name
                    symbols.append(self._make_symbol(
                        name=name,
                        kind=kind,
                        line_start=node.start_point[0] + 1,
                        line_end=node.end_point[0] + 1,
                        qualified_name=qualified,
                        signature=sig,
                        visibility=vis,
                        is_exported=(vis == "public"),
                        parent_name=parent_name,
                    ))

    def _collect_type_refs(self, node, source, kind, line, source_name):
        """Recursively collect type_identifier nodes as references."""
        for child in node.children:
            if child.type == "type_identifier":
                self._pending_inherits.append(self._make_reference(
                    target_name=self.node_text(child, source),
                    kind=kind,
                    line=line,
                    source_name=source_name,
                ))
            else:
                self._collect_type_refs(child, source, kind, line, source_name)

    # ------------------------------------------------------------------ #
    #  Reference extraction                                               #
    # ------------------------------------------------------------------ #

    def _walk_refs(self, node, source: bytes, refs: list[dict], scope_name):
        for child in node.children:
            if child.type == "method_invocation":
                self._extract_method_call(child, source, refs, scope_name)
            elif child.type == "object_creation_expression":
                self._extract_new(child, source, refs, scope_name)
            elif child.type == "dml_expression":
                self._extract_dml(child, source, refs, scope_name)
            elif child.type == "field_access":
                self._extract_field_access(child, source, refs, scope_name)
            else:
                new_scope = scope_name
                if child.type in ("class_declaration", "interface_declaration", "enum_declaration"):
                    n = child.child_by_field_name("name")
                    if n is None:
                        for sub in child.children:
                            if sub.type == "identifier":
                                n = sub
                                break
                    if n:
                        cname = self.node_text(n, source)
                        new_scope = f"{scope_name}.{cname}" if scope_name else cname
                elif child.type == "trigger_declaration":
                    for sub in child.children:
                        if sub.type == "identifier":
                            new_scope = self.node_text(sub, source)
                            break
                elif child.type in ("method_declaration", "constructor_declaration"):
                    n = child.child_by_field_name("name")
                    if n is None:
                        for sub in child.children:
                            if sub.type == "identifier":
                                n = sub
                                break
                    if n:
                        mname = self.node_text(n, source)
                        new_scope = f"{scope_name}.{mname}" if scope_name else mname
                self._walk_refs(child, source, refs, new_scope)

    def _extract_method_call(self, node, source, refs, scope_name):
        name_node = node.child_by_field_name("name")
        obj_node = node.child_by_field_name("object")
        if name_node is None:
            return
        name = self.node_text(name_node, source)
        if obj_node:
            name = f"{self.node_text(obj_node, source)}.{name}"

        refs.append(self._make_reference(
            target_name=name,
            kind="call",
            line=node.start_point[0] + 1,
            source_name=scope_name,
        ))
        # Recurse into arguments
        args = node.child_by_field_name("arguments")
        if args:
            self._walk_refs(args, source, refs, scope_name)

    def _extract_new(self, node, source, refs, scope_name):
        type_node = node.child_by_field_name("type")
        if type_node:
            name = self.node_text(type_node, source)
            refs.append(self._make_reference(
                target_name=name,
                kind="call",
                line=node.start_point[0] + 1,
                source_name=scope_name,
            ))
        args = node.child_by_field_name("arguments")
        if args:
            self._walk_refs(args, source, refs, scope_name)

    def _extract_dml(self, node, source, refs, scope_name):
        """Extract DML operations (insert, update, delete, upsert, undelete, merge)."""
        text = self.node_text(node, source).strip()
        op = text.split()[0] if text else ""
        if op:
            refs.append(self._make_reference(
                target_name=f"DML.{op}",
                kind="call",
                line=node.start_point[0] + 1,
                source_name=scope_name,
            ))

    def _extract_field_access(self, node, source, refs, scope_name):
        """Extract Trigger.isInsert / Trigger.new style references."""
        text = self.node_text(node, source)
        if text.startswith("Trigger."):
            refs.append(self._make_reference(
                target_name=text,
                kind="call",
                line=node.start_point[0] + 1,
                source_name=scope_name,
            ))
