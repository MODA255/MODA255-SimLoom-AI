"""Detached software job entry point, separate from modules imported by the API."""
import sys
from pathlib import Path

from .worker_jobs import job_id, supervise


if __name__ == "__main__":
    supervise(Path(sys.argv[1]), job_id(sys.argv[2]))
