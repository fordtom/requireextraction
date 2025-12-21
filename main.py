import json
import re
from pathlib import Path
from reqif.parser import ReqIFParser


def extract_requirement(bundle, node):
    """Extract a requirement object from a hierarchy node."""
    spec_object = bundle.get_spec_object_by_ref(node.spec_object)
    if not spec_object:
        return None

    spec_type = bundle.get_spec_object_type_by_ref(spec_object.spec_object_type)
    if not spec_type:
        return None

    # Build attribute map
    attr_def_map = {}
    if hasattr(spec_type, "attribute_definitions"):
        for attr_def in spec_type.attribute_definitions:
            attr_def_map[attr_def.identifier] = attr_def

    # Extract attributes
    attrs = {}
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

    # Build requirement object
    req = {
        "id": node.identifier,
        "foreign_id": attrs.get("ReqIF.ForeignID", ""),
        "origin_id": attrs.get("origID", ""),
        "name": name,
    }

    # Add metadata if available
    last_change = node.last_change or spec_object.last_change
    if last_change:
        req["last_change"] = last_change

    return req


def process_reqif_file(file_path):
    """Process a ReqIF file and return flat structure with requirements and links."""
    try:
        bundle = ReqIFParser.parse(file_path)
        requirements = []
        links = []

        # Process all specifications
        for specification in bundle.core_content.req_if_content.specifications:
            # Collect all nodes with their parent relationships
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

            # Build links
            for child_id, parent_id in parent_map.items():
                links.append(
                    {
                        "source": parent_id,
                        "type": "hierarchy",
                        "target": child_id,
                    }
                )

        return {"requirements": requirements, "links": links}

    except Exception as e:
        print(f"Error processing {file_path}: {e}")
        return None


if __name__ == "__main__":
    test_files = [
        "examples/reqif_testfile.reqif",
        "examples/Sample.reqif",
        "examples/Sample_CustomAttributes.reqif",
    ]

    for test_file in test_files:
        if Path(test_file).exists():
            result = process_reqif_file(test_file)
            if result:
                output_file = test_file.replace(".reqif", "_flat.json")
                with open(output_file, "w") as f:
                    json.dump(result, f, indent=2, default=str)
                print(f"Processed {test_file} -> {output_file}")
                print(f"  Requirements: {len(result['requirements'])}")
                print(f"  Links: {len(result['links'])}")
            else:
                print(f"Failed to process {test_file}")
        else:
            print(f"File not found: {test_file}")
