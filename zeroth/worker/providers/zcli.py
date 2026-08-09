"""zCLI-backed provider.

Shelling out to zcli is deliberate: it is the interface Zerops documents and
keeps stable, and it avoids hand-rolling auth and polling against the REST API
under a 48-hour clock.

Everything below is confirmed against a real project on a live account
(zcli 1.1.0, 2026-08-08), not guessed from --help text:

- Flags are `-P/--project-id` and `-S/--service-id`, not `--projectId`/
  `--serviceId`.
- zcli has no ZEROPS_TOKEN env-var auth. `zcli login <token>` makes an
  authenticated call and persists a session file - that's the only way in.
- `project project-import`'s stdout never contains the project id, only
  per-service `stack.*` progress lines. The id has to be looked up
  afterward via `project list`.
- Import YAML's `buildFromGit` creates services but does NOT build them -
  they sit at READY_TO_DEPLOY forever. `zcli service deploy <hostname>`
  run against a *local* working directory (not the git URL) is what
  actually triggers a build. This means deploy() needs a local clone with
  zerops.yml written into it, not just project_id + zerops_yaml text.
- `zcli service log` returned nothing (build or runtime, either
  --message-type) across every real failure this file caused during
  development - the useful signal is `service deploy`'s own stdout/stderr
  and exit code, which log() below no longer relies on as the primary
  source.
- The live subdomain URL is `{hostname}_zeropsSubdomain` in
  `zcli project env -P <id>` output, e.g.
  `web_zeropsSubdomain="https://web-2b21-8080.prg1.zerops.app"`. Adding a
  `ports` entry changes this URL (a port suffix gets added), so it must be
  read after the deploy that declares the final port, not cached earlier.
- Not yet confirmed: whether `project delete --confirm` frees quota
  immediately, and the exact server-side reason a `service deploy` process
  can fail (zcli's own client-side log has no more detail than "identifier
  for communication with our support: <id>" - the Zerops GUI or support
  channel has more than the CLI exposes here).
"""
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import yaml

from zeroth.config import settings
from zeroth.worker.providers.base import DeployResult

ZEROPS_YAML_FILENAME = "zerops.yml"


class ZcliError(Exception):
    pass


