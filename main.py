"""ReqIF parser with StrictDoc normalization and automatic workarounds.

Pipeline:
1. Try StrictDoc conversion
2. On known failures, pre-process bundle to fix issues
3. Retry StrictDoc conversion
4. Maximize acceptance while maintaining normalized output
"""

import copy
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

from reqif.parser import ReqIFParser, ReqIFZParser
from reqif.models.reqif_types import SpecObjectAttributeType
from strictdoc.backend.reqif.p01_sdoc.reqif_to_sdoc_converter import (
    P01_ReqIFToSDocConverter,
)
from strictdoc.backend.reqif.sdoc_reqif_fields import (
    map_reqif_field_title_to_sdoc_field_title,
)
from strictdoc.export.json.json_generator import JSONGenerator


@dataclass
class ConversionResult:
    success: bool
    data: Optional[dict] = None
    error: Optional[str] = None
    workarounds_applied: List[str] = field(default_factory=list)


def preprocess_reqif_xml(content: str) -> str:
    """Preprocess ReqIF XML to handle common issues."""
    # Strip BOM if present
    if content.startswith('\ufeff'):
        content = content[1:]

    # Strip content before XML declaration
    xml_decl_match = re.search(r"<\?xml[^?]*\?>", content)
    if xml_decl_match:
        content = content[xml_decl_match.start():]

    # Strip namespace prefix from all ReqIF elements (reqif:ELEMENT -> ELEMENT)
    # Common prefixes: reqif, r
    # This also handles root element: <reqif:REQ-IF> -> <REQ-IF>
    content = re.sub(r'<(reqif|r):([A-Z])', r'<\2', content)
    content = re.sub(r'</(reqif|r):([A-Z])', r'</\2', content)

    # Convert prefixed xmlns to default xmlns ONLY if no default xmlns exists
    # <REQ-IF xmlns:reqif="..."> -> <REQ-IF xmlns="...">
    # But skip if file already has xmlns="..." to avoid duplicates
    if not re.search(r'<REQ-IF[^>]+xmlns="', content):
        content = re.sub(r'xmlns:(reqif|r)=', 'xmlns=', content)
    else:
        # Remove redundant prefixed xmlns declarations if default already exists
        content = re.sub(r'\s+xmlns:(reqif|r)="[^"]*"', '', content)

    return content


def fix_unsupported_attribute_types(bundle) -> List[str]:
    """Convert BOOLEAN and REAL attributes to STRING type in-place.

    StrictDoc doesn't support BOOLEAN or REAL types. Convert them to STRING.
    Returns list of field names that were converted.
    """
    fixed_fields = []
    unsupported_types = {
        SpecObjectAttributeType.BOOLEAN,
        SpecObjectAttributeType.REAL,
        SpecObjectAttributeType.INTEGER,
        SpecObjectAttributeType.DATE,
    }

    try:
        content = bundle.core_content.req_if_content
        if not content or not content.spec_types:
            return fixed_fields

        for spec_type in content.spec_types:
            if not hasattr(spec_type, "attribute_definitions"):
                continue
            if not spec_type.attribute_definitions:
                continue

            for attr in spec_type.attribute_definitions:
                if attr.attribute_type in unsupported_types:
                    fixed_fields.append(f"{attr.long_name}:{attr.attribute_type.name}")
                    attr.attribute_type = SpecObjectAttributeType.STRING

        # Also fix the actual attribute values in spec objects
        if content.spec_objects:
            for spec_obj in content.spec_objects:
                if not spec_obj.attributes:
                    continue
                for attr in spec_obj.attributes:
                    if attr.attribute_type in unsupported_types:
                        attr.attribute_type = SpecObjectAttributeType.STRING
                        # Convert value to string
                        if attr.value is not None:
                            if isinstance(attr.value, bool):
                                attr.value = "true" if attr.value else "false"
                            elif not isinstance(attr.value, str):
                                attr.value = str(attr.value)

    except Exception:
        pass

    return fixed_fields


def fix_missing_spec_type_names(bundle) -> List[str]:
    """Add default names to spec types with missing long_name.

    Returns list of spec type identifiers that were fixed.
    """
    fixed_types = []

    try:
        content = bundle.core_content.req_if_content
        if not content or not content.spec_types:
            return fixed_types

        for spec_type in content.spec_types:
            if not hasattr(spec_type, "long_name"):
                continue
            if spec_type.long_name is None or spec_type.long_name.strip() == "":
                # Use identifier as fallback name
                spec_type.long_name = spec_type.identifier or "REQUIREMENT"
                fixed_types.append(spec_type.identifier)

    except Exception:
        pass

    return fixed_types


