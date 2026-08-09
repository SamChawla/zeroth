# E2E tests

```bash
npm i -D @playwright/test
npx playwright install chromium        # or set CHROME_PATH to a system Chrome
BASE_URL=https://<web-subdomain> npx playwright test
```

The suite is read-only by design: it never presses deploy, so running it costs
nothing and cannot leave infrastructure behind.
