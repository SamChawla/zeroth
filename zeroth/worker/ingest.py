"""Shallow clone and inspect. The worker reads files; it never executes them.

The size cap is enforced three times, deliberately: at submit via the host's
API (cheap, but rate-limitable and GitLab-less), DURING the clone by watching
the directory grow (kills a kernel-sized checkout in seconds instead of
letting it finish), and after the clone as the final authority. The middle
guard exists because the first one failed live: GitHub rate-limited the
unauthenticated preflight and the worker spent two minutes checking out
94,854 files from torvalds/linux before the old post-clone check could run.
"""
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

from zeroth.config import settings
from zeroth.safety import RepoRejected


class IngestError(Exception):
    pass


def _dir_mb(path: Path) -> float:
    total = 0
    try:
        for f in path.rglob("*"):
            try:
                if f.is_file():
                    total += f.stat().st_size
            except OSError:
                continue
    except OSError:
        pass
    return total / 1_048_576


def clone(clone_url: str) -> Path:
    target = Path(tempfile.mkdtemp(prefix="zeroth-"))
    # Pack + working tree together overshoot the source size; 2x the cap is a
    # generous ceiling that still kills a runaway clone within seconds.
    ceiling_mb = settings.max_repo_mb * 2
    deadline = time.time() + 180

    proc = subprocess.Popen(
        ["git", "clone", "--depth", "1", "--single-branch", "--no-tags",
         clone_url, str(target)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    try:
        while proc.poll() is None:
            if _dir_mb(target) > ceiling_mb:
                proc.kill()
                proc.wait(timeout=10)
                shutil.rmtree(target, ignore_errors=True)
                raise RepoRejected(
                    f"Repository exceeds the {settings.max_repo_mb}MB limit — the clone "
                    f"was stopped once it passed {ceiling_mb}MB on disk."
                )
            if time.time() > deadline:
                proc.kill()
                proc.wait(timeout=10)
                shutil.rmtree(target, ignore_errors=True)
                raise IngestError("clone did not finish within 180s")
            time.sleep(2)
    except Exception:
        if proc.poll() is None:
            proc.kill()
        raise

    if proc.returncode != 0:
        stderr = proc.stderr.read() if proc.stderr else ""
        shutil.rmtree(target, ignore_errors=True)
        # git writes checkout progress to stderr; the cause is in the other lines.
        cause = " ".join(
            line.strip() for line in stderr.splitlines()
            if line.strip() and "Updating files:" not in line and "Receiving objects:" not in line
            and "Resolving deltas:" not in line
        )[:300]
        raise IngestError(f"clone failed: {cause or 'git exited non-zero'}")

    size_mb = _dir_mb(target)
    if size_mb > settings.max_repo_mb:
        shutil.rmtree(target, ignore_errors=True)
        raise RepoRejected(
            f"Repository is {size_mb:.0f}MB after clone; limit is {settings.max_repo_mb}MB.")

    return target


def cleanup(path: Path) -> None:
    shutil.rmtree(path, ignore_errors=True)
