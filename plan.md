# Plan: Close Salesforce Metadata Coverage Gap (61% → ~95%)

## Problem Summary

Benchmark shows 61% coverage after initial Apex/sfxml parsers. Three gaps remain:

1. **LWC→Apex import edges not wired** — `@salesforce/apex/*` imports are extracted as references but never resolve to Apex symbols because the resolution logic treats them as file paths
2. **XML reference extraction is coarse** — only 8 tag types checked; misses field refs in layouts/profiles, flow→apex actions, formula references, etc.
3. **~39% of files unparsed** — Aura components (.cmp/.app/.evt/.intf), Visualforce (.page/.component), and plain .xml files have no language mapping or extractor

## Phase 1: Wire LWC → Apex cross-language edges

**Files:** `src/roam/index/relations.py`, `src/roam/languages/javascript_lang.py`

### 1a. Tag `@salesforce/*` imports with structured metadata in JS extractor

In `javascript_lang.py` `_extract_esm_import()`, detect `@salesforce/` import paths and enrich the reference dict:

```python
# After extracting import_path:
if path.startswith("@salesforce/apex/"):
    # "@salesforce/apex/AccountHandler.getAccounts" → class="AccountHandler", method="getAccounts"
    apex_ref = path[len("@salesforce/apex/"):]  # "AccountHandler.getAccounts"
    parts = apex_ref.split(".", 1)
    ref["sf_class"] = parts[0]                   # "AccountHandler"
    ref["sf_method"] = parts[1] if len(parts) > 1 else None
    ref["target_name"] = apex_ref                 # Override to "AccountHandler.getAccounts"
elif path.startswith("@salesforce/schema/"):
    schema_ref = path[len("@salesforce/schema/"):]
    ref["target_name"] = schema_ref               # "Account.Name" or "Account"
elif path.startswith("@salesforce/label/"):
    label_ref = path[len("@salesforce/label/"):]
    if label_ref.startswith("c."):
        ref["target_name"] = label_ref[2:]        # Strip "c." namespace prefix
```

This makes `target_name` match the `qualified_name` that the Apex extractor already produces (e.g., `AccountHandler.getAccounts`).

### 1b. Add Salesforce-aware path matching in `relations.py`

Add a new function `_resolve_salesforce_import()` and call it from `_match_import_path()`:

```python
def _resolve_salesforce_import(import_path: str, candidates: list[dict]) -> list[dict]:
    """Resolve @salesforce/* import paths to Apex/metadata symbols."""
    if not import_path.startswith("@salesforce/"):
        return []
    if import_path.startswith("@salesforce/apex/"):
        apex_ref = import_path[len("@salesforce/apex/"):]
        parts = apex_ref.split(".", 1)
        class_name = parts[0]
        # Match candidates whose file_path ends with the Apex class file
        return [c for c in candidates
                if c.get("file_path", "").endswith(f"/{class_name}.cls")
                or c.get("file_path", "").endswith(f"/{class_name}.trigger")]
    if import_path.startswith("@salesforce/schema/"):
        schema_ref = import_path[len("@salesforce/schema/"):]
        # Match field or object symbols from sfxml metadata
        return [c for c in candidates
                if c.get("qualified_name", "") == schema_ref
                or c.get("name", "") == schema_ref.split(".")[-1]]
    return []
```

Hook it into `_match_import_path()` at the top:

```python
def _match_import_path(import_path, candidates):
    sf_matches = _resolve_salesforce_import(import_path, candidates)
    if sf_matches:
        return sf_matches
    # ... existing logic
```

### 1c. Also handle dotted target_name lookup

In `resolve_references()`, when `target_name` contains a dot (e.g., `AccountHandler.getAccounts`), also try looking up the qualified name directly in `symbols_by_qualified`:

Already handled on line 84: `qn_matches = symbols_by_qualified.get(target_name, [])`. Since the Apex extractor produces `qualified_name = "AccountHandler.getAccounts"`, this will match once we set `target_name` correctly in step 1a.

