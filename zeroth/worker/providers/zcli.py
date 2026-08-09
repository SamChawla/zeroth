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
import time
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import httpx
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

    # Optional progress callback set by the caller (pathfinder wires it to the
    # run's event stream). Long blocking phases announce themselves before
    # starting, and the probe loop heartbeats - ten silent minutes reads as a
    # hang no matter how healthy the run is.
    on_progress = None

    def _progress(self, text: str) -> None:
        cb = self.on_progress
        if cb:
            try:
                cb(text)
            except Exception:  # noqa: BLE001 - progress must never break the run
                pass

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
        # Rewrite the name in the TEXT, not by re-serialising. Round-tripping
        # through yaml.safe_dump drops comments, and a leading
        # `# zeropsPreprocessor=on` is not a comment to Zerops - it is what
        # enables <@generateRandomString(...)> macros. Losing it leaves those
        # macros as literal strings and the import fails with a support id and
        # nothing else. Confirmed against a repository that uses them.
        import_yaml = _rename_project(import_yaml, project_name)

        with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False) as fh:
            fh.write(import_yaml)
            path = fh.name

        # An import that builds from git runs a real build inside the import, so
        # it needs the build budget, not the provisioning one. A plain import is
        # quick either way; this ceiling only matters when a build is happening.
        budget = settings.provision_timeout_s
        if "buildFromGit" in import_yaml:
            budget = max(budget, settings.deploy_timeout_s)

        self._progress(f"importing the project — can take up to {budget}s under load")
        try:
            proc = self._run(["project", "project-import", path], timeout=budget)
        except subprocess.TimeoutExpired:
            # A timeout leaves a real project behind exactly like a failed
            # import does, and it used to leak because cleanup only ran on a
            # non-zero exit code.
            self._destroy_by_name(project_name)
            cause = ("a buildFromGit import runs the application's full build"
                     if "buildFromGit" in import_yaml
                     else "the platform can queue imports under load")
            raise ZcliError(
                f"import did not finish within {budget}s ({cause}). Nothing was "
                f"deployed; the partially-created project was cleaned up. Try again."
            ) from None
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

    def _commit_config(self, repo_dir: Path) -> None:
        """Best effort: make the written zerops.yml part of the tree push reads."""
        if not (repo_dir / ".git").exists():
            return
        for args in (["add", "-A"],
                     ["-c", "user.name=zeroth", "-c", "user.email=zeroth@localhost",
                      "commit", "-m", "zeroth: configuration under verification", "--no-verify"]):
            subprocess.run(["git", "-C", str(repo_dir), *args],
                           capture_output=True, text=True, timeout=30)

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
               targets: list[tuple[str, str]] | None = None,
               routes: list[str] | None = None) -> DeployResult:
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

        # `zcli push` runs the platform's build pipeline; `zcli service deploy`
        # ships the directory as an ALREADY-BUILT artifact, so buildCommands
        # never ran and every deploy died server-side with nothing but a
        # support id. Bisected on a one-file Flask app: identical yaml fails
        # via service deploy and boots via push. push reads committed state,
        # so the zerops.yml written a moment ago is committed first.
        self._commit_config(repo_dir)

        log_chunks = []
        for service, setup in targets:
            self._progress(
                f"building and deploying {service} — the platform build runs now "
                f"(up to {settings.deploy_timeout_s}s)")
            proc = self._run(
                ["push", service, "-P", project_id, "--setup", setup,
                 "--workingDir", str(repo_dir)],
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

        # Re-bind the subdomain AFTER the deploy. The import enabled it on a
        # service that had no ports yet; the push just declared the real one,
        # and the route from before does not follow it - the app sits healthy
        # behind an eternal 502. The git-build path never hits this because
        # its import queues the enable AFTER the build. Confirmed live:
        # gunicorn listening in the runtime logs while the subdomain 502'd
        # for minutes.
        for service, _ in targets:
            self._progress(f"binding the public subdomain for {service}")
            self._run(["service", "enable-subdomain", service, "-P", project_id],
                      timeout=120)

        # A build that succeeded and a deploy that activated still is not the
        # claim being sold. "Verified" means it answers, so the deploy path
        # ends the same way the git-build path does: polling the URL.
        probe = self.await_git_build(project_id, [svc for svc, _ in targets], routes=routes)
        probe.logs = (combined + "\n" + probe.logs)[-8000:]
        if probe.ok:
            probe.verification["deployed_setups"] = setups
        return probe


    def _runtime_logs(self, project_id: str, services: list[str]) -> str:
        """Runtime logs for the repair loop. This is the crash traceback -
        without it a diagnosis is a guess about a URL that did not answer."""
        chunks = []
        try:
            listing = self._run(["service", "list", "-P", project_id], timeout=30)
            for line in listing.stdout.splitlines():
                cells = [c.strip() for c in line.split("│") if c.strip()]
                if len(cells) >= 2 and cells[1] in services:
                    proc = self._run(["service", "log", "-P", project_id, "-S", cells[0],
                                      "--limit", "60"], timeout=45)
                    if proc.stdout.strip():
                        chunks.append(f"--- {cells[1]} runtime ---\n{proc.stdout[-3000:]}")
        except Exception:  # noqa: BLE001 - diagnostics must not replace the error
            pass
        return "\n".join(chunks)

    def await_git_build(self, project_id: str, services: list[str],
                        routes: list[str] | None = None) -> DeployResult:
        """Verify a buildFromGit import by checking the application answers.

        A buildFromGit import already builds and deploys the application - the
        import command blocks until its queued processes finish. Deploying our
        local clone on top of that was both redundant and a different claim:
        the repository says "import this and it runs", so the honest check is
        whether it now answers over HTTP.
        """
        url = self._public_url(project_id, services)
        if not url:
            return DeployResult(
                ok=False, phase="runtime", project_id=project_id,
                error="the import finished but no public URL exists for the runtime service",
            )

        self._progress("deployed — waiting for the application to answer")
        last = "no response"
        started = time.time()
        next_beat = started + 30
        # Boot + LB registration. 180s was calibrated on a quiet platform;
        # under load a fresh container can take minutes to route, and giving
        # up early misreports a healthy app as a failed one.
        # Observed live: the app booted (runtime logs show gunicorn listening)
        # while the fresh subdomain still 502'd for minutes - LB registration
        # for a brand-new project lags the boot under load.
        deadline = time.time() + 420
        while time.time() < deadline:
            try:
                resp = httpx.get(url, timeout=10, follow_redirects=True)
                if resp.status_code < 500:
                    checks = [{"path": "/", "status": resp.status_code,
                               "ok": resp.status_code < 500}]
                    # Application-level smoke checks: every parameter-free GET
                    # route the fingerprint found in the sources. A 4xx on a
                    # route the app itself declares is a finding, not noise.
                    for route in (routes or []):
                        if route == "/":
                            continue
                        try:
                            r2 = httpx.get(url.rstrip("/") + route, timeout=10,
                                           follow_redirects=True)
                            checks.append({"path": route, "status": r2.status_code,
                                           "ok": r2.status_code < 400})
                        except httpx.HTTPError as exc:
                            checks.append({"path": route, "status": 0, "ok": False,
                                           "error": str(exc)[:120]})
                    return DeployResult(
                        ok=True, phase="runtime", project_id=project_id, url=url,
                        logs="\n".join(f"GET {c['path']} -> {c['status']}" for c in checks),
                        verification={"source": "zcli", "http": resp.status_code,
                                      "url": url, "checks": checks},
                    )
                last = f"HTTP {resp.status_code}"
            except httpx.HTTPError as exc:
                last = str(exc)[:200]
            if time.time() >= next_beat:
                elapsed = int(time.time() - started)
                self._progress(
                    f"still waiting for the application to answer — {elapsed}s, last: {last}")
                next_beat += 30
            time.sleep(10)
        self._progress("no answer within the window — collecting runtime logs for diagnosis")
        return DeployResult(
            ok=False, phase="runtime", project_id=project_id, url=url,
            error=f"the application never answered at {url} ({last})",
            logs=self._runtime_logs(project_id, services),
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

def _rename_project(import_yaml: str, name: str) -> str:
    """Set project.name, preserving every other byte of the document."""
    out, in_project, renamed = [], False, False
    for line in import_yaml.splitlines():
        stripped = line.strip()
        if not renamed and re.match(r"^project:\s*$", line):
            in_project = True
            out.append(line)
            continue
        if in_project and not renamed and re.match(r"^\s+name:\s", line):
            indent = line[: len(line) - len(line.lstrip())]
            out.append(f"{indent}name: {name}")
            renamed = True
            continue
        # Any top-level key ends the project block.
        if in_project and stripped and not line[0].isspace() and not stripped.startswith("#"):
            in_project = False
        out.append(line)
    if not renamed:
        out.insert(0, f"project:\n  name: {name}")
    return "\n".join(out) + "\n"
