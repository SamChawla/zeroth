"""Repository admission control.

Untrusted repositories never execute on the Zeroth worker. The worker only
reads files and makes controlled API calls; the repo is only ever built
inside an isolated ephemeral Zerops project.
"""
import ipaddress
import re
from urllib.parse import urlparse

import httpx

from zeroth.config import settings

ALLOWED_HOSTS = {"github.com", "www.github.com", "gitlab.com", "www.gitlab.com"}
_REPO_RE = re.compile(r"^/([\w.-]+)/([\w.-]+?)(?:\.git)?/?$")


class RepoRejected(Exception):
    pass


def normalise(repo_url: str) -> tuple[str, str, str]:
    """Return (clone_url, owner, repo) or raise RepoRejected."""
    url = repo_url.strip()
    if not url.startswith(("http://", "https://")):
        url = "https://" + url

    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()

    if host not in ALLOWED_HOSTS:
        raise RepoRejected(
            f"Only public GitHub and GitLab repositories are supported (got '{host}')."
        )

    # Defence in depth: refuse anything that resolves to a literal private address.
    try:
        if ipaddress.ip_address(host).is_private:
            raise RepoRejected("Private addresses are not accepted.")
    except ValueError:
        pass

    match = _REPO_RE.match(parsed.path)
    if not match:
        raise RepoRejected("URL does not look like a repository path (/owner/repo).")

    owner, repo = match.groups()
    return f"https://{host}/{owner}/{repo}.git", owner, repo


def preflight_size(owner: str, repo: str) -> None:
    """Check repo size before cloning. A 4GB repo would kill the worker."""
    try:
        resp = httpx.get(
            f"https://api.github.com/repos/{owner}/{repo}",
            timeout=10,
            headers={"Accept": "application/vnd.github+json"},
        )
    except httpx.HTTPError:
        return  # GitLab or API hiccup: fall through to the clone-time guard.

    if resp.status_code == 404:
        raise RepoRejected("Repository not found, or it is private.")
    if resp.status_code != 200:
        return

    data = resp.json()
    if data.get("private"):
        raise RepoRejected("Private repositories are not supported.")

    size_mb = (data.get("size") or 0) / 1024
    if size_mb > settings.max_repo_mb:
        raise RepoRejected(
            f"Repository is {size_mb:.0f}MB; the limit is {settings.max_repo_mb}MB."
        )
