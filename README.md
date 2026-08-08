# Zeroth

**From repository to verified deployment.**

Paste a public repository URL. Zeroth reads it, decides the Zerops
architecture, writes the configuration, deploys it to a throwaway Zerops
project, watches it build, and repairs the configuration when it fails — then
hands back config it has actually watched boot.

Most config generators hand you plausible YAML. Plausible YAML that does not
boot is worse than none, so Zeroth proves it first.

## How it works

```
repository
   ↓  deterministic analyzer — no source code leaves the worker
fingerprint (facts + evidence)
   ↓  model reasons over facts, emits JSON
manifest  →  validated against schema locally     ← failure class 1
   ↓  Jinja templates render the YAML
zerops-project-import.yaml + zerops.yaml
   ↓  ephemeral Zerops project                    ← failure class 2
deploy, read logs, verify                          ← failure class 3
   ↓  on failure: diagnose → patch → redeploy (max 3)
verified bundle + DEPLOYMENT.md + teardown
```

### Why failures are classified

A schema error costs milliseconds and no provisioning. An import rejection
costs one API call. A runtime failure costs a full build. Catching each at the
cheapest possible level is the difference between a demo and a tool.

### Why the model never reads source code

`zeroth/worker/fingerprint.py` extracts facts from manifests, lockfiles,
`docker-compose.yml`, `Dockerfile` and `.env.example` — each with the evidence
that produced it. Only those facts reach the model. Every generated service can
therefore answer *why is this here?* with a filename.

## Architecture on Zerops

| Service | Type | Role |
|---|---|---|
| `web` | static | UI, live route view, gallery |
| `api` | python@3.12 | Job intake, SSE relay, bundle download |
| `worker` | python@3.12 | Clone, fingerprint, analyze, deploy, repair |
| `db` | postgresql@16 | Jobs, attempts, artifacts |
| `cache` | valkey@7.2 | Job queue, event fan-out, rate limits, concurrency cap |

Only `web` and `api` are public. Everything else talks over the private network.

## Run it yourself

```bash
zcli project project-import import.yaml
```

## Local development

```bash
pip install uv
uv pip install --system -r requirements.txt
cp .env.example .env          # PATHFINDER_PROVIDER=simulated needs no credentials
uvicorn zeroth.api.main:app --reload --port 8000
python -m zeroth.worker.main  # second terminal
python -m zeroth.scripts.seed_gallery  # optional: populate the showcase with sample runs
python -m http.server 5173 -d web
```

The simulated provider runs the whole pipeline without provisioning anything,
and deliberately fails its first attempt so the repair loop can be exercised
offline. Switch `PATHFINDER_PROVIDER=zcli` once credentials are in place.

The web UI is three static pages: `index.html` is the showcase (completed
runs, shown first), `run.html` starts a new run or replays a finished one via
`?job=<id>`, and `about.html` is the reference page — pipeline, failure
classes, services, safety guards.

## Safety

- Public GitHub/GitLab only, size-capped, private addresses refused.
- Repository code never executes on the Zeroth worker — only inside the
  isolated ephemeral project.
- Teardown runs in a `finally` block on every path.
- Global concurrency cap and per-IP rate limit, so a burst of traffic cannot
  drain credits.
- No platform secrets are placed in ephemeral project environments.
