"""
ReqIF to JSON converter using StrictDoc.

This script provides an alternative approach to main.py, using the strictdoc
package instead of the reqif package directly. StrictDoc converts ReqIF to its
own SDoc format first, then exports to JSON.

Benefits of this approach:
- StrictDoc handles tool-specific ReqIF quirks from different requirements tools
- Provides standardized output format
- Includes schema/grammar in the output
- Hierarchical JSON structure with TOC numbering

Tradeoffs:
- Loses ReqIF UUIDs (uses numeric IDs instead)
- Loses last_change timestamps
"""

import json
import re
from pathlib import Path
from typing import Any

from reqif.parser import ReqIFParser, ReqIFZParser

from strictdoc.backend.reqif.p01_sdoc.reqif_to_sdoc_converter import (
    P01_ReqIFToSDocConverter,
)
from strictdoc.backend.sdoc.models.document import SDocDocument
from strictdoc.backend.sdoc.models.node import SDocNode


def clean_html(html_str: str) -> str:
    """Remove HTML tags and clean whitespace from a string."""
    if not html_str:
        return ""
    text = re.sub(r"<[^>]+>", "", html_str)
    return " ".join(text.split())


def serialize_grammar(document: SDocDocument) -> dict[str, Any]:
    """Serialize the document grammar to a dictionary."""
    if not document.grammar:
        return {"ELEMENTS": []}

    elements = []
    for element in document.grammar.elements:
        element_dict = {
            "NODE_TYPE": element.tag,
            "FIELDS": [],
            "RELATIONS": [],
        }

        for field in element.fields:
            element_dict["FIELDS"].append({
                "TITLE": field.title,
                "REQUIRED": "True" if field.required else "False",
                "TYPE": "String",
            })

        for relation in element.relations:
            rel_dict = {"TYPE": relation.relation_type}
            if relation.relation_role:
                rel_dict["ROLE"] = relation.relation_role
            element_dict["RELATIONS"].append(rel_dict)

        elements.append(element_dict)

    return {"ELEMENTS": elements}


def serialize_node(
    node: SDocNode,
    document: SDocDocument,
    level_stack: tuple[int, ...],
) -> dict[str, Any]:
    """Serialize an SDocNode to a dictionary."""
    # Build level string for TOC
    level_str = ""
    if node.ng_resolved_custom_level and node.ng_resolved_custom_level != "None":
        level_str = node.ng_resolved_custom_level
    elif level_stack:
        level_str = ".".join(map(str, level_stack))

    node_dict: dict[str, Any] = {
        "_TOC": level_str,
        "_NODE_TYPE": node.node_type,
    }

    # Extract all fields from the node
    if document.grammar:
        element = document.grammar.elements_by_type.get(node.node_type)
        if element:
            for element_field in element.fields:
                field_name = element_field.title
                if field_name in node.ordered_fields_lookup:
                    fields = node.ordered_fields_lookup[field_name]
                    for field in fields:
                        node_dict[field_name] = field.get_text_value()

    # Handle composite nodes (sections with children)
    if node.is_composite and hasattr(node, 'section_contents'):
        node_dict["NODES"] = []
        current_number = 0
        for subnode in node.section_contents:
            if isinstance(subnode, SDocNode):
                if subnode.ng_resolved_custom_level is None:
                    current_number += 1
                child_dict = serialize_node(
                    subnode, document, level_stack + (current_number,)
                )
                node_dict["NODES"].append(child_dict)

    return node_dict


def serialize_document(document: SDocDocument) -> dict[str, Any]:
    """Serialize an SDocDocument to a dictionary."""
    doc_dict: dict[str, Any] = {
        "_NODE_TYPE": "DOCUMENT",
        "TITLE": document.title,
        "GRAMMAR": serialize_grammar(document),
        "NODES": [],
    }

    # Add options if present
    if document.config:
        options = {}
        if document.config.markup:
            options["MARKUP"] = document.config.markup
        if document.config.enable_mid is not None:
            options["ENABLE_MID"] = document.config.enable_mid
        if options:
            doc_dict["_OPTIONS"] = options

    # Serialize all top-level nodes
    current_number = 0
    for node in document.section_contents:
        if isinstance(node, SDocNode):
            if node.ng_resolved_custom_level is None:
                current_number += 1
            node_dict = serialize_node(node, document, (current_number,))
            doc_dict["NODES"].append(node_dict)

    return doc_dict