class ZcliProvider:
    """One instance owns one zcli session, in its own throwaway HOME.

    `zcli login` writes a session file under HOME rather than taking auth per
    command, so a shared HOME would mean concurrent runs overwriting each
    other's credentials - and a run against a user's own account silently
    replacing Zeroth's. Each instance therefore gets an isolated HOME, which
    makes the account-targeted run safe and disposable. Call close() when done.
    """

    name = "zcli"

    def __init__(self, token: str | None = None) -> None:
        self._token = token or settings.zcli_token
        self._own_account = not token
        self._home: str | None = None
        self._logged_in = False

    def _env(self) -> dict:
        if self._home is None:
            self._home = tempfile.mkdtemp(prefix="zeroth-zcli-")
        env = dict(os.environ)
        env["HOME"] = self._home
        return env

    def _ensure_login(self) -> None:
        if self._logged_in:
            return
        if not self._token:
            raise ZcliError("no Zerops token available for this run")
        proc = subprocess.run(
            ["zcli", "login", self._token],
            capture_output=True, text=True, timeout=30, env=self._env(),
        )
        if proc.returncode != 0:
            source = "ZCLI_TOKEN" if self._own_account else "the token you supplied"
            raise ZcliError(
                f"zcli login failed — check {source}: "
                + self._scrub(proc.stderr.strip() or proc.stdout.strip())[:300]
            )
        self._logged_in = True

    def _run(self, args: list[str], timeout: int = 120, cwd: str | None = None) -> subprocess.CompletedProcess:
        self._ensure_login()
        return subprocess.run(
            ["zcli", *args], capture_output=True, text=True,
            timeout=timeout, cwd=cwd, env=self._env(),
        )

    def _scrub(self, text: str) -> str:
        """Keep the token out of anything that reaches the database or the UI.

        zcli echoes its arguments back on some failures, and build logs are
        persisted verbatim on every attempt.
        """
        return text.replace(self._token, "<redacted>") if self._token else text

    def close(self) -> None:
        """Drop the session file. Must never raise - it runs in a finally."""
        if self._home:
            shutil.rmtree(self._home, ignore_errors=True)
            self._home = None
        self._logged_in = False

    def create_project(self, import_yaml: str, project_name: str) -> str:
        # The name in the YAML is NOT used. Projects are looked up by name after
        # import, so honouring a repository's own project name means adopting
        # whatever already answers to it - a leftover from a previous failed run,
        # or worse, a real project of the user's with the same name. Zeroth then
        # deploys into someone else's project and reports "Service [x] not
        # found" when its services are absent. The name we pass is unique per
        # run, so the lookup can only ever resolve to the project this call made.
        doc = yaml.safe_load(import_yaml) or {}
        doc.setdefault("project", {})["name"] = project_name
        import_yaml = yaml.safe_dump(doc, sort_keys=False)

        with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False) as fh:
            fh.write(import_yaml)
            path = fh.name

        try:
            proc = self._run(["project", "project-import", path], timeout=settings.provision_timeout_s)
        finally:
            Path(path).unlink(missing_ok=True)

        if proc.returncode != 0:
            # An import can fail AFTER creating the project - services were
            # created, then something later in the queue failed. Raising without
            # cleaning up strands that project, which is exactly what the
            # teardown guarantee is supposed to prevent. Best effort: it must
            # not mask the original error.
            self._destroy_by_name(project_name)
            raise ZcliError(self._scrub(
                "import failed: " + "\n".join(x for x in (proc.stdout.strip(), proc.stderr.strip()) if x)))

        return self._find_project_id(project_name)

    def _destroy_by_name(self, name: str) -> None:
        try:
            self.destroy(self._find_project_id(name))
        except Exception:  # noqa: BLE001 - cleanup must never raise
            pass

    def _find_project_id(self, name: str) -> str:
        proc = self._run(["project", "list"], timeout=30)
        if proc.returncode != 0:
            raise ZcliError(f"could not list projects: {proc.stderr.strip() or proc.stdout.strip()}")
        for line in proc.stdout.splitlines():
            if "│" not in line:
                continue
            cells = [c.strip() for c in line.split("│") if c.strip()]
            if len(cells) >= 2 and cells[0] != "ID" and cells[1] == name:
                return cells[0]
        raise ZcliError(f"created project '{name}' but could not find its id in `zcli project list`")

    def deploy(self, project_id: str, repo_dir: Path, zerops_yaml: str,
               targets: list[tuple[str, str]] | None = None) -> DeployResult:
        # A repository may already ship its own Zerops config, and zcli picks
        # zerops.yaml over the zerops.yml we write - so ours was silently
        # ignored and the deploy ran against theirs, failing with "setup <name>
        # was not found". Confirmed against a real repository whose own file
        # declared prod/dev setups. Clear both spellings before writing, so the
        # configuration being verified is unambiguously the generated one.
        for existing in ("zerops.yml", "zerops.yaml"):
            (repo_dir / existing).unlink(missing_ok=True)
        (repo_dir / ZEROPS_YAML_FILENAME).write_text(zerops_yaml, encoding="utf-8")

        setups = [svc["setup"] for svc in (yaml.safe_load(zerops_yaml).get("zerops") or [])]
        if not setups:
            return DeployResult(
                ok=False, phase="schema", project_id=project_id,
                error="zerops.yml has no setups to deploy",
            )

        # A setup name is not a service name. Generated configuration happens to
        # use one name for both, but a repository writing its own config usually
        # does not - tlak declares setups prod/dev against services tlak/db, and
        # deploying "prod" as though it were a service fails with
        # "Service [prod] not found". Callers that know the real mapping pass it.
        if not targets:
            targets = [(setup, setup) for setup in setups]

        log_chunks = []
        for service, setup in targets:
            proc = self._run(
                ["service", "deploy", service, "-P", project_id, "--setup", setup,
                 "--working-dir", str(repo_dir)],
                timeout=settings.deploy_timeout_s,
            )
            log_chunks.append(f"--- {service} (setup: {setup}) ---\n{proc.stdout}\n{proc.stderr}")
            if proc.returncode != 0:
                combined = self._scrub("\n".join(log_chunks))
                return DeployResult(
                    ok=False, phase="runtime", project_id=project_id,
                    logs=combined[-8000:],
                    error=self._scrub(
                        _first_error(proc.stdout + proc.stderr) or f"{service} deploy failed"
                    ),
                )

        combined = self._scrub("\n".join(log_chunks))
        url = self._public_url(project_id, [svc for svc, _ in targets])
        return DeployResult(
            ok=True, phase="runtime", project_id=project_id,
            logs=combined[-8000:], url=url,
            verification={"source": "zcli", "deployed_setups": setups, "url": url},
        )

    def _public_url(self, project_id: str, setups: list[str]) -> str:
        proc = self._run(["project", "env", "-P", project_id], timeout=30)
        if proc.returncode != 0:
            return ""
        for line in proc.stdout.splitlines():
            for setup in setups:
                prefix = f"{setup}_zeropsSubdomain="
                if line.startswith(prefix):
                    return line[len(prefix):].strip().strip('"')
        return ""

    def logs(self, project_id: str, service: str = "") -> str:
        args = ["service", "log", "--project-id", project_id, "--limit", "200"]
        if service:
            args += ["--service-id", service]
        try:
            proc = self._run(args, timeout=45)
            return (proc.stdout or proc.stderr)[-8000:]
        except Exception as exc:  # noqa: BLE001
            return f"[zeroth] could not retrieve logs: {exc}"

    def verify(self, project_id: str, service: str = "") -> dict:
        return {"source": "zcli", "project": project_id}

    def destroy(self, project_id: str) -> None:
        try:
            self._run(["project", "delete", "--project-id", project_id, "--confirm"], timeout=90)
        except Exception:  # noqa: BLE001 - teardown must never raise
            pass


def _first_error(logs: str) -> str:
    for line in logs.splitlines():
        if re.search(r"error|exception|failed|refused|denied", line, re.I):
            return line.strip()[:400]
    return ""
