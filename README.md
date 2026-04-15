# Tigrao RADIO Backend

Minimal backend with FastAPI + SQLite + Spotify OAuth + Telegram bot (aiogram).

## Environment variables

Only these runtime environment variables are used:

- `TELEGRAM_BOT_TOKEN`
- `SPOTIFY_CLIENT_ID`
- `SPOTIFY_CLIENT_SECRET`

Create a local `.env` file from `.env.example`.

## Local setup

1. Create and activate a virtualenv.
2. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```
3. Run the backend:
   ```bash
   python app/bootstrap.py
   ```
4. Verify health endpoint:
   ```bash
   curl http://localhost:8000/healthz
   ```

SQLite DB is created automatically at `./data/app.db`.

## Spotify login flow

1. Start the app.
2. Open:
   - `http://localhost:8000/spotify/login`
3. Complete Spotify auth.
4. Spotify redirects to `/callback` and tokens are saved in SQLite.

> For Telegram `/login`, the bot points users to the backend `/spotify/login` route.

## Deployment (Railway)

Railway deployment is configured in `railway.json`:

- start command: `python app/bootstrap.py`
- healthcheck path: `/healthz`

### GitHub Actions auto deploy

Workflow file: `.github/workflows/deploy.yml`

On push to `main`, it:

1. installs dependencies,
2. validates code via `python -m compileall app`,
3. deploys via Railway CLI.

Required GitHub secrets:

- `RAILWAY_TOKEN`
- `RAILWAY_PROJECT_ID`
- `RAILWAY_SERVICE`

After secrets are set, deploy runs automatically without manual deployment steps.
