# Zeroth

**From repository to verified deployment.**

Paste a public repository URL. Zeroth tells you whether it can be deployed to
Zerops at all, writes the configuration for it, and — if you ask — deploys that
configuration for real, watches it build, and repairs it when it fails.

Most config generators hand you plausible YAML. Plausible YAML that does not
boot is worse than none, so Zeroth will prove it.

---

## How it works

Two phases. The first is free, provisions nothing, and finishes in seconds. The
second only happens when you ask for it.

```
repository
   ↓  admission control — host allowlist, size cap, private addresses refused
   ↓  shallow clone
fingerprint (facts + evidence)      deterministic; no source code leaves the worker
   ↓
DEPLOYABILITY  deployable | needs changes | not deployable
   ↓  model reasons over facts, emits JSON
manifest  →  validated against schema locally      ← failure class 1
   ↓  Jinja templates render the YAML
zerops-project-import.yaml + zerops.yaml
   ↓
READY — configuration in hand, nothing provisioned
   ↓  ← you press "Try it out", and pick where it lands
real Zerops project                                ← failure class 2
deploy, read logs, verify                          ← failure class 3
   ↓  on failure: diagnose → patch → redeploy (max 2)
verified bundle + DEPLOYMENT.md
```

### Is this repository even deployable?

That is the first question anyone actually has, so it is the first one Zeroth
answers — before spending a model call on *how* to deploy it.

`zeroth/worker/compatibility.py` judges the fingerprint against the runtimes the
generator is permitted to emit and returns one of three verdicts:

| Verdict | Meaning |
|---|---|
| **Deployable** | Runs on Zerops as it stands |
| **Needs changes** | Will deploy, but something should be fixed first — each change is named |
| **Not deployable** | No Zerops runtime for this stack |

It checks runtime support and version availability, whether a dependency
manifest exists, whether anything declares how to start the application, port
binding, and containerisation. The finding that matters most in practice is a
**database in the dependencies with no connection string read from the
environment** — the exact shape of application that works on a laptop and fails
on every platform.

This stage is deliberately *not* a model call. These are the checks that can be
decided from evidence, so the stage costs nothing, cannot invent a blocker, and
is testable without an API key. Genuinely ambiguous questions are left to the
analyze stage rather than guessed at here.

### Why verification is opt-in

Deploying costs minutes and credits, and most people want to read the
configuration before anything is provisioned on their behalf. Stopping at
`READY` makes the common path fast and free, and turns the expensive part into a
deliberate act.

### Where a verification run lands

| Target | What happens | Teardown |
|---|---|---|
| Throwaway project | Provisioned on Zeroth's own account | Always destroyed |
| Your Zerops account | Provisioned with a token you supply for that one run | Kept if it came up; failed attempts always destroyed |

The token is read once from a short-TTL key and deleted on read — never written
to the database, the logs, or the downloadable bundle — and each run gets an
isolated `zcli` session, because `zcli login` persists a session file rather
than authenticating per command. Without that isolation a run against your
account would overwrite Zeroth's own session, and concurrent runs would race for
the same credentials.

### Why failures are classified

A schema error costs milliseconds and no provisioning. An import rejection costs
one API call. A runtime failure costs a full build. Catching each at the cheapest
possible level is the difference between a demo and a tool.

### Why the model never reads source code

`zeroth/worker/fingerprint.py` extracts facts from manifests, lockfiles,
`docker-compose.yml`, `Dockerfile` and `.env.example` — each with the evidence
that produced it. Only those facts reach the model. Every generated service can
therefore answer *why is this here?* with a filename.

## Architecture on Zerops

| Service | Type | Role |
|---|---|---|
| `web` | `python@3.12` | UI: showcase, live run view, generated configuration |
| `api` | `python@3.12` | Job intake, SSE relay, bundle download |
| `worker` | `python@3.12` | Clone, fingerprint, assess, analyze, deploy, repair |
| `db` | `postgresql@16` | Jobs, attempts, artifacts |
| `cache` | `valkey@7.2` | Job queue, event fan-out, rate limits, concurrency cap |

