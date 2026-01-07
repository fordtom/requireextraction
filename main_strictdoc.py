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
- Two-step conversion process (ReqIF -> SDoc -> JSON)
"""

import json
import re
import shutil
import subprocess
import tempfile
from pathlib import Path


def run_strictdoc_import(input_path: Path, output_dir: Path) -> Path | None:
    """Run strictdoc import to convert ReqIF to SDoc format.

    Args:
        input_path: Path to the .reqif file
        output_dir: Directory to write the .sdoc output

    Returns:
        Path to the generated SDoc directory, or None on failure
    """
    sdoc_output = output_dir / f"{input_path.stem}.sdoc"

    cmd = [
        "strictdoc", "import", "reqif", "p01_sdoc",
        str(input_path),
        str(sdoc_output),
        "--reqif-import-markup=HTML"
    ]

    try:
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            print(f"Error importing ReqIF: {result.stderr}")
            return None
        return sdoc_output
    except Exception as e:
        print(f"Failed to run strictdoc import: {e}")
        return None


def run_strictdoc_export(sdoc_dir: Path, output_dir: Path) -> Path | None:
    """Run strictdoc export to convert SDoc to JSON format.

    Args:
        sdoc_dir: Path to the directory containing .sdoc files
        output_dir: Directory to write the JSON output

    Returns:
        Path to the generated JSON file, or None on failure
    """
    cmd = [
        "strictdoc", "export",
        str(sdoc_dir),
        "--formats=json",
        f"--output-dir={output_dir}"
    ]

    try:
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            print(f"Error exporting to JSON: {result.stderr}")
            return None

        # StrictDoc puts JSON in json/index.json
        json_file = output_dir / "json" / "index.json"
        if json_file.exists():
            return json_file
        return None
    except Exception as e:
        print(f"Failed to run strictdoc export: {e}")
        return None


def flatten_nodes(nodes: list, parent_uid: str | None = None) -> tuple[list, list]:
    """Flatten a nested node structure into requirements and links.

    Args:
        nodes: List of nested node objects from StrictDoc JSON
        parent_uid: UID of the parent node (for building links)

    Returns:
        Tuple of (requirements list, links list)
    """
    requirements = []
    links = []

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
        if parent_uid:
            links.append({
                "source": parent_uid,
                "type": "hierarchy",
                "target": uid
            })

        # Recursively process children
        child_nodes = node.get("NODES", [])
        if child_nodes:
            child_reqs, child_links = flatten_nodes(child_nodes, uid)
            requirements.extend(child_reqs)
            links.extend(child_links)

    return requirements, links


def clean_html(html_str: str) -> str:
    """Remove HTML tags and clean whitespace from a string."""
    if not html_str:
        return ""
    text = re.sub(r"<[^>]+>", "", html_str)
    return " ".join(text.split())


def process_reqif_file(file_path: str | Path) -> dict | None:
    """Process a ReqIF file using StrictDoc and return JSON output.

    This function:
    1. Converts ReqIF to SDoc format using strictdoc import
    2. Exports SDoc to JSON using strictdoc export
    3. Returns both the native StrictDoc JSON and a flattened version

    Args:
        file_path: Path to the .reqif file

    Returns:
        Dict containing 'strictdoc' (native format) and 'flat' (requirements + links)
    """
    file_path = Path(file_path)

    if not file_path.exists():
        print(f"File not found: {file_path}")
        return None

    # Create temporary directory for intermediate files
    with tempfile.TemporaryDirectory() as temp_dir:
        temp_path = Path(temp_dir)

        # Step 1: Import ReqIF to SDoc
        print(f"  Converting ReqIF to SDoc...")
        sdoc_dir = run_strictdoc_import(file_path, temp_path)
        if not sdoc_dir:
            return None

        # Step 2: Export SDoc to JSON
        print(f"  Exporting SDoc to JSON...")
        json_export_dir = temp_path / "json_export"
        json_file = run_strictdoc_export(sdoc_dir, json_export_dir)
        if not json_file:
            return None

        # Read the JSON output
        with open(json_file) as f:
            strictdoc_json = json.load(f)

        # Flatten the hierarchical structure for comparison
        all_requirements = []
        all_links = []

        for document in strictdoc_json.get("DOCUMENTS", []):
            nodes = document.get("NODES", [])
            reqs, links = flatten_nodes(nodes)
            all_requirements.extend(reqs)
            all_links.extend(links)

        return {
            "strictdoc": strictdoc_json,
            "flat": {
                "requirements": all_requirements,
                "links": all_links
            }
        }


def process_reqifz_file(file_path: str | Path, output_dir: Path | None = None) -> dict | None:
    """Process a ReqIFZ bundle file using StrictDoc.

    Note: As of the current StrictDoc version, ReqIFZ support may be limited.
    This function extracts the ReqIF files from the bundle and processes each.

    Args:
        file_path: Path to the .reqifz file
        output_dir: Directory to extract attachments to

    Returns:
        Dict containing processed data, or None on failure
    """
    import zipfile

    file_path = Path(file_path)

    if output_dir is None:
        output_dir = file_path.parent / f"{file_path.stem}_strictdoc_output"
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    all_results = {
        "strictdoc_documents": [],
        "flat": {
            "requirements": [],
            "links": []
        },
        "attachments": []
    }

    try:
        with zipfile.ZipFile(file_path, 'r') as zf:
            # Extract all files
            zf.extractall(output_dir / "extracted")

            # Find all .reqif files in the extracted content
            extracted_dir = output_dir / "extracted"
            reqif_files = list(extracted_dir.rglob("*.reqif"))

            if not reqif_files:
                print(f"No .reqif files found in bundle")
                return None

            # Process each ReqIF file
            for reqif_file in reqif_files:
                print(f"  Processing embedded: {reqif_file.name}")
                result = process_reqif_file(reqif_file)

                if result:
                    # Add source file reference
                    for doc in result["strictdoc"].get("DOCUMENTS", []):
                        doc["_SOURCE_FILE"] = str(reqif_file.name)

                    all_results["strictdoc_documents"].extend(
                        result["strictdoc"].get("DOCUMENTS", [])
                    )

                    for req in result["flat"]["requirements"]:
                        req["source_file"] = str(reqif_file.name)

                    all_results["flat"]["requirements"].extend(
                        result["flat"]["requirements"]
                    )
                    all_results["flat"]["links"].extend(
                        result["flat"]["links"]
                    )

            # Track attachments (non-reqif files)
            for item in zf.namelist():
                if not item.endswith('.reqif') and not item.endswith('/'):
                    all_results["attachments"].append(item)

        return all_results

    except Exception as e:
        print(f"Error processing ReqIFZ: {e}")
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
