#!/usr/bin/env python3
"""Test script to parse all collected ReqIF examples and generate a comparison report.

Pipeline: ReqIF/ReqIFZ → strictdoc ReqIFParser → ReqIFBundle → Our extraction → JSON

Failures can occur at:
1. strictdoc parser level (XML errors, schema issues, assertion errors)
2. Our extraction level (empty specifications, missing references)
"""

import json
import sys
import time
from pathlib import Path
from dataclasses import dataclass, asdict
from typing import Optional

# Add parent to path
sys.path.insert(0, str(Path(__file__).parent))

from reqif.parser import ReqIFParser, ReqIFZParser
from reqif.models.error_handling import ReqIFXMLParsingError

from main import preprocess_reqif_xml, extract_requirement_from_spec_object


@dataclass
class ParseResult:
    file: str
    source: str
    size_kb: float
    success: bool
    requirements_count: int
    links_count: int
    attachments_count: int
    parse_time_ms: float
    error: Optional[str] = None
    error_level: Optional[str] = None  # "strictdoc" or "extraction"
    sample_requirement: Optional[dict] = None


def test_file(file_path: Path) -> ParseResult:
    """Test parsing a single file and return results.

    Tests both strictdoc parsing and our extraction separately to identify failure level.
    """
    from main import extract_requirement

    source = file_path.parent.name
    size_kb = file_path.stat().st_size / 1024
    start_time = time.time()

    # Step 1: Test strictdoc parsing (with preprocessing for XML comments)
    try:
        if file_path.suffix.lower() == '.reqifz':
            z_bundle = ReqIFZParser.parse(str(file_path))
            bundles = list(z_bundle.reqif_bundles.values())
        else:
            # Preprocess to handle XML comments before declaration
            with open(file_path, 'r', encoding='utf-8') as f:
                content = f.read()
            content = preprocess_reqif_xml(content)
            bundle = ReqIFParser.parse_from_string(content)
            bundles = [bundle]
    except (ReqIFXMLParsingError, Exception) as e:
        parse_time_ms = (time.time() - start_time) * 1000
        error_msg = str(e)
        # Categorize strictdoc errors
        if "XML declaration" in error_msg:
            error_type = "XML: comments before declaration"
        elif "Expected root tag" in error_msg:
            error_type = "XML: invalid root tag (RIF vs REQ-IF)"
        elif "AssertionError" in error_msg or "assert" in error_msg.lower():
            error_type = "strictdoc assertion error"
        else:
            error_type = f"strictdoc: {error_msg[:50]}"
        return ParseResult(
            file=file_path.name,
            source=source,
            size_kb=round(size_kb, 2),
            success=False,
            requirements_count=0,
            links_count=0,
            attachments_count=0,
            parse_time_ms=round(parse_time_ms, 2),
            error=error_type,
            error_level="strictdoc"
        )

    # Step 2: Test our extraction
    try:
        all_requirements = []
        all_links = []
        attachments_count = 0

        for bundle in bundles:
            if bundle.core_content is None or bundle.core_content.req_if_content is None:
                continue

            content = bundle.core_content.req_if_content
            specs = content.specifications if content else None

            # Track if we successfully extracted any requirements from hierarchy
            extracted_from_hierarchy = False
            if specs:
                for specification in specs:
                    # Standard path: process specifications with hierarchy
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
                            all_requirements.append(req)
                            extracted_from_hierarchy = True

                    for child_id, parent_id in parent_map.items():
                        all_links.append({
                            "source": parent_id,
                            "type": "hierarchy",
                            "target": child_id,
                        })

            # Fallback: no requirements from hierarchy (empty specs, broken refs, or no specs)
            if not extracted_from_hierarchy and content and content.spec_objects:
                # Fallback: no specifications but have spec objects
                # Treat all spec objects as a flat list (no hierarchy)
                for spec_object in content.spec_objects:
                    req = extract_requirement_from_spec_object(bundle, spec_object)
                    if req:
                        all_requirements.append(req)

        # Handle reqifz attachments
        if file_path.suffix.lower() == '.reqifz' and hasattr(z_bundle, 'attachments') and z_bundle.attachments:
            attachments_count = len([a for a in z_bundle.attachments.values() if a])

        parse_time_ms = (time.time() - start_time) * 1000

        if len(all_requirements) == 0 and len(bundles) > 0:
            # Parsed successfully but no requirements extracted
            # Check if there were any spec objects at all
            has_spec_objects = any(
                b.core_content and b.core_content.req_if_content and
                b.core_content.req_if_content.spec_objects
                for b in bundles
            )
            if has_spec_objects:
                error = "extraction: spec objects exist but no specifications/hierarchy"
            else:
                error = "extraction: no spec objects in file"
            return ParseResult(
                file=file_path.name,
                source=source,
                size_kb=round(size_kb, 2),
                success=False,
                requirements_count=0,
                links_count=0,
                attachments_count=attachments_count,
                parse_time_ms=round(parse_time_ms, 2),
                error=error,
                error_level="extraction"
            )

        sample = all_requirements[0] if all_requirements else None

        return ParseResult(
            file=file_path.name,
            source=source,
            size_kb=round(size_kb, 2),
            success=True,
            requirements_count=len(all_requirements),
            links_count=len(all_links),
            attachments_count=attachments_count,
            parse_time_ms=round(parse_time_ms, 2),
            sample_requirement=sample
        )

    except Exception as e:
        parse_time_ms = (time.time() - start_time) * 1000
        return ParseResult(
            file=file_path.name,
            source=source,
            size_kb=round(size_kb, 2),
            success=False,
            requirements_count=0,
            links_count=0,
            attachments_count=0,
            parse_time_ms=round(parse_time_ms, 2),
            error=f"extraction: {str(e)[:50]}",
            error_level="extraction"
        )


