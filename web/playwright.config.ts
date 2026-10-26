import { defineConfig, devices } from "@playwright/test";

const PORT = 8797;
export const BASIC_PORT = 8798;
const UPSTREAM = 8795, BASIC_UPSTREAM = 8796;
const worker = (port: number, upstream: number) => ({ command: `../worker/scripts/e2e-dev.sh ${port} ${upstream}`,
  url: `http://127.0.0.1:${port}/`, reuseExistingServer: false, timeout: 120_000 });
const upstream = (port: number, mode = "") => ({ command: `node ../worker/test/replay-upstream.ts ${port} ${mode}`,
  port, reuseExistingServer: false });

export default defineConfig({
  testDir: "e2e",
  timeout: 30_000,
  use: { baseURL: `http://127.0.0.1:${PORT}`, trace: "retain-on-failure" },
  projects: [{ name: "desktop", use: { ...devices["Desktop Chrome"] } }],
  webServer: [upstream(UPSTREAM), upstream(BASIC_UPSTREAM, "basic"), worker(PORT, UPSTREAM), worker(BASIC_PORT, BASIC_UPSTREAM)],
});
