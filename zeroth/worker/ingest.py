"""Shallow clone and inspect. The worker reads files; it never executes them."""
import shutil
import subprocess
import tempfile
from pathlib import Path

from zeroth.config import settings
from zeroth.safety import RepoRejected


class IngestError(Exception):
    pass


def clone(clone_url: str) -> Path:
    target = Path(tempfile.mkdtemp(prefix="zeroth-"))
    proc = subprocess.run(
        ["git", "clone", "--depth", "1", "--single-branch", "--no-tags",
         clone_url, str(target)],
        capture_output=True, text=True, timeout=120,
    )
    if proc.returncode != 0:
        shutil.rmtree(target, ignore_errors=True)
        raise IngestError(f"clone failed: {proc.stderr.strip()[:300]}")

    size_mb = sum(f.stat().st_size for f in target.rglob("*") if f.is_file()) / 1_048_576
    if size_mb > settings.max_repo_mb:
        shutil.rmtree(target, ignore_errors=True)
        raise RepoRejected(f"Repository is {size_mb:.0f}MB after clone; limit is {settings.max_repo_mb}MB.")

    return target


def cleanup(path: Path) -> None:
    shutil.rmtree(path, ignore_errors=True)
