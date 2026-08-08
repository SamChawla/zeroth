# Screenshots

Four images, referenced from the root `README.md`. Capture at **1440px wide** in
a window with no browser chrome if possible, and use the **dark theme** for the
run views (the terminal and code panels read better) — the landing shot can be
either, whichever looks stronger.

| File | What it should show |
|---|---|
| `landing.png` | The landing page from the top: eyebrow, headline, the repository input, and enough of the five-step workflow strip below it to see steps 04–05 marked opt-in. |
| `run-ready.png` | A finished analysis in the `ready` state: the deployability verdict card at the top with at least one finding, and the "Try it out" panel with both targets visible. This is the money shot — it shows the verdict *and* the fact that nothing was deployed. |
| `run-verified.png` | A completed verification: the green "Deployment verified" card with the Status / Attempts / Environment / Verification row, and the attempt trail behind it. If a repair happened, even better — it shows the loop working. |
| `verifications.png` | The Recent verifications table on the landing page with a few real rows in it. |

## Producing a good `run-verified.png`

This one needs a real verification to have run and passed, which needs
`ZCLI_TOKEN` set and credits available. Failing that, a run against the
simulated provider still produces a genuine verified card — it deliberately
fails its first attempt and passes the second, so the repair trail is real
rather than staged.

Do not crop out the failure states if a run had them. The product's claim is
that it reports what actually happened, and a screenshot that only ever shows
green undercuts that.
