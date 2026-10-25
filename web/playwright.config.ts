import { defineConfig, devices } from "@playwright/test";

const PORT = 8797;
export const BASIC_PORT = 8798;
export default defineConfig({
  testDir: "e2e",
  timeout: 30_000,
  use: { baseURL: `http://127.0.0.1:${PORT}`, trace: "retain-on-failure" },
  projects: [{ name: "desktop", use: { ...devices["Desktop Chrome"] } }],
  webServer: [
    { command: `uv run --project ../pipeline python ../pipeline/tests/serve_replay.py ${PORT} .output/public`,
      url: `http://127.0.0.1:${PORT}/`, reuseExistingServer: false },
    { command: `uv run --project ../pipeline python ../pipeline/tests/serve_replay.py ${BASIC_PORT} .output/public basic`,
      url: `http://127.0.0.1:${BASIC_PORT}/`, reuseExistingServer: false },
  ],
});
