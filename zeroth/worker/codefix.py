"""AI-drafted code fix, on explicit request.

Zeroth's standing rule is that no source code reaches the model - analysis
runs on facts. Drafting a concrete patch is the one thing that genuinely
requires reading code, so it is a separate, opt-in step with a narrow scope:
ONLY the files the findings cite (plus the dependency manifest and the
entrypoint), capped in size, on a button that says exactly that. The result
is a unified diff handed to the owner - Zeroth still never writes to anyone's
repository.
"""
import logging
from pathlib import Path

from zeroth import llm

log = logging.getLogger("zeroth.codefix")

MAX_FILES = 6
MAX_FILE_BYTES = 6_000

SYSTEM = """You are helping make a repository deployable on Zerops. You will
get deployability findings and the relevant files. Produce the SMALLEST code
change that fixes the findings.

Rules:
- Output a JSON object: {"explanation": "...", "diff": "..."}.
- "diff" is a valid unified diff against the given files (--- a/path,
  +++ b/path). Only touch the files provided. Create a new file only if a
  finding says one is missing.
- Do not invent dependencies or services the code does not already use.
- Bind 0.0.0.0 rather than localhost; read ports and connection strings from
  environment variables.
- Keep the explanation to a few sentences, one per change."""


def _cited_files(repo_dir: Path, fingerprint: dict, compatibility: dict) -> list[Path]:
    """The files the findings talk about - not the repository."""
    names: list[str] = []
    for f in (compatibility or {}).get("findings", []):
        for token in (f.get("evidence", "") + " " + f.get("detail", "")).replace(",", " ").split():
            clean = token.strip(".;:()").lstrip("/")
            if "." in clean and len(clean) < 60 and "/" not in clean[:1]:
                names.append(clean)
    names += ["requirements.txt", "package.json", "Procfile", ".env.example"]
    names += [e.split("/")[-1] for e in (fingerprint or {}).get("entrypoints", [])]

    subdir = (fingerprint or {}).get("project_subdir") or ""
    roots = [repo_dir / subdir] if subdir and (repo_dir / subdir).is_dir() else [repo_dir]

    out, seen = [], set()
    for root in roots:
        for name in names:
            path = root / name
            if path.is_file() and path.suffix not in (".png", ".jpg", ".ico") and str(path) not in seen:
                seen.add(str(path))
                out.append(path)
            if len(out) >= MAX_FILES:
                return out
    return out


def draft(repo_dir: Path, fingerprint: dict, compatibility: dict) -> dict:
    findings = [
        f for f in (compatibility or {}).get("findings", [])
        if f.get("level") in ("blocker", "fatal", "change")
    ]
    if not findings:
        return {"explanation": "Nothing actionable to fix.", "diff": ""}

    files = _cited_files(repo_dir, fingerprint, compatibility)
    parts = ["Findings:"]
    for i, f in enumerate(findings, 1):
        parts.append(f"{i}. {f['title']} - {f['detail']}")
    parts.append("\nFiles (the only ones you may change):")
    for path in files:
        rel = path.relative_to(repo_dir)
        try:
            body = path.read_text(encoding="utf-8", errors="replace")[:MAX_FILE_BYTES]
        except OSError:
            continue
        parts.append(f"\n--- {rel} ---\n{body}")

    result = llm.complete_json(SYSTEM, "\n".join(parts), max_tokens=3000)
    return {
        "explanation": str(result.get("explanation", ""))[:2000],
        "diff": str(result.get("diff", ""))[:12000],
    }
