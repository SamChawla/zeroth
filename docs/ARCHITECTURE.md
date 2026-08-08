# Zeroth — architecture

## Thesis

Config generators are easy and mostly worthless: plausible YAML that does not
boot is worse than no YAML. Zeroth's product is the **verification loop** —
config that has been deployed to a real Zerops project and watched come up.

The loop is deliberately *not* on the default path. Provisioning costs minutes
and credits, and a user who has not read the config yet has no reason to want
either spent. So the pipeline stops with the configuration in hand, and
verification is a second phase that only runs when someone asks for it.

## Pipeline

```
PHASE A — always runs, provisions nothing, finishes in seconds
GitHub URL
  ├─ safety      admission control: host allowlist, size preflight, private-IP refusal
  ├─ ingest      shallow clone (--depth 1 --single-branch), size re-check
  ├─ fingerprint DETERMINISTIC. facts + evidence. No source code leaves here.
  ├─ analyze     model reasons over facts only → manifest JSON
  ├─ validate    JSON Schema + semantic checks        ← FAILURE CLASS 1 (local, free)
  ├─ generate    Jinja → import YAML + zerops.yaml, parsed before use
  └─ READY       configuration returned; nothing provisioned

PHASE B — only on explicit request (POST /api/jobs/{id}/verify)
  └─ pathfinder  target: throwaway project | the user's own account
       ├─ create                                      ← FAILURE CLASS 2 (import rejected)
       ├─ deploy + poll (circuit breaker)             ← FAILURE CLASS 3 (built, did not run)
       ├─ logs + verify
       ├─ on failure → diagnose → patch → retry (max 2)
       ├─ destroy failed attempts (finally, always)
       └─ keep the passing project ONLY when the target is the user's account
     emit        bundle + DEPLOYMENT.md with the full evidence and repair trail
```

## Verification targets

`ephemeral` runs on Zeroth's own credentials and is always destroyed.
`account` runs on a token the user supplies for that single request: it is
stashed in Valkey under a TTL, read once by the worker, and deleted on read —
never persisted to Postgres, never rendered into an artifact, and scrubbed out
of any log line it might otherwise reach.

Because `zcli login` persists a session file under `HOME` rather than
authenticating per command, each provider instance gets its own throwaway
`HOME`. Without that, a run against a user's account would overwrite Zeroth's
own session, and two concurrent runs would race for the same credentials.

## The three failure classes

This is the technical spine, and the strongest thing to say to a judge.

| Class | Caught at | Cost | Repair |
|---|---|---|---|
| Schema | Local validation | Milliseconds | Model corrects its own JSON |
| Infrastructure | Project import | One API call | Model patches manifest |
| Runtime | Build/verify | Full build cycle | Model reads logs, patches manifest |

Catching each at the cheapest level available is what separates a tool from a
demo. Note that class 1 needs no provisioning at all — the repair loop is
demonstrable even with zero credits.

## Deterministic first, model second

`fingerprint.py` is a rules engine. It parses manifests, lockfiles,
`docker-compose.yml`, `Dockerfile`, `.env.example`, `Procfile` and produces
`Fact(key, value, evidence)` triples. Only facts reach the model.

Consequences:
- The model cannot hallucinate a dependency that isn't declared.
- Every generated service answers *why?* with a filename.
- Token cost is bounded regardless of repo size.
- The analysis stage is unit-testable without an API key.

## Templates render YAML, never the model

The model emits JSON validated against a schema; Jinja renders the YAML; the
generator parses its own output before returning it. This removes an entire
failure class (indentation, quoting, invalid keys) and makes attempt-to-attempt
diffs readable.

## Zerops services

| Service | Type | Why it cannot be removed |
|---|---|---|
| `web` | static | The demo surface. Judges open a URL, not a terminal. |
| `api` | python@3.12 | Intake, SSE relay, bundle download |
| `worker` | python@3.12 | Clone/analyze/deploy are minutes long; they cannot block HTTP |
| `db` | postgresql@16 | Jobs, attempts, artifacts, gallery |
| `cache` | valkey@7.2 | Queue, event fan-out, rate limit, concurrency cap |

Only `web` and `api` are public; the rest is private network. Autoscaling is
real, not staged: concurrent pathfinder runs fan out across worker replicas.

## Transport abstraction

`ZeropsProvider` is a Protocol. Implementations: `SimulatedProvider` (offline,
deliberately fails attempt 1) and `ZcliProvider`. If the spike shows ZCP MCP is
the better path, add a third implementation and change one factory line —
nothing above the interface knows the difference.

## Guards

- Teardown in `finally` on every path, plus tagged projects for sweeping.
- Circuit breaker: a deploy stuck past `DEPLOY_TIMEOUT_S` is terminated, never
  left holding a worker slot.
- Global concurrency cap and per-IP hourly limit in Valkey.
- Repository code never executes on the worker.
- No platform secrets in ephemeral project environments.