def fix_duplicate_field_names(bundle) -> List[str]:
    """Rename duplicate field names by adding suffix.

    Uses StrictDoc's actual field mapping to detect collisions.
    Returns list of fields that were renamed.
    """
    renamed_fields = []

    try:
        content = bundle.core_content.req_if_content
        if not content or not content.spec_types:
            return renamed_fields

        for spec_type in content.spec_types:
            if not hasattr(spec_type, "attribute_definitions"):
                continue
            if not spec_type.attribute_definitions:
                continue

            seen_names = {}
            for attr in spec_type.attribute_definitions:
                # Use StrictDoc's mapping to get the actual normalized name
                mapped_name = map_reqif_field_title_to_sdoc_field_title(attr.long_name)
                # Then apply StrictDoc's safe name transformation
                safe_name = mapped_name.upper().replace(".", "_").replace("-", "_")
                safe_name = re.sub(r"[^A-Za-z0-9_]", "", safe_name)

                if safe_name in seen_names:
                    # Rename with suffix to avoid collision
                    count = seen_names[safe_name] + 1
                    seen_names[safe_name] = count
                    old_name = attr.long_name
                    # Rename the original field name (not the mapped one)
                    attr.long_name = f"{attr.long_name}_{count}"
                    renamed_fields.append(f"{old_name} -> {attr.long_name}")
                else:
                    seen_names[safe_name] = 1

    except Exception:
        pass

    return renamed_fields


def fix_empty_attribute_values(bundle) -> List[str]:
    """Remove attributes with empty values that StrictDoc can't handle.

    Returns list of spec object identifiers that had empty values removed.
    """
    fixed_objects = []

    try:
        content = bundle.core_content.req_if_content
        if not content or not content.spec_objects:
            return fixed_objects

        for spec_obj in content.spec_objects:
            if not spec_obj.attributes:
                continue

            # Filter out empty string attributes
            original_count = len(spec_obj.attributes)
            spec_obj.attributes = [
                attr for attr in spec_obj.attributes
                if attr.value is not None and (
                    not isinstance(attr.value, str) or attr.value.strip() != ''
                )
            ]
            if len(spec_obj.attributes) < original_count:
                fixed_objects.append(spec_obj.identifier)

    except Exception:
        pass

    return fixed_objects


def fix_missing_spec_object_refs(bundle) -> List[str]:
    """Remove hierarchy nodes and relations that reference missing spec objects.

    Returns list of removed items.
    """
    removed_items = []

    try:
        content = bundle.core_content.req_if_content
        if not content:
            return removed_items

        # Build set of valid spec object identifiers
        valid_refs = set()
        if content.spec_objects:
            for so in content.spec_objects:
                valid_refs.add(so.identifier)

        # Filter hierarchy nodes
        if content.specifications:
            for spec in content.specifications:
                if not spec.children:
                    continue

                def filter_valid_nodes(nodes):
                    valid_nodes = []
                    for node in nodes:
                        if node.spec_object in valid_refs:
                            if node.children:
                                node.children = filter_valid_nodes(node.children)
                            valid_nodes.append(node)
                        else:
                            removed_items.append(f"hierarchy:{node.identifier}")
                    return valid_nodes

                spec.children = filter_valid_nodes(spec.children)

        # Build set of valid spec type identifiers
        valid_type_refs = set()
        if content.spec_types:
            for st in content.spec_types:
                valid_type_refs.add(st.identifier)

        # Filter spec relations with missing source/target or missing type
        if content.spec_relations:
            original_count = len(content.spec_relations)
            valid_relations = []
            for rel in content.spec_relations:
                # Check source and target exist
                if rel.source not in valid_refs or rel.target not in valid_refs:
                    continue
                # Check relation type exists (if specified)
                if hasattr(rel, 'relation_type_ref') and rel.relation_type_ref:
                    if rel.relation_type_ref not in valid_type_refs:
                        continue
                valid_relations.append(rel)
            content.spec_relations = valid_relations
            removed_count = original_count - len(content.spec_relations)
            if removed_count > 0:
                removed_items.append(f"relations:{removed_count}")

        # Rebuild the lookup's parent mapping from the cleaned relations
        # This ensures consistency between spec_relations and the lookup
        if hasattr(bundle, 'lookup') and hasattr(bundle.lookup, 'spec_relations_parent_lookup'):
            bundle.lookup.spec_relations_parent_lookup.clear()
            if content.spec_relations:
                for rel in content.spec_relations:
                    if rel.source not in bundle.lookup.spec_relations_parent_lookup:
                        bundle.lookup.spec_relations_parent_lookup[rel.source] = []
                    bundle.lookup.spec_relations_parent_lookup[rel.source].append(rel.target)

    except Exception:
        pass

    return removed_items


