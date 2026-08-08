"""Fingerprint -> deployment manifest.

The model reasons over extracted facts, never over source code. Its output is
JSON validated against MANIFEST_SCHEMA before anything is provisioned.
"""
import json

from jsonschema import Draft7Validator

from zeroth.llm import complete_json
from zeroth.worker.fingerprint import Fingerprint
from zeroth.worker.manifest_schema import ALLOWED_TYPES, MANIFEST_SCHEMA

SYSTEM = """You are a deployment architect for Zerops, a container platform.

You are given FACTS extracted from a repository by a deterministic analyzer.
You never see application source code. Design the smallest Zerops architecture
that will actually run this application.

Rules:
- Use only these service types: {types}
- One runtime service for the application. Add a database service only if the
  facts show one is used. Add a cache only if the facts show one is used.
  Add a worker service only if the facts show a background worker.
- hostname must be lowercase alphanumeric, 2-25 chars: api, db, cache, worker, web.
- Exactly one service has "public": true - the one serving HTTP.
- Every service needs a "reason" quoting the specific fact that justifies it.
- build_commands and start_command must be real shell commands for the runtime.
- For Django use gunicorn; for FastAPI use uvicorn binding 0.0.0.0.
- Reference other services by hostname (e.g. db) - Zerops resolves these on the
  private network.
"""

USER = """FACTS:
{facts}

Return JSON:
{{
  "project_name": "short-slug",
  "summary": "one sentence describing the architecture",
  "services": [
    {{
      "hostname": "api",
      "type": "python@3.12",
      "role": "api",
      "reason": "quote the fact that justifies this service",
      "public": true,
      "port": 8000,
      "build_commands": ["pip install -r requirements.txt"],
      "start_command": "gunicorn config.wsgi --bind 0.0.0.0:8000",
      "env": {{"DATABASE_URL": "${{db_connectionString}}"}}
    }}
  ]
}}"""


class AnalysisError(Exception):
    pass


def _facts_block(fp: Fingerprint) -> str:
    lines = [
        f"repository: {fp.repo_name}",
        f"language: {fp.language}",
        f"runtime_version: {fp.runtime_version or 'not declared'}",
        f"framework: {fp.framework or 'none detected'}",
        f"databases: {fp.databases or 'none'}",
        f"caches: {fp.caches or 'none'}",
        f"background worker: {fp.has_worker}",
        f"ports: {fp.ports}",
        f"env vars declared: {fp.env_vars[:25]}",
        f"config files present: {fp.present_files}",
        f"root entries: {fp.tree[:30]}",
        f"compose services: {fp.compose_services or 'none'}",
        "",
        "evidence:",
    ]
    lines += [f"  - {f.key}={f.value}  ({f.evidence})" for f in fp.facts]
    return "\n".join(lines)


def validate(manifest: dict) -> list[str]:
    validator = Draft7Validator(MANIFEST_SCHEMA)
    errors = [
        f"{'/'.join(str(p) for p in e.path) or 'root'}: {e.message}"
        for e in validator.iter_errors(manifest)
    ]
    services = manifest.get("services") or []
    if not any(s.get("public") for s in services):
        errors.append("root: no service is marked public")
    if sum(1 for s in services if s.get("public")) > 1:
        errors.append("root: more than one service marked public")
    hostnames = [s.get("hostname") for s in services]
    if len(hostnames) != len(set(hostnames)):
        errors.append("root: duplicate hostnames")
    for svc in services:
        if svc.get("type") not in ALLOWED_TYPES:
            errors.append(f"{svc.get('hostname')}: unsupported type {svc.get('type')!r}")
    return errors


def analyze(fp: Fingerprint) -> dict:
    system = SYSTEM.format(types=", ".join(sorted(ALLOWED_TYPES)))
    user = USER.format(facts=_facts_block(fp))
    manifest = complete_json(system, user, max_tokens=2500)

    errors = validate(manifest)
    if errors:
        # One corrective round-trip: schema errors are cheap to fix and the
        # model repairs its own output far more reliably than we can patch it.
        manifest = complete_json(
            system,
            user + "\n\nYour previous answer was invalid:\n"
            + "\n".join(f"- {e}" for e in errors)
            + f"\n\nPrevious answer:\n{json.dumps(manifest)[:1500]}\n\nReturn corrected JSON.",
            max_tokens=2500,
        )
        errors = validate(manifest)
        if errors:
            raise AnalysisError("; ".join(errors))
    return manifest
