# Deploying to Render

The app ships as **one Docker service**: FastAPI serves both the `/api` backend
and the built React frontend from a single origin (so there's no CORS and just
one URL in production).

## What's in the box
- `Dockerfile` — multi-stage: builds the React app (`node:24-slim`, `npm run
  build` → `dist/`), then runs the API on `python:3.11-slim` and serves `dist/`.
- `render.yaml` — Render Blueprint: one `web` service, `env: docker`,
  health check `/api/health`.
- `.dockerignore` — keeps secrets (`.env`), local DBs (`*.db`), `node_modules`,
  `.venv`, tests, etc. out of the image.

## Steps

1. **Push to GitHub** (make sure `.env` and `*.db` are gitignored — they are):
   ```bash
   git add -A && git commit -m "Deploy: single-service Docker for Render" && git push
   ```

2. **Create the service on Render**
   - Render Dashboard → **New +** → **Blueprint**.
   - Connect your GitHub repo. Render detects `render.yaml` and proposes the
     `expense-tracker` web service.

3. **Set the environment variables** (Render prompts for these because they are
   `sync: false` — they are never stored in the repo):
   - **`DATABASE_URL`** — your Neon Postgres connection string, e.g.
     `postgresql://<user>:<pass>@<host>/<db>?sslmode=require`. Use the **same**
     Neon URL as your local setup so production shares your existing data.
     *(If you skip this, the app falls back to an in-container SQLite file that
     resets on every deploy — fine for a demo, not for real persistence.)*
   - **`GEMINI_API_KEY`** — your Google Gemini key (for the AI assistant /
     auto-categorize / insights). Optional: if unset, those endpoints return
     `503` and the rest of the app works normally.
   - *(Optional)* `GEMINI_MODEL` to override the default `gemini-2.5-flash`.

4. **Deploy.** Render builds the Docker image and starts the service. The health
   check hits `/api/health`.

5. **Open the URL** Render gives you (e.g. `https://expense-tracker.onrender.com`).
   The React app loads at `/`, deep links like `/dashboard` work on refresh (SPA
   fallback), and the API is under `/api` on the same origin.

## Notes
- **Same origin, no CORS**: because FastAPI serves the SPA, the browser calls
  `/api/...` on the same host — no cross-origin config needed in prod.
- **Data**: reusing the Neon `DATABASE_URL` means local and prod share one
  database. The schema is created/migrated automatically on startup
  (`create_all` + the portable `_migrate_add_*` helpers).
- **Seeding stays off**: `SEED_DB` is not set, so prod starts from your real
  data, not the sample dataset.
- **Free plan** spins down when idle; the first request after idle is slow.
  Bump `plan:` in `render.yaml` to `starter` to avoid that.
