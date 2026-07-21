import { defineConfig, devices } from '@playwright/test'

// E2E runs against an ISOLATED stack so it never touches Neon or a signed-in
// dev server: a backend on 8001 with auth DISABLED (empty CLERK_* so load_dotenv
// can't re-enable it) + a throwaway SQLite DB, and the frontend on 3001 with the
// Clerk key blanked (renders past the sign-in gate) proxying /api → 8001.
const BACKEND = 'http://localhost:8001'
const FRONTEND = 'http://localhost:3001'

export default defineConfig({
  testDir: './e2e',
  fullyParallel: false,
  workers: 1, // one shared test DB → run serially
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  reporter: 'list',
  use: {
    baseURL: FRONTEND,
    reducedMotion: 'reduce', // skips the once-per-session intro + all loader animation
    trace: 'on-first-retry',
  },
  projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'] } }],
  webServer: [
    {
      command: 'python -m uvicorn src.api.main:app --host 127.0.0.1 --port 8001',
      url: `${BACKEND}/api/health`,
      reuseExistingServer: !process.env.CI,
      timeout: 60_000,
      env: {
        CLERK_ISSUER: '',
        CLERK_JWKS_URL: '',
        REQUIRE_AUTH: '',
        DATABASE_URL: 'sqlite:////tmp/et_e2e.db',
      },
    },
    {
      command: 'npx vite --port 3001 --strictPort',
      url: FRONTEND,
      reuseExistingServer: !process.env.CI,
      timeout: 60_000,
      env: {
        VITE_API_TARGET: BACKEND,
        VITE_CLERK_PUBLISHABLE_KEY: '',
      },
    },
  ],
})