def flatten_document(doc_dict: dict) -> tuple[list, list]:
    """Flatten a document dictionary into requirements and links lists."""
    requirements = []
    links = []

    def process_nodes(nodes: list, parent_uid: str | None = None):
        for node in nodes:
            node_type = node.get("_NODE_TYPE", "")
            uid = node.get("UID", "")

            # Build requirement object
            req = {
                "id": uid,
                "name": node.get("TITLE", "") or clean_html(node.get("STATEMENT", "")),
                "type": node_type,
                "attributes": {}
            }

            # Extract all non-metadata fields as attributes
            for key, value in node.items():
                if not key.startswith("_") and key not in ("NODES", "UID"):
                    req["attributes"][key] = value

            # Add TOC if present
            toc = node.get("_TOC", "")
            if toc:
                req["toc"] = toc

            requirements.append(req)

            # Build link from parent to this node
            if parent_uid and uid:
                links.append({
                    "source": parent_uid,
                    "type": "hierarchy",
                    "target": uid
                })

            # Recursively process children
            child_nodes = node.get("NODES", [])
            if child_nodes:
                process_nodes(child_nodes, uid)

    process_nodes(doc_dict.get("NODES", []))
    return requirements, links


def process_reqif_file(file_path: str | Path) -> dict | None:
    """Process a ReqIF file using StrictDoc and return JSON output.

    Args:
        file_path: Path to the .reqif file

    Returns:
        Dict containing 'strictdoc' (native format) and 'flat' (requirements + links)
    """
    file_path = Path(file_path)

    if not file_path.exists():
        print(f"File not found: {file_path}")
        return None

    try:
        # Parse ReqIF using the reqif library
        bundle = ReqIFParser.parse(str(file_path))

        # Convert to SDoc using StrictDoc's converter
        converter = P01_ReqIFToSDocConverter()
        documents = converter.convert_reqif_bundle(
            bundle,
            enable_mid=False,
            import_markup="HTML",
        )

        if not documents:
            print(f"No documents found in {file_path}")
            return None

        # Serialize all documents
        strictdoc_json = {
            "_COMMENT": "Fields with _ are metadata. Fields without _ are the actual content.",
            "DOCUMENTS": []
        }

        all_requirements = []
        all_links = []

        for document in documents:
            doc_dict = serialize_document(document)
            strictdoc_json["DOCUMENTS"].append(doc_dict)

            # Flatten for comparison
            reqs, links = flatten_document(doc_dict)
            all_requirements.extend(reqs)
            all_links.extend(links)

        return {
            "strictdoc": strictdoc_json,
            "flat": {
                "requirements": all_requirements,
                "links": all_links
            }
        }

    except Exception as e:
        print(f"Error processing {file_path}: {e}")
        import traceback
        traceback.print_exc()
        return None


