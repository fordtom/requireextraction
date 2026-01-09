# reqif Library Bug Reports

## Bug 1: REAL attribute DEFAULT-VALUE handler looks for INTEGER

**File:** `reqif/parsers/attribute_definition_parser.py`
**Lines:** 106-112

**Description:**
The `ATTRIBUTE-DEFINITION-REAL` handler incorrectly looks for `ATTRIBUTE-VALUE-INTEGER` instead of `ATTRIBUTE-VALUE-REAL` when parsing default values.

**Code (lines 95-112):**
```python
elif attribute_definition.tag == "ATTRIBUTE-DEFINITION-REAL":
    attribute_type = SpecObjectAttributeType.REAL
    # ... datatype parsing ...

    xml_default_value = attribute_definition.find("DEFAULT-VALUE")
    if xml_default_value is not None:
        xml_attribute_value = xml_default_value.find(
            "ATTRIBUTE-VALUE-INTEGER"  # BUG: Should be "ATTRIBUTE-VALUE-REAL"
        )
        assert xml_attribute_value is not None
        default_value = xml_attribute_value.attrib["THE-VALUE"]
```

**Fix:**
Change line 109 from `"ATTRIBUTE-VALUE-INTEGER"` to `"ATTRIBUTE-VALUE-REAL"`.

**Impact:**
Any ReqIF file with a REAL-typed attribute that has a DEFAULT-VALUE will fail to parse with an AssertionError.

**Affected file:** `examples/collected/capella/Sample3.reqif` (IBM Rational DOORS export)

---

## Bug 2: DEFAULT-VALUE handler crashes when THE-VALUE is an attribute

**File:** `reqif/parsers/attribute_definition_parser.py`
**Lines:** 168-169

**Description:**
When parsing XHTML attribute definitions with DEFAULT-VALUE, the code assumes THE-VALUE is a child element. However, some tools (e.g., fmStudio/ProR) export THE-VALUE as an XML attribute on the value element itself.

**Expected format (child element):**
```xml
<DEFAULT-VALUE>
  <ATTRIBUTE-VALUE-XHTML>
    <THE-VALUE><div>content</div></THE-VALUE>
  </ATTRIBUTE-VALUE-XHTML>
</DEFAULT-VALUE>
```

**Actual format from some tools (attribute):**
```xml
<DEFAULT-VALUE>
  <ATTRIBUTE-VALUE-XHTML THE-VALUE="xhtml string"/>
</DEFAULT-VALUE>
```

**Code (lines 168-169):**
```python
xml_values = xml_attribute_value.find("THE-VALUE")  # Returns None when THE-VALUE is an attribute
default_value = lxml_stringify_namespaced_children(xml_values)  # Crashes: 'NoneType' has no attribute 'nsmap'
```

**Fix:**
Check if THE-VALUE exists as a child element; if not, check for it as an attribute:
```python
xml_values = xml_attribute_value.find("THE-VALUE")
if xml_values is not None:
    default_value = lxml_stringify_namespaced_children(xml_values)
elif "THE-VALUE" in xml_attribute_value.attrib:
    default_value = xml_attribute_value.attrib["THE-VALUE"]
else:
    default_value = None
```

**Impact:**
Files from fmStudio, ProR, and potentially other tools fail to parse with `AttributeError: 'NoneType' object has no attribute 'nsmap'`.

**Affected files:**
- `Datatype-Demo.reqif` (fmStudio/ProR)
- `Datatype-Demo-XhtML-Fault.reqif` (fmStudio/ProR)

---

## Feature Gap 1: SPEC-RELATION attribute types limited to XHTML/ENUMERATION/STRING/INTEGER

**File:** `reqif/parsers/spec_relation_parser.py`
**Lines:** 40-84

**Description:**
The `SpecRelationParser` only handles four attribute value types in SPEC-RELATIONS:
- ATTRIBUTE-VALUE-XHTML
- ATTRIBUTE-VALUE-ENUMERATION
- ATTRIBUTE-VALUE-STRING
- ATTRIBUTE-VALUE-INTEGER

Any other type (BOOLEAN, DATE, REAL) raises `NotImplementedError`.

**Implementation complexity:** Low-Medium

The existing code structure is clear - each type has a ~10 line handler block. Adding BOOLEAN/DATE/REAL would follow the same pattern as INTEGER:

```python
elif xml_value.tag == "ATTRIBUTE-VALUE-BOOLEAN":
    attribute_value = xml_value.attrib["THE-VALUE"]
    definition_ref = xml_value[0][0].text
    values_attribute = SpecObjectAttribute(
        xml_node=xml_value,
        attribute_type=SpecObjectAttributeType.BOOLEAN,
        definition_ref=definition_ref,
        value=attribute_value,
    )
```

The same pattern would work for DATE and REAL. Estimate: ~30 lines of code, straightforward copy-paste with type changes.

**Affected file:** `output.reqif` (CDP4 tool)

---

## Feature Gap 2: DEFAULT-VALUE handling incomplete across all attribute types

**File:** `reqif/parsers/attribute_definition_parser.py`

**Description:**
DEFAULT-VALUE parsing is inconsistent across attribute types. Some types handle it, others don't, and the XHTML handler has the bug described above. A comprehensive review of DEFAULT-VALUE handling for all attribute types would improve robustness.

**Implementation complexity:** Medium

Would require:
1. Audit all attribute type handlers for DEFAULT-VALUE support
2. Standardize the pattern for extracting default values
3. Handle both child-element and attribute forms of THE-VALUE
4. Add appropriate fallbacks when DEFAULT-VALUE is present but malformed

This is more of a robustness improvement than a single fix.
