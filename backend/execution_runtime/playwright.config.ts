import { defineConfig } from "@playwright/test";

const generatedDir = process.env.PETF_PLAYWRIGHT_TEST_DIR ?? "./generated/playwright";
const artifactsDir = process.env.PETF_PLAYWRIGHT_ARTIFACTS_DIR ?? "./artifacts/playwright";
const browserChannel = process.env.PETF_PLAYWRIGHT_BROWSER_CHANNEL ?? process.env.EXECUTION_BROWSER_CHANNEL ?? "msedge";
const webServerCommand = process.env.PETF_PLAYWRIGHT_WEB_SERVER_COMMAND;
const webServerUrl = process.env.PETF_PLAYWRIGHT_WEB_SERVER_URL ?? "http://127.0.0.1:41731/calculator";

export default defineConfig({
  testDir: generatedDir,
  outputDir: `${artifactsDir}/test-results`,
  webServer: webServerCommand
    ? {
        command: webServerCommand,
        url: webServerUrl,
        reuseExistingServer: false,
        timeout: 10_000,
      }
    : undefined,
  reporter: [
    ["list"],
    ["json", { outputFile: `${artifactsDir}/results.json` }],
    ["html", { outputFolder: `${artifactsDir}/html-report`, open: "never" }],
  ],
  use: {
    channel: browserChannel,
    headless: true,
    screenshot: "only-on-failure",
    trace: "retain-on-failure",
    video: "retain-on-failure",
  },
});
