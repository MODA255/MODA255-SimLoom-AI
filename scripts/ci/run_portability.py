"""Run the portability test set with a runner-native JUnit report path."""

from __future__ import annotations

import os
from pathlib import Path

import pytest


TESTS = [
    "tests/test_cad_transactions.py",
    "tests/test_cad_replay.py",
    "tests/test_version_files.py",
    "tests/test_cad_editing.py",
    "tests/test_native_dxf.py",
    "tests/test_cad_plotting.py",
    "tests/test_cad_viewer.py",
    "tests/test_cad_corners.py",
    "tests/test_software_integration.py",
    "tests/test_worker_jobs.py",
    "tests/test_vendor_validation.py",
    "tests/test_native_vendor_evidence.py",
    "tests/test_four_vendor_runners.py",
    "tests/test_workflow_resume.py",
]


def main() -> int:
    report = Path(os.environ.get("RUNNER_TEMP", ".")) / "cad-portability.xml"
    report.parent.mkdir(parents=True, exist_ok=True)
    return int(pytest.main([*TESTS, "-q", f"--junitxml={report}"]))


if __name__ == "__main__":
    raise SystemExit(main())