def process_reqifz_file(file_path: str | Path, output_dir: Path | None = None) -> dict | None:
    """Process a ReqIFZ bundle file using StrictDoc.

    Args:
        file_path: Path to the .reqifz file
        output_dir: Directory to extract attachments to

    Returns:
        Dict containing processed data, or None on failure
    """
    file_path = Path(file_path)

    if output_dir is None:
        output_dir = file_path.parent / f"{file_path.stem}_strictdoc_output"
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    try:
        # Parse ReqIFZ bundle
        z_bundle = ReqIFZParser.parse(str(file_path))

        all_results = {
            "strictdoc_documents": [],
            "flat": {
                "requirements": [],
                "links": []
            },
            "attachments": []
        }

        converter = P01_ReqIFToSDocConverter()

        # Process each ReqIF bundle
        for bundle_name, bundle in z_bundle.reqif_bundles.items():
            print(f"  Processing embedded: {bundle_name}")

            documents = converter.convert_reqif_bundle(
                bundle,
                enable_mid=False,
                import_markup="HTML",
            )

            for document in documents:
                doc_dict = serialize_document(document)
                doc_dict["_SOURCE_FILE"] = bundle_name
                all_results["strictdoc_documents"].append(doc_dict)

                reqs, links = flatten_document(doc_dict)
                for req in reqs:
                    req["source_file"] = bundle_name
                all_results["flat"]["requirements"].extend(reqs)
                all_results["flat"]["links"].extend(links)

        # Track attachments
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

                all_results["attachments"].append(str(attachment_path.relative_to(output_dir)))
                print(f"  Extracted attachment: {attachment_name}")

        return all_results

    except Exception as e:
        print(f"Error processing {file_path}: {e}")
        import traceback
        traceback.print_exc()
        return None


def process_file(file_path: str | Path) -> dict | None:
    """Process a ReqIF or ReqIFZ file and output JSON using StrictDoc.

    Args:
        file_path: Path to the .reqif or .reqifz file

    Returns:
        Processing result dict, or None on failure
    """
    file_path = Path(file_path)

    if not file_path.exists():
        print(f"File not found: {file_path}")
        return None

    extension = file_path.suffix.lower()

    if extension == ".reqifz":
        print(f"Processing ReqIFZ bundle: {file_path}")
        output_dir = file_path.parent / f"{file_path.stem}_strictdoc_output"
        result = process_reqifz_file(file_path, output_dir)

        if result:
            # Write the native StrictDoc JSON
            strictdoc_output = output_dir / f"{file_path.stem}_strictdoc.json"
            with open(strictdoc_output, "w") as f:
                json.dump({"DOCUMENTS": result["strictdoc_documents"]}, f, indent=2)

            # Write the flattened JSON
            flat_output = output_dir / f"{file_path.stem}_strictdoc_flat.json"
            with open(flat_output, "w") as f:
                json.dump(result["flat"], f, indent=2)

            print(f"  Native output: {strictdoc_output}")
            print(f"  Flat output: {flat_output}")
            print(f"  Requirements: {len(result['flat']['requirements'])}")
            print(f"  Links: {len(result['flat']['links'])}")
            print(f"  Attachments: {len(result['attachments'])}")

        return result

    elif extension == ".reqif":
        print(f"Processing ReqIF file: {file_path}")
        result = process_reqif_file(file_path)

        if result:
            # Write the native StrictDoc JSON
            strictdoc_output = str(file_path).replace(".reqif", "_strictdoc.json")
            with open(strictdoc_output, "w") as f:
                json.dump(result["strictdoc"], f, indent=2)

            # Write the flattened JSON
            flat_output = str(file_path).replace(".reqif", "_strictdoc_flat.json")
            with open(flat_output, "w") as f:
                json.dump(result["flat"], f, indent=2)

            print(f"  Native output: {strictdoc_output}")
            print(f"  Flat output: {flat_output}")
            print(f"  Requirements: {len(result['flat']['requirements'])}")
            print(f"  Links: {len(result['flat']['links'])}")

        return result

    else:
        print(f"Unsupported file type: {extension}")
        return None


if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1:
        for file_arg in sys.argv[1:]:
            process_file(file_arg)
    else:
        # Default test files
        test_files = [
            "examples/reqif_testfile.reqif",
        ]

        # Also look for any .reqifz files in examples
        examples_dir = Path("examples")
        if examples_dir.exists():
            test_files.extend(str(f) for f in examples_dir.glob("*.reqifz"))

        for test_file in test_files:
            if Path(test_file).exists():
                result = process_file(test_file)
                if result is None:
                    print(f"Failed to process {test_file}")
