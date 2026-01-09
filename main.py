import json
import re
from pathlib import Path
from reqif.parser import ReqIFParser, ReqIFZParser


def preprocess_reqif_xml(content: str) -> str:
    """Preprocess ReqIF XML content to handle common issues.

    - Strips XML comments before the XML declaration (invalid but common)
    - Preserves all other content including comments after declaration
    """
    # Find the XML declaration
    xml_decl_match = re.search(r'<\?xml[^?]*\?>', content)
    if xml_decl_match:
        # Return content starting from XML declaration
        return content[xml_decl_match.start():]
    return content


def extract_requirement_from_spec_object(bundle, spec_object):
    """Extract a requirement directly from a spec object (no hierarchy node).

    Used when files have spec objects but no specification hierarchy.
    """
    spec_type = None
    if spec_object.spec_object_type:
        spec_type = bundle.get_spec_object_type_by_ref(spec_object.spec_object_type)

    # Build attribute map from spec type if available
    attr_def_map = {}
    if spec_type and hasattr(spec_type, "attribute_definitions") and spec_type.attribute_definitions:
        for attr_def in spec_type.attribute_definitions:
            attr_def_map[attr_def.identifier] = attr_def

    # Extract attributes
    attrs = {}
    if spec_object.attributes:
        for attr in spec_object.attributes:
            attr_def = attr_def_map.get(attr.definition_ref)
            attr_name = (
                attr_def.long_name
                if attr_def and hasattr(attr_def, "long_name")
                else attr.definition_ref
            )

            if isinstance(attr.value, list):
                attrs[attr_name] = attr.value
            elif attr.value_stripped_xhtml:
                attrs[attr_name] = attr.value_stripped_xhtml
            else:
                attrs[attr_name] = attr.value

    # Clean HTML tags from text
    def clean_html(html_str):
        if not html_str:
            return ""
        text_content = re.sub(r"<[^>]+>", "", html_str)
        return " ".join(text_content.split())

    # Determine name from various sources
    chapter_name = attrs.get("ReqIF.ChapterName", "")
    text = attrs.get("ReqIF.Text", "")
    long_name = spec_object.long_name or ""

    if chapter_name:
        name = chapter_name
    elif text:
        name = clean_html(text)
    elif long_name:
        name = long_name
    else:
        name = ""

    req = {
        "id": spec_object.identifier,
        "name": name,
    }

    if spec_object.last_change:
        req["last_change"] = spec_object.last_change

    if spec_type and hasattr(spec_type, "long_name"):
        req["type"] = spec_type.long_name

    req["attributes"] = attrs

    return req


def extract_requirement(bundle, node):
    """Extract a requirement object from a hierarchy node."""
    try:
        spec_object = bundle.get_spec_object_by_ref(node.spec_object)
    except KeyError:
        # Spec object reference doesn't exist (file inconsistency)
        return None

    if not spec_object:
        return None

    spec_type = None
    if spec_object.spec_object_type:
        try:
            spec_type = bundle.get_spec_object_type_by_ref(spec_object.spec_object_type)
        except KeyError:
            pass  # Type not found, continue without it

    # Build attribute map
    attr_def_map = {}
    if spec_type and hasattr(spec_type, "attribute_definitions") and spec_type.attribute_definitions:
        for attr_def in spec_type.attribute_definitions:
            attr_def_map[attr_def.identifier] = attr_def

    # Extract attributes
    attrs = {}
    for attr in (spec_object.attributes or []):
        attr_def = attr_def_map.get(attr.definition_ref)
        attr_name = (
            attr_def.long_name
            if attr_def and hasattr(attr_def, "long_name")
            else attr.definition_ref
        )

        if isinstance(attr.value, list):
            attrs[attr_name] = attr.value
        elif attr.value_stripped_xhtml:
            attrs[attr_name] = attr.value_stripped_xhtml
        else:
            attrs[attr_name] = attr.value

    # Clean HTML tags from text
    def clean_html(html_str):
        if not html_str:
            return ""
        # Remove HTML tags but preserve text content
        text_content = re.sub(r"<[^>]+>", "", html_str)
        # Clean up whitespace
        return " ".join(text_content.split())

    # Determine name
    chapter_name = attrs.get("ReqIF.ChapterName", "")
    text = attrs.get("ReqIF.Text", "")
    long_name = node.long_name or spec_object.long_name or ""

    # Name: prefer ChapterName, then Text, then long_name
    if chapter_name:
        name = chapter_name
    elif text:
        name = clean_html(text)
    elif long_name:
        name = long_name
    else:
        name = ""

    # Build requirement object with core identifiers
    req = {
        "id": node.identifier,
        "name": name,
    }

    # Add metadata if available
    last_change = node.last_change or spec_object.last_change
    if last_change:
        req["last_change"] = last_change

    # Add spec object type info
    if spec_type and hasattr(spec_type, "long_name"):
        req["type"] = spec_type.long_name

    # Add all extracted attributes dynamically
    req["attributes"] = attrs

    return req


