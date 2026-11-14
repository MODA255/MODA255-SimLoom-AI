"""Publish JUnit pytest failures as GitHub Actions annotations.

The normal pytest output is not exposed through the public Actions API.  Keeping
the failure summaries as annotations makes cross-platform CI failures visible
without requiring a repository token to download private job logs.
"""

from __future__ import annotations

import html
import sys
import xml.etree.ElementTree as ET
from pathlib import Path


def _annotation_text(testcase: ET.Element, failure: ET.Element) -> str:
    name = testcase.get("name", "unknown test")
    classname = testcase.get("classname", "")
    raw_detail = " ".join((failure.get("message") or "", "".join(failure.itertext()))).strip()
    # Pytest puts the useful assertion or subprocess stderr at the end of the
    # traceback. Keep both ends so long tracebacks remain actionable in an
    # annotation instead of ending before the actual failure reason.
    compact = " ".join(raw_detail.split())
    if len(compact) > 900:
        detail = compact[:300] + " … " + compact[-580:]
    else:
        detail = compact
    return f"{classname}.{name}: {detail}" if classname else f"{name}: {detail}"


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: report_pytest_failures.py JUNIT_XML", file=sys.stderr)
        return 2
    report = Path(sys.argv[1])
    if not report.is_file():
        print(f"JUnit report not found: {report}", file=sys.stderr)
        return 1
    root = ET.parse(report).getroot()
    failures = [
        (testcase, child)
        for testcase in root.iter("testcase")
        for child in testcase
        if child.tag in {"failure", "error"}
    ]
    for testcase, failure in failures:
        message = html.escape(_annotation_text(testcase, failure), quote=False)
        print(f"::error title=pytest failure::{message}")
    print(f"pytest failures reported: {len(failures)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