def main():
    examples_dir = Path(__file__).parent / "examples" / "collected"

    if not examples_dir.exists():
        print(f"Examples directory not found: {examples_dir}")
        return

    # Find all ReqIF files
    files = []
    for pattern in ['**/*.reqif', '**/*.reqifz', '**/*.xml']:
        files.extend(examples_dir.glob(pattern))

    # Sort by size
    files.sort(key=lambda f: f.stat().st_size)

    print(f"Found {len(files)} ReqIF files to test\n")
    print("=" * 80)

    results = []
    success_count = 0
    fail_count = 0
    total_requirements = 0
    total_links = 0

    for file_path in files:
        print(f"\nTesting: {file_path.relative_to(examples_dir)}")
        result = test_file(file_path)
        results.append(result)

        if result.success:
            success_count += 1
            total_requirements += result.requirements_count
            total_links += result.links_count
            print(f"  ✓ OK: {result.requirements_count} requirements, {result.links_count} links ({result.parse_time_ms}ms)")
        else:
            fail_count += 1
            print(f"  ✗ FAILED: {result.error}")

    print("\n" + "=" * 80)
    print("\nSUMMARY")
    print("=" * 80)
    print(f"Total files tested: {len(files)}")
    print(f"Successful: {success_count}")
    print(f"Failed: {fail_count}")
    print(f"Total requirements extracted: {total_requirements}")
    print(f"Total links extracted: {total_links}")

    # Group by source
    print("\n" + "-" * 40)
    print("BY SOURCE:")
    sources = {}
    for r in results:
        if r.source not in sources:
            sources[r.source] = {'success': 0, 'fail': 0, 'reqs': 0}
        if r.success:
            sources[r.source]['success'] += 1
            sources[r.source]['reqs'] += r.requirements_count
        else:
            sources[r.source]['fail'] += 1

    for source, stats in sorted(sources.items()):
        print(f"  {source}: {stats['success']}/{stats['success']+stats['fail']} files, {stats['reqs']} requirements")

    # Failed files by level
    if fail_count > 0:
        strictdoc_fails = [r for r in results if not r.success and r.error_level == "strictdoc"]
        extraction_fails = [r for r in results if not r.success and r.error_level == "extraction"]

        if strictdoc_fails:
            print("\n" + "-" * 40)
            print(f"STRICTDOC PARSER FAILURES ({len(strictdoc_fails)}):")
            for r in strictdoc_fails:
                print(f"  - {r.source}/{r.file}: {r.error}")

        if extraction_fails:
            print("\n" + "-" * 40)
            print(f"EXTRACTION FAILURES ({len(extraction_fails)}):")
            for r in extraction_fails:
                print(f"  - {r.source}/{r.file}: {r.error}")

    # Save detailed report
    report = {
        'summary': {
            'total_files': len(files),
            'successful': success_count,
            'failed': fail_count,
            'total_requirements': total_requirements,
            'total_links': total_links,
        },
        'by_source': sources,
        'results': [asdict(r) for r in results]
    }

    report_path = Path(__file__).parent / "examples" / "collected" / "parse_report.json"
    with open(report_path, 'w') as f:
        json.dump(report, f, indent=2, default=str)

    print(f"\nDetailed report saved to: {report_path}")


if __name__ == "__main__":
    main()
