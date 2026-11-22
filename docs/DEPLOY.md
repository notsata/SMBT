# Deployment

One container image serves the API **and** the static PWA from the same origin (simplest auth, no CORS, service worker scope `/`). A second container from the same image runs the scheduler. PostgreSQL with TimescaleDB stores history (plain PostgreSQL also works; SQLite for local dev).

## 1. Docker Compose on a small VPS (recommended)

Requirements: Docker, a domain name, ports 80/443 open. 1 vCPU / 2 GB RAM is enough.

```bash
git clone <your repo> smbt && cd smbt
cp .env.example .env
python3 -c "import secrets;print(secrets.token_urlsafe(32))"   # -> APP_ACCESS_TOKEN
# set APP_ACCESS_TOKEN, POSTGRES_PASSWORD, optional FRED/Tiingo/Anthropic keys
docker compose run --rm api python -m app.cli vapid             # paste VAPID_* into .env for push
docker compose up -d --build
docker compose logs -f worker                                   # first run ingests history (~10 min for 600 symbols)
```

The worker runs ingestion → score recomputation → alert evaluation at **18:15 America/New_York, Mon–Fri** (after vendors publish EOD data), plus a Saturday catch-up. Trigger manually from Settings → "Run ingestion now" or `docker compose exec api python -m app.cli ingest`.

### HTTPS (required)

Service workers, installation and Web Push need HTTPS, and iOS will not install a non-HTTPS PWA. Put Caddy in front (automatic Let's Encrypt):

```
# /etc/caddy/Caddyfile
markets.example.com {
    encode gzip
    reverse_proxy 127.0.0.1:8000
}
```

Keep `COOKIE_SECURE=true`. Bind the API to localhost only (`API_PORT=127.0.0.1:8000` in `.env`) when Caddy runs on the same host.

### Backups

```bash
docker compose exec db pg_dump -U smbt smbt | gzip > smbt-$(date +%F).sql.gz
```

Everything can be re-ingested except alerts, watchlist, settings and imported series — back those up.

## 2. Managed platforms

Any host that runs a Docker image + managed Postgres works (Fly.io, Render, Railway, a cloud VM):

- Web service: this `Dockerfile`, port 8000, health check `/api/health`.
- Worker: same image, command `python -m app.cli worker`, exactly **one** instance (it holds the schedule).
- Database: managed PostgreSQL 15+; set `DATABASE_URL=postgresql+psycopg://user:pass@host:5432/db`. TimescaleDB is used when the extension is available, otherwise plain tables.
- Secrets: set env vars in the platform's secret store, never in the image.

## 3. Local development

```bash
cd backend && python -m venv .venv && . .venv/bin/activate && pip install -r requirements.txt pytest
python -m app.cli sample            # bundled real 1999-2018 data, works offline
uvicorn app.main:app --reload       # SQLite at backend/smbt.db by default
cd ../frontend && npm install && npm run dev    # :3000, proxies /api -> :8000 (API_URL to change)
```

Production frontend build: `npm run build` writes `frontend/out/`, which FastAPI serves automatically when present.

## 4. Installing on devices

- **iPhone (iOS 16.4+)**: open the HTTPS URL in Safari → Share → *Add to Home Screen* → open SMBT from the home screen → sign in → Alerts → *Enable notifications*. Push only works from the installed app, not a Safari tab. iOS may delay or batch notifications (Focus, Low Power).
- **Windows**: Chrome or Edge → install icon in the address bar (or menu → *Install SMBT*). Enable notifications on the Alerts page.
- Each device signs in once with `APP_ACCESS_TOKEN` (HttpOnly cookie, 90 days). Watchlist, alerts and settings live on the server, so all devices stay in sync. Rotating the token signs out every device.

## 5. Security checklist

- `APP_ACCESS_TOKEN` set (the API logs a warning when it is empty; without it anyone who finds the URL can read and change your alerts).
- HTTPS only; `COOKIE_SECURE=true`.
- Login is rate-limited (10 failures / 15 min / IP); use a long random token.
- API keys only in `.env` / platform secrets; they are never logged or returned by the API.
- The AI analyst only receives data computed by this app (no browsing, no code execution) and requests go to the Anthropic API with the server-side refusal fallback enabled.
- Single-user design: everyone with the token shares one workspace. Multi-user accounts would need real user management.

## 6. Configuration reference

See `.env.example`. Notable: `BENCHMARKS` (default `SPY,QQQ`), `UNIVERSE_FILE` (scanner list, default `backend/app/universe.csv` with sector/cap metadata), `SP500_HISTORY_URL`, `COMMISSION_BPS`, `SLIPPAGE_BPS`, `MAX_ALERTS_PER_DAY`, `STALE_AFTER_DAYS`, `ANALYST_MODEL`.