---

## Phase 2: Expand XML metadata reference extraction

**File:** `src/roam/languages/sfxml_lang.py`

### 2a. Expand ref_tags to cover all cross-reference points

Replace the current 8-tag `ref_tags` set with a comprehensive one:

```python
# Tags whose text content references another metadata entity
_REF_TAGS = {
    # Apex references
    "apexClass", "class", "apexPage", "apexComponent", "apexTrigger",
    "triggerType", "template",
    # Object/field references
    "customObject", "object", "field", "fieldName",
    "referenceTo", "relatedList", "relationshipName",
    "lookupFilter",
    # Automation references
    "actionName", "flowName", "processType",
    "workflowAction", "targetWorkflow",
    # Layout references
    "recordType", "layoutItems",
    # Permission references (field is already listed above)
    # Generic cross-references
    "name",  # only when inside specific parent contexts (see 2b)
}
```

### 2b. Add context-aware reference extraction

Instead of blindly treating all matching tags as references, check parent context:

```python
def _walk_xml_refs(self, node, source, refs, file_path, parent_tag=None):
    if node.type == "element":
        tag_name = self._get_element_tag(node, source)

        # Always-reference tags
        if tag_name in _ALWAYS_REF_TAGS:
            text = self._get_element_text(node, source)
            if text:
                refs.append(self._make_reference(
                    target_name=text, kind="reference",
                    line=node.start_point[0] + 1))

        # Context-dependent: <field> inside permission/layout contexts
        if tag_name == "field" and parent_tag in (
            "fieldPermissions", "layoutItems", "columns",
            "WorkflowFieldUpdate", "sortField",
        ):
            text = self._get_element_text(node, source)
            if text:
                # "Account.Industry__c" → reference to Industry__c
                refs.append(self._make_reference(
                    target_name=text.split(".")[-1] if "." in text else text,
                    kind="reference",
                    line=node.start_point[0] + 1))

        # Recurse, passing current tag as parent context
        for child in node.children:
            self._walk_xml_refs(child, source, refs, file_path, parent_tag=tag_name)
```

### 2c. Parse formula references from validation rules and formula fields

```python
import re
_FORMULA_FIELD_RE = re.compile(r'\b([A-Z]\w+)\.([A-Za-z_]\w+__[cr])\b')

def _extract_formula_refs(self, formula_text, line, refs):
    """Extract Object.Field__c references from Salesforce formula syntax."""
    for m in _FORMULA_FIELD_RE.finditer(formula_text):
        refs.append(self._make_reference(
            target_name=m.group(2),  # Field API name
            kind="reference",
            line=line))
```

Call this when walking `<formula>` or `<formulaText>` elements.

---

## Phase 3: Add Aura and Visualforce file support

**Files:** `src/roam/index/parser.py`, `src/roam/languages/registry.py`, new `src/roam/languages/aura_lang.py`

### 3a. Map Aura/Visualforce extensions to the xml grammar

In both `parser.py` EXTENSION_MAP and `registry.py` _EXTENSION_MAP:

```python
# Aura components (XML-based)
".cmp": "aura",
".app": "aura",
".evt": "aura",
".intf": "aura",
".design": "aura",
# Visualforce (XML-based)
".page": "visualforce",
".component": "visualforce",
```

Add `"aura"` and `"visualforce"` to `_SUPPORTED_LANGUAGES`.

In `parse_file()`, map both to the `xml` grammar:

```python
if language in ("sfxml", "aura", "visualforce"):
    grammar_language = "xml"
```

### 3b. Create lightweight `AuraExtractor`

New file `src/roam/languages/aura_lang.py`:

Aura components are XML files with `<aura:component>`, `<aura:application>`, `<aura:event>`, `<aura:interface>` root elements. Extract:

- **Symbols:** component name (from filename), attributes (`<aura:attribute>`), handlers (`<aura:handler>`), methods (`<aura:method>`)
- **References:** controller/helper references (`controller="MyController"`), event references (`<aura:registerEvent>`, `<aura:handler event="c:MyEvent">`), component usage (`<c:ChildComponent>`)

### 3c. Create lightweight `VisualforceExtractor`

Visualforce pages use `<apex:page>`, `<apex:component>`, etc. Extract:

- **Symbols:** page/component name (from filename), custom controllers
- **References:** controller attribute (`controller="MyController"`), extensions, included components (`<apex:include>`, `<c:MyComponent>`)

### 3d. Register in `_create_extractor()`

```python
elif language == "aura":
    from .aura_lang import AuraExtractor
    return AuraExtractor()
elif language == "visualforce":
    from .visualforce_lang import VisualforceExtractor
    return VisualforceExtractor()
```

---

## Phase 4: Handle plain .xml files as Salesforce metadata

**File:** `src/roam/index/parser.py`, `src/roam/languages/registry.py`

### 4a. Route non-meta .xml files through sfxml when inside force-app/

Some Salesforce XML files don't use the `-meta.xml` suffix (e.g., `CustomLabels.labels`, older package.xml). Add path-based heuristics:

```python
def detect_language(file_path):
    if file_path.lower().endswith("-meta.xml"):
        return "sfxml"
    _, ext = os.path.splitext(file_path)
    if ext == ".xml":
        # Inside a Salesforce project structure → treat as sfxml
        if _is_salesforce_path(file_path):
            return "sfxml"
        return "xml"  # Generic XML parsing (no symbol extraction)
    return EXTENSION_MAP.get(ext)

def _is_salesforce_path(path):
    parts = path.lower().replace("\\", "/").split("/")
    sf_dirs = {"force-app", "src", "unpackaged", "metadata"}
    return bool(sf_dirs & set(parts))
```

### 4b. Handle extensionless Salesforce metadata

Some metadata types (older format) use compound extensions: `.labels`, `.workflow`, `.object`. Map these:

```python
".labels": "sfxml",
".workflow": "sfxml",
".object": "sfxml",
```

These also need the xml grammar routing in `parse_file()`.

---

## Phase 5: Tests

### 5a. LWC → Apex edge resolution test

```python
def test_lwc_apex_import_resolves(salesforce_project):
    """@salesforce/apex/ imports create graph edges to Apex classes."""
    out, rc = roam("deps", "force-app/main/default/lwc/accountList/accountList.js",
                   cwd=str(salesforce_project))
    assert "AccountHandler" in out
```

### 5b. XML reference expansion tests

- Validation rule formula → field reference
- Flow actionName → Apex class reference
- Profile fieldPermissions → field reference
- Layout field → field reference

### 5c. Aura component extraction tests

- `.cmp` file extracts component symbol + attributes
- `<aura:handler event="c:MyEvent">` creates event reference
- Controller references create edges to JS controller file

### 5d. Visualforce extraction tests

- `.page` file extracts page symbol + controller reference
- `<apex:include>` creates component reference

---

## Estimated Coverage Impact

| Phase | Change | Coverage Δ |
|-------|--------|-----------|
| Phase 1 | LWC→Apex edge wiring | +8-12% |
| Phase 2 | XML ref expansion | +5-8% |
| Phase 3 | Aura + VF extractors | +10-15% |
| Phase 4 | Plain .xml routing | +3-5% |
| **Total** | | **~87-95%** |

## Implementation Order

1. **Phase 1** first (highest value per effort — wires the cross-language graph)
2. **Phase 2** next (expands metadata→code edges, pure addition)
3. **Phase 3** (new extractors, medium effort, large file coverage gain)
4. **Phase 4** (edge case handling, low effort)

Phases 1 and 2 are independent and can be done in parallel. Phase 3 depends on the grammar routing changes in Phase 4, so they should be done together or Phase 4 first.
