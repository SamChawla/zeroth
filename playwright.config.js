// E2E tests against a deployed Zeroth instance.
//
//   BASE_URL=https://your-web-subdomain npx playwright test
//
// Defaults to the hackathon deployment. CHROME_PATH lets CI or a container
// reuse a system Chrome instead of downloading a browser.
const { defineConfig } = require("@playwright/test");

module.exports = defineConfig({
  testDir: "./tests",
  timeout: 60_000,
  retries: 1,
  use: {
    baseURL: process.env.BASE_URL || "https://web-2b21-8080.prg1.zerops.app",
    ...(process.env.CHROME_PATH
      ? { launchOptions: { executablePath: process.env.CHROME_PATH } }
      : {}),
  },
});
