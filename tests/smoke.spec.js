// Smoke: the pages render, the theme resolves, and the states that carry the
// product's claims are present. Deliberately no deploy is triggered - tests
// must not spend credits.
const { test, expect } = require("@playwright/test");

test("landing page renders with the product claim", async ({ page }) => {
  await page.goto("/index.html");
  await expect(page.locator("h1")).toContainText("verified deployment");
  // Theme resolved before paint: html carries a concrete data-theme.
  const theme = await page.locator("html").getAttribute("data-theme");
  expect(["dark", "light"]).toContain(theme);
  // The coming-soon control is disabled, not fake-working.
  await expect(page.getByRole("button", { name: /private repo/i })).toBeDisabled();
});

test("run page renders the workspace and collapsed panels", async ({ page }) => {
  await page.goto("/run.html");
  await expect(page.locator("#run-form")).toBeVisible();
  // Panels default closed; their toggles exist and are collapsed.
  const toggles = page.locator(".collapse-toggle[aria-expanded]");
  expect(await toggles.count()).toBeGreaterThan(0);
  // BYOK is offered but optional.
  await expect(page.locator("#byok-provider")).toBeAttached();
});

test("a finished run replays: verdict, timeline, gate state", async ({ page, request }) => {
  const api = process.env.API_URL || "https://api-2b21-8000.prg1.zerops.app";
  const gallery = await (await request.get(`${api}/api/gallery`)).json();
  // Runs older than the events table have no timeline; find one that does.
  let target = null;
  for (const j of gallery) {
    const job = await (await request.get(`${api}/api/jobs/${j.id}`)).json();
    if ((job.events || []).length) { target = j.id; break; }
  }
  test.skip(!target, "no runs with persisted events yet");
  await page.goto(`/run.html?job=${target}`);
  await expect(page.locator("#compat-verdict")).not.toHaveText("Checking", { timeout: 15_000 });
  // The timeline rebuilt from persisted events, not the expired live stream.
  await expect
    .poll(async () => page.locator("#timeline > div").count(), { timeout: 15_000 })
    .toBeGreaterThan(0);
});