def process_reqif_file(file_path, preprocess=True):
    """Process a ReqIF file and return flat structure with requirements and links.

    Args:
        file_path: Path to the .reqif file
        preprocess: If True, preprocess XML to strip leading comments

    Returns:
        Dict with 'requirements' and 'links', or None on error
    """
    try:
        # Optionally preprocess to handle XML comments before declaration
        if preprocess:
            with open(file_path, 'r', encoding='utf-8') as f:
                content = f.read()
            content = preprocess_reqif_xml(content)
            bundle = ReqIFParser.parse_from_string(content)
        else:
            bundle = ReqIFParser.parse(file_path)

        requirements = []
        links = []

        content = bundle.core_content.req_if_content
        specifications = content.specifications if content else None

        extracted_from_hierarchy = False
        if specifications:
            # Standard path: iterate through specification hierarchy
            for specification in specifications:
                node_map = {}  # node_id -> node
                parent_map = {}  # child_id -> parent_id

                def collect_nodes(node, parent_id=None):
                    node_map[node.identifier] = node
                    if parent_id:
                        parent_map[node.identifier] = parent_id

                    if node.children:
                        for child in node.children:
                            collect_nodes(child, node.identifier)

                # Collect all nodes from root nodes
                for root_node in bundle.iterate_specification_hierarchy(specification):
                    collect_nodes(root_node)

                # Extract requirements
                for node_id, node in node_map.items():
                    req = extract_requirement(bundle, node)
                    if req:
                        requirements.append(req)
                        extracted_from_hierarchy = True

                # Build links
                for child_id, parent_id in parent_map.items():
                    links.append(
                        {
                            "source": parent_id,
                            "type": "hierarchy",
                            "target": child_id,
                        }
                    )

        # Fallback: no hierarchy found (either no specs, or specs with empty/broken hierarchy)
        if not extracted_from_hierarchy and content and content.spec_objects:
            # Fallback: no specifications, but have spec objects
            # Treat all spec objects as a flat list (no hierarchy)
            for spec_object in content.spec_objects:
                req = extract_requirement_from_spec_object(bundle, spec_object)
                if req:
                    requirements.append(req)
            # No hierarchy links in this case

        return {"requirements": requirements, "links": links}

    except Exception as e:
        print(f"Error processing {file_path}: {e}")
        return None