def apply_workarounds(bundle) -> List[str]:
    """Apply all known workarounds to a bundle.

    Returns list of workarounds applied.
    """
    workarounds = []

    # Fix unsupported attribute types (BOOLEAN, REAL, INTEGER, DATE)
    type_fixes = fix_unsupported_attribute_types(bundle)
    if type_fixes:
        workarounds.append(f"Converted unsupported types to STRING: {', '.join(type_fixes)}")

    # Fix missing spec type names
    name_fixes = fix_missing_spec_type_names(bundle)
    if name_fixes:
        workarounds.append(f"Added default names to {len(name_fixes)} spec types")

    # Fix duplicate field names
    dup_fixes = fix_duplicate_field_names(bundle)
    if dup_fixes:
        workarounds.append(f"Renamed duplicate fields: {', '.join(dup_fixes)}")

    # Fix empty attribute values
    empty_fixes = fix_empty_attribute_values(bundle)
    if empty_fixes:
        workarounds.append(f"Removed empty attributes from {len(empty_fixes)} objects")

    # Fix missing references in hierarchy and relations
    ref_fixes = fix_missing_spec_object_refs(bundle)
    if ref_fixes:
        workarounds.append(f"Removed invalid references: {', '.join(ref_fixes)}")

    return workarounds


def convert_bundle_to_json(bundle, workarounds_applied=None) -> ConversionResult:
    """Convert a ReqIF bundle to StrictDoc JSON format."""
    if workarounds_applied is None:
        workarounds_applied = []

    try:
        sdoc_documents = P01_ReqIFToSDocConverter.convert_reqif_bundle(
            bundle,
            enable_mid=False,
            import_markup="HTML",
        )

        if not sdoc_documents:
            return ConversionResult(
                success=False,
                error="No specifications found in ReqIF file",
            )

        # Build JSON output
        result = {
            "_COMMENT": "Normalized via StrictDoc.",
            "DOCUMENTS": [],
        }

        if workarounds_applied:
            result["_WORKAROUNDS_APPLIED"] = workarounds_applied

        for doc in sdoc_documents:
            doc_dict = JSONGenerator._write_document(doc)
            result["DOCUMENTS"].append(doc_dict)

        return ConversionResult(
            success=True,
            data=result,
            workarounds_applied=workarounds_applied,
        )

    except Exception as e:
        return ConversionResult(
            success=False,
            error=str(e)[:500],
            workarounds_applied=workarounds_applied,
        )


def convert_reqif_to_json(bundle) -> ConversionResult:
    """Convert ReqIF bundle to JSON with automatic workarounds.

    Strategy:
    1. Try direct conversion
    2. If fails, apply workarounds and retry
    """
    # First attempt: direct conversion
    result = convert_bundle_to_json(bundle)
    if result.success:
        return result

    # Second attempt: apply workarounds and retry
    workarounds = apply_workarounds(bundle)

    if workarounds:
        result = convert_bundle_to_json(bundle, workarounds)
        if result.success:
            return result

    # Still failed - return error with context
    return ConversionResult(
        success=False,
        error=result.error,
        workarounds_applied=workarounds,
    )


def process_reqif_file(file_path, preprocess=True) -> ConversionResult:
    """Process a ReqIF file with automatic workarounds."""
    try:
        if preprocess:
            with open(file_path, "r", encoding="utf-8") as f:
                content = f.read()
            content = preprocess_reqif_xml(content)
            bundle = ReqIFParser.parse_from_string(content)
        else:
            bundle = ReqIFParser.parse(file_path)

        return convert_reqif_to_json(bundle)

    except Exception as e:
        return ConversionResult(
            success=False,
            error=f"Parse error: {str(e)[:300]}",
        )