Only `web` and `api` are public; everything else talks over the private network.

`web` is a runtime rather than `type: static` — the static base 502'd with no
logs across several attempts, and serving the same files from `python -m
http.server` was a confirmed path rather than a guess. The reasoning is kept in
`zerops.yaml` next to the setup it explains.

Because `web` is served from its own origin, the browser cannot reach the API by
relative path. `web/config.js` carries the API base URL — checked in with a
localhost default for development, and overwritten at build time with the
deployed `api` subdomain.

## HTTP API

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/api/jobs` | Submit a repository. Rejects unsupported hosts and oversized repositories here, before any job exists |
| `GET` | `/api/jobs/{id}` | Full record: fingerprint, verdict, manifest, artifacts, attempts |
| `POST` | `/api/jobs/{id}/verify` | Deploy a generated configuration. `{"target": "ephemeral"}` or `{"target": "account", "token": "..."}` |
| `GET` | `/api/jobs/{id}/events` | SSE stream, with replay for a browser that connects late |
| `GET` | `/api/jobs/{id}/bundle` | Downloadable bundle |
| `GET` | `/api/gallery` | Public completed runs |
| `GET` | `/healthz` | Liveness |

## Run it yourself

```bash
zcli project project-import import.yaml
```

Then set the credentials. **Set them as project-level environment variables**,
and note the gotcha: `import.yaml` declares `EURI_API_KEY`, `GROQ_API_KEY` and
`ZCLI_TOKEN` under `envSecrets` so the names are versioned without the values.
Those are *service-scoped* and service scope **shadows** project scope — an
empty service-level variable silently wins over a populated project-level one,
and the application reports no provider configured. Either fill the values in at
the service level, or delete the empty service-level entries so the project
values resolve.

| Variable | Needed for |
|---|---|
| `GROQ_API_KEY` or `EURI_API_KEY` | The analyze and repair stages. Without one, a run fails at `analyzing` |
| `ZCLI_TOKEN` | Deploying to the throwaway project. Not needed for the user's-own-account path |
| `PATHFINDER_PROVIDER` | `simulated` (default) or `zcli` |

## Local development

```bash
pip install uv
uv pip install --system -r requirements.txt
cp .env.example .env          # PATHFINDER_PROVIDER=simulated needs no credentials
uvicorn zeroth.api.main:app --reload --port 8000
python -m zeroth.worker.main            # second terminal
python -m zeroth.scripts.seed_gallery   # optional: sample runs for the showcase
python -m http.server 5173 -d web       # third terminal
```

`web/config.js` already points at `http://localhost:8000`, so the UI on 5173
reaches the API without further configuration.

The simulated provider runs the whole pipeline without provisioning anything and
deliberately fails its first attempt, so the repair loop can be exercised
offline. Switch `PATHFINDER_PROVIDER=zcli` once credentials are in place.

The UI is three static pages: `index.html` is the showcase, `run.html` starts a
run or replays a finished one via `?job=<id>`, and `about.html` is the reference
page. A finished run is rebuilt from the stored record rather than the event
stream, so a link keeps working long after the replay buffer has expired.

## Safety

- Public GitHub/GitLab only, size-capped, private addresses refused — all
  enforced at submit time, before a job is created.
- Repository code never executes on the Zeroth worker; it is only ever built
  inside the deployment target.
- Teardown runs in a `finally` block on every path. A project is kept only when
  it succeeded *and* you asked for it to land in your own account.
- Global concurrency cap and a per-IP hourly limit, keyed on the forwarded
  client address rather than the platform proxy — otherwise every visitor shares
  one bucket.
- No platform secrets are placed in ephemeral project environments.

## Built with

Zeroth was developed inside **ZCP**, Zerops' control-plane container, using
**Claude** as the coding agent driving the `zerops_*` MCP tooling — provisioning,
deploying, reading logs and verifying services against the live project rather
than against a local mock. The failure modes documented in `zerops.yaml` and
`zeroth/worker/providers/zcli.py` were all found that way: on a real account,
against a real deployment.
