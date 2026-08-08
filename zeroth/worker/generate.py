"""Manifest -> Zerops configuration.

Templates render the YAML; the model never emits YAML directly. This removes
an entire class of failure (indentation, quoting, invalid keys) and makes the
output diffable between attempts.
"""
from datetime import datetime, timezone
from pathlib import Path

import yaml
from jinja2 import Environment, FileSystemLoader, StrictUndefined

from zeroth.config import settings

TEMPLATE_DIR = Path(__file__).resolve().parent.parent / "templates"

_env = Environment(
    loader=FileSystemLoader(TEMPLATE_DIR),
    undefined=StrictUndefined,
    trim_blocks=True,
    lstrip_blocks=True,
    keep_trailing_newline=True,
)


class GenerationError(Exception):
    pass


# The manifest schema marks these service fields OPTIONAL, but the templates
# reference them directly under StrictUndefined - so a manifest that
# legitimately omits one would raise at render time and be reported as an
# infrastructure failure, which is both wrong and expensive to diagnose.
# Filling them in here is deliberate: loosening the templates instead would
# also swallow a genuinely missing hostname or type, which must still be loud.
SERVICE_DEFAULTS = {
    "reason": "",
    "public": False,
    "port": 0,
    "build_commands": [],
    "start_command": "",
    "env": {},
}


def _with_defaults(manifest: dict) -> dict:
    filled = dict(manifest)
    filled.setdefault("summary", "")
    filled["services"] = [
        {**SERVICE_DEFAULTS, **service} for service in (manifest.get("services") or [])
    ]
    return filled


def render_import_yaml(manifest: dict, repo_url: str, verified: bool = False) -> str:
    note = "deployed and verified by Zeroth" if verified else "not yet verified"
    text = _env.get_template("import.yaml.j2").render(
        manifest=_with_defaults(manifest), repo_url=repo_url, verified_note=note
    )
    _assert_parses(text, "zerops-project-import.yaml")
    return text


def render_zerops_yaml(manifest: dict, repo_url: str) -> str:
    text = _env.get_template("zerops.yaml.j2").render(
        manifest=_with_defaults(manifest), repo_url=repo_url
    )
    _assert_parses(text, "zerops.yaml")
    return text


def render_report(job, fp: dict, manifest: dict, runs: list, result: tuple[str, str]) -> str:
    headline, detail = result
    return _env.get_template("deployment.md.j2").render(
        job=job,
        fp=fp,
        manifest=_with_defaults(manifest),
        runs=runs,
        result_headline=headline,
        result_detail=detail,
        public_url=settings.zeroth_public_url,
        generated_at=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
    )


def _assert_parses(text: str, name: str) -> None:
    """Never hand the platform YAML we have not parsed ourselves."""
    try:
        yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise GenerationError(f"{name} is not valid YAML: {exc}") from exc
