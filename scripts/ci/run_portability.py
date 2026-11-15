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


def _command_escape(value: str) -> str:
    compact = " ".join(str(value).split())
    if len(compact) > 1400:
        compact = compact[:500] + " … " + compact[-850:]
    return (compact.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
            .replace(":", "%3A").replace(",", "%2C"))


class GithubFailureReporter:
    """Keep the first useful traceback visible when job logs are unavailable."""

    def pytest_runtest_logreport(self, report):
        if report.failed:
            print(f"::error title=pytest {report.when}::{_command_escape(report.longrepr)}", flush=True)

    def pytest_collectreport(self, report):
        if report.failed:
            print(f"::error title=pytest collection::{_command_escape(report.longrepr)}", flush=True)


def main() -> int:
    report = Path(os.environ.get("RUNNER_TEMP", ".")) / "cad-portability.xml"
    report.parent.mkdir(parents=True, exist_ok=True)
    print(f"::notice title=portability report::JUnit report path: {_command_escape(report)}", flush=True)
    try:
        return int(pytest.main([*TESTS, "-q", f"--junitxml={report}"], plugins=[GithubFailureReporter()]))
    except BaseException as exc:
        print(f"::error title=portability runner::{_command_escape(repr(exc))}", flush=True)
        raise


if __name__ == "__main__":
    raise SystemExit(main())
