#!/usr/bin/env python3
"""Test script to parse all collected ReqIF examples and generate a comparison report."""

import json
import sys
import time
from pathlib import Path
from dataclasses import dataclass, asdict
from typing import Optional

# Add parent to path
sys.path.insert(0, str(Path(__file__).parent))

from main import process_reqif_file, process_reqifz_file


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
    sample_requirement: Optional[dict] = None


def test_file(file_path: Path) -> ParseResult:
    """Test parsing a single file and return results."""
    source = file_path.parent.name
    size_kb = file_path.stat().st_size / 1024

    start_time = time.time()

    try:
        if file_path.suffix.lower() == '.reqifz':
            output_dir = file_path.parent / f"{file_path.stem}_test_output"
            result = process_reqifz_file(str(file_path), str(output_dir))
            attachments_count = len(result.get('attachments', [])) if result else 0
        else:
            result = process_reqif_file(str(file_path))
            attachments_count = 0

        parse_time_ms = (time.time() - start_time) * 1000

        if result:
            requirements = result.get('requirements', [])
            links = result.get('links', [])

            # Get first requirement as sample
            sample = None
            if requirements:
                sample = requirements[0]

            return ParseResult(
                file=file_path.name,
                source=source,
                size_kb=round(size_kb, 2),
                success=True,
                requirements_count=len(requirements),
                links_count=len(links),
                attachments_count=attachments_count,
                parse_time_ms=round(parse_time_ms, 2),
                sample_requirement=sample
            )
        else:
            return ParseResult(
                file=file_path.name,
                source=source,
                size_kb=round(size_kb, 2),
                success=False,
                requirements_count=0,
                links_count=0,
                attachments_count=0,
                parse_time_ms=round(parse_time_ms, 2),
                error="Parser returned None"
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
            error=str(e)
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

    # Failed files
    if fail_count > 0:
        print("\n" + "-" * 40)
        print("FAILED FILES:")
        for r in results:
            if not r.success:
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