def process_reqifz_file(file_path, output_dir=None):
    """Process a ReqIFZ bundle and return combined data with attachments extracted.

    Args:
        file_path: Path to the .reqifz file
        output_dir: Directory to extract attachments to. If None, uses <filename>_output/

    Returns:
        Dict with 'requirements', 'links', and 'attachments' (list of extracted file paths)
    """
    try:
        file_path = Path(file_path)

        if output_dir is None:
            output_dir = file_path.parent / f"{file_path.stem}_output"
        else:
            output_dir = Path(output_dir)

        z_bundle = ReqIFZParser.parse(str(file_path))

        all_requirements = []
        all_links = []
        extracted_attachments = []

        # Process each ReqIF bundle in the archive
        for bundle_name, bundle in z_bundle.reqif_bundles.items():
            print(f"  Processing embedded ReqIF: {bundle_name}")

            if bundle.core_content is None or bundle.core_content.req_if_content is None:
                continue

            if bundle.core_content.req_if_content.specifications is None:
                continue

            for specification in bundle.core_content.req_if_content.specifications:
                node_map = {}
                parent_map = {}

                def collect_nodes(node, parent_id=None):
                    node_map[node.identifier] = node
                    if parent_id:
                        parent_map[node.identifier] = parent_id
                    if node.children:
                        for child in node.children:
                            collect_nodes(child, node.identifier)

                for root_node in bundle.iterate_specification_hierarchy(specification):
                    collect_nodes(root_node)

                for node_id, node in node_map.items():
                    req = extract_requirement(bundle, node)
                    if req:
                        req["source_file"] = bundle_name
                        all_requirements.append(req)

                for child_id, parent_id in parent_map.items():
                    all_links.append({
                        "source": parent_id,
                        "type": "hierarchy",
                        "target": child_id,
                    })

        # Extract attachments (images, documents, etc.)
        if z_bundle.attachments:
            attachments_dir = output_dir / "attachments"
            attachments_dir.mkdir(parents=True, exist_ok=True)

            for attachment_name, attachment_data in z_bundle.attachments.items():
                # Skip directory entries (empty data or names ending with /)
                if not attachment_data or attachment_name.endswith("/"):
                    continue

                # Preserve directory structure within attachments
                attachment_path = attachments_dir / attachment_name
                attachment_path.parent.mkdir(parents=True, exist_ok=True)

                with open(attachment_path, "wb") as f:
                    f.write(attachment_data)

                extracted_attachments.append(str(attachment_path.relative_to(output_dir)))
                print(f"  Extracted attachment: {attachment_name}")

        return {
            "requirements": all_requirements,
            "links": all_links,
            "attachments": extracted_attachments,
        }

    except Exception as e:
        print(f"Error processing {file_path}: {e}")
        import traceback
        traceback.print_exc()
        return None


def process_file(file_path):
    """Process a ReqIF or ReqIFZ file based on extension."""
    file_path = Path(file_path)

    if not file_path.exists():
        print(f"File not found: {file_path}")
        return None

    extension = file_path.suffix.lower()

    if extension == ".reqifz":
        print(f"Processing ReqIFZ bundle: {file_path}")
        output_dir = file_path.parent / f"{file_path.stem}_output"
        result = process_reqifz_file(file_path, output_dir)

        if result:
            output_file = output_dir / f"{file_path.stem}_flat.json"
            output_dir.mkdir(parents=True, exist_ok=True)
            with open(output_file, "w") as f:
                json.dump(result, f, indent=2, default=str)
            print(f"Processed {file_path} -> {output_file}")
            print(f"  Requirements: {len(result['requirements'])}")
            print(f"  Links: {len(result['links'])}")
            print(f"  Attachments: {len(result['attachments'])}")
        return result

    elif extension == ".reqif":
        print(f"Processing ReqIF file: {file_path}")
        result = process_reqif_file(file_path)

        if result:
            output_file = str(file_path).replace(".reqif", "_flat.json")
            with open(output_file, "w") as f:
                json.dump(result, f, indent=2, default=str)
            print(f"Processed {file_path} -> {output_file}")
            print(f"  Requirements: {len(result['requirements'])}")
            print(f"  Links: {len(result['links'])}")
        return result

    else:
        print(f"Unsupported file type: {extension}")
        return None


if __name__ == "__main__":
    import sys

    # If command line arguments provided, process those files
    if len(sys.argv) > 1:
        for file_arg in sys.argv[1:]:
            process_file(file_arg)
    else:
        # Default test files
        test_files = [
            "examples/reqif_testfile.reqif",
            "examples/Sample.reqif",
            "examples/Sample_CustomAttributes.reqif",
        ]

        # Also look for any .reqifz files in examples
        examples_dir = Path("examples")
        if examples_dir.exists():
            test_files.extend(str(f) for f in examples_dir.glob("*.reqifz"))

        for test_file in test_files:
            result = process_file(test_file)
            if result is None:
                print(f"Failed to process {test_file}")
