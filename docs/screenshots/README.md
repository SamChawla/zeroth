# Screenshots

Seven images, referenced from the root `README.md`. They are **crops of a single
full-page capture** of one real run — `SamChawla/zeroth-flask-demo`, verified on
the first attempt against an ephemeral project — rather than seven separate
shots. One capture keeps the theme, the viewport and the run consistent across
every image in the README, and means nothing can be quietly staged between them.

Capture at **1920px wide**, full page, no browser chrome. Crop the landing shot
to the full width (the nav spans it); crop everything else to the content
column, `x = 409 … 1515`.

| File | Section it appears in | What it shows |
|---|---|---|
| `landing.png` | Top of the README | Nav, hero, repository input, and the four constraints enforced at submit time |
| `pipeline-stages.png` | The Pipeline | The three stage cards — analyze, generate, verify — each with its own status, above the replay timeline |
| `generated-config.png` | Analyze | The rendered `zerops-project-import.yaml`, marked verified, beside the services panel |
| `why-services.png` | Analyze | Each generated service traced back to the evidence that produced it |
| `verdict.png` | Deterministic stage 1 | The deployability verdict, its findings, and the generated fix prompt |
| `event-log.png` | Live run streaming | The full transcript, timestamped, from clone to `verified=true` |
| `verified.png` | Verification with teeth | The verified card: status, attempts, environment, checks passed |

## Re-capturing

A verified run needs `ZCLI_TOKEN` set and credits available. Failing that, a run
against the simulated provider still produces a genuine trail — it deliberately
fails its first attempt and passes the second, so `event-log.png` and
`verified.png` show a real repair rather than a staged one.

Do not crop out the failure states if a run had them. The product's claim is
that it reports what actually happened, and a screenshot that only ever shows
green undercuts that.