def process_reqifz_file(file_path, output_dir=None) -> ConversionResult:
    """Process a ReqIFZ bundle with automatic workarounds."""
    try:
        file_path = Path(file_path)

        if output_dir is None:
            output_dir = file_path.parent / f"{file_path.stem}_output"
        else:
            output_dir = Path(output_dir)

        z_bundle = ReqIFZParser.parse(str(file_path))

        all_documents = []
        all_workarounds = []
        extracted_attachments = []
        errors = []

        for bundle_name, bundle in z_bundle.reqif_bundles.items():
            result = convert_reqif_to_json(bundle)

            if result.workarounds_applied:
                all_workarounds.extend(
                    f"[{bundle_name}] {w}" for w in result.workarounds_applied
                )

            if result.success and result.data:
                for doc in result.data.get("DOCUMENTS", []):
                    doc["_SOURCE_FILE"] = bundle_name
                    all_documents.append(doc)
            else:
                errors.append(f"[{bundle_name}] {result.error}")

        # Extract attachments
        if z_bundle.attachments:
            attachments_dir = output_dir / "attachments"
            attachments_dir.mkdir(parents=True, exist_ok=True)

            for attachment_name, attachment_data in z_bundle.attachments.items():
                if not attachment_data or attachment_name.endswith("/"):
                    continue

                attachment_path = attachments_dir / attachment_name
                attachment_path.parent.mkdir(parents=True, exist_ok=True)

                with open(attachment_path, "wb") as f:
                    f.write(attachment_data)

                extracted_attachments.append(
                    str(attachment_path.relative_to(output_dir))
                )

        if all_documents:
            data = {
                "_COMMENT": "Normalized via StrictDoc.",
                "DOCUMENTS": all_documents,
                "ATTACHMENTS": extracted_attachments,
            }
            if all_workarounds:
                data["_WORKAROUNDS_APPLIED"] = all_workarounds
            if errors:
                data["_PARTIAL_ERRORS"] = errors

            return ConversionResult(
                success=True,
                data=data,
                workarounds_applied=all_workarounds,
            )
        else:
            return ConversionResult(
                success=False,
                error="; ".join(errors) if errors else "No documents found",
                workarounds_applied=all_workarounds,
            )

    except Exception as e:
        return ConversionResult(
            success=False,
            error=f"Archive error: {str(e)[:300]}",
        )


def count_nodes(nodes):
    """Recursively count nodes in the tree."""
    count = len(nodes)
    for node in nodes:
        if "NODES" in node:
            count += count_nodes(node["NODES"])
    return count


def process_file(file_path):
    """Process a ReqIF or ReqIFZ file."""
    file_path = Path(file_path)

    if not file_path.exists():
        print(f"File not found: {file_path}")
        return None

    extension = file_path.suffix.lower()

    if extension == ".reqifz":
        output_dir = file_path.parent / f"{file_path.stem}_output"
        result = process_reqifz_file(file_path, output_dir)
        if result.success:
            output_file = output_dir / f"{file_path.stem}_sdoc.json"
            output_dir.mkdir(parents=True, exist_ok=True)
        else:
            output_file = None
    elif extension == ".reqif":
        result = process_reqif_file(file_path)
        output_file = Path(str(file_path).replace(".reqif", "_sdoc.json")) if result.success else None
    else:
        print(f"Unsupported file type: {extension}")
        return None

    # Output results
    if result.success:
        with open(output_file, "w") as f:
            json.dump(result.data, f, indent=2, default=str)

        docs = len(result.data.get("DOCUMENTS", []))
        nodes = sum(count_nodes(doc.get("NODES", [])) for doc in result.data.get("DOCUMENTS", []))

        print(f"✓ {file_path.name}: {docs} docs, {nodes} nodes")
        if result.workarounds_applied:
            print(f"  Workarounds: {len(result.workarounds_applied)}")
    else:
        print(f"✗ {file_path.name}: {result.error[:80]}")

    return result


if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1:
        for file_arg in sys.argv[1:]:
            process_file(file_arg)
    else:
        test_file = Path("examples/reqif_testfile.reqif")
        if test_file.exists():
            process_file(test_file)
