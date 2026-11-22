# Deployment

One container, one process: FastAPI serves the API, the `/ws` WebSocket and the static PWA from the same origin, and runs the background loops (stream hub, macro refresh, breadth, provisional scores, session alerts). There is no database, volume, worker or user store. Restarting the container loses only in-memory caches, which refill from the providers.

Read [DATA_SOURCES.md §2](DATA_SOURCES.md#2-licensing-and-access-models) before exposing the server to anyone but yourself: `ACCESS_MODE=public` serves only providers you are licensed to display publicly.

## 1. Docker Compose on a small VPS

Requirements: Docker, a domain, ports 80/443 open. 1 vCPU / 1–2 GB RAM is enough (more with breadth enabled: ~500 constituent histories in memory).

```bash
git clone <your repo> smbt && cd smbt
cp .env.example .env          # add provider keys; set ACCESS_MODE / PUBLIC_DISPLAY_LICENSES
docker compose up -d --build
docker compose exec api python -m app.cli check   # live check of every configured provider and stream
```

The compose file runs the container read-only with a tmpfs `/tmp`; nothing is written to disk.

**Run exactly one instance.** The process holds the upstream stream connections and live state. Several replicas would each open their own provider streams (Alpaca allows one stream connection per account on most plans) and would not share subscriptions. Scale vertically.

### HTTPS and WebSockets

The PWA needs HTTPS to install on iPhone and to use notifications. Caddy (automatic certificates, proxies WebSockets without extra config):

```
# /etc/caddy/Caddyfile
markets.example.com {
    encode gzip
    reverse_proxy 127.0.0.1:8000
}
```

Set `API_PORT=127.0.0.1:8000` in `.env` so the container is reachable only through Caddy. This matters: the server trusts `X-Forwarded-For` for per-IP rate limits, which a client could spoof if the port were public. With nginx, forward `Upgrade`/`Connection` headers for `/ws` and raise `proxy_read_timeout` above 60 s (the server sends status every 5 s).

## 2. Managed platforms

Any host that runs a Docker image works (Fly.io, Render, Railway, a cloud VM): this `Dockerfile`, port 8000, health check `/api/health`, one instance, WebSockets enabled, keys in the platform's secret store (never in the image). No database add-on is needed.

## 3. Local development

```bash
cd backend && python -m venv .venv && . .venv/bin/activate && pip install -r requirements.txt pytest
cp ../.env.example ../.env       # add at least one equity provider key for live data
python -m app.cli check          # verify keys and streams
uvicorn app.main:app --reload    # :8000
cd ../frontend && npm install && npm run dev    # :3000, proxies /api to :8000 (set API_URL to change)
```

`npm run dev` proxies `/api` only; for live quotes in dev, open the built app from FastAPI (`npm run build`, then `http://127.0.0.1:8000`), which serves `frontend/out/` and `/ws` on one origin.

Tests: `python -m pytest -q` (backend; includes the real-browser test when `frontend/out` exists and Node can load Playwright), `npm run typecheck && npm test` (frontend).

## 4. Windows desktop app

`SMBT-Setup.exe` (built by the `windows-app` workflow) installs per user and runs in `personal` mode on `127.0.0.1:8765`. On first start it creates `%LOCALAPPDATA%\SMBT\settings.env` listing every provider key; add yours, save, restart SMBT. Only that settings file is written; logs go to the app's console window, and no market data is stored.

## 5. Installing on devices

- **iPhone (iOS 16.4+)**: open the HTTPS URL in Safari → Share → *Add to Home Screen*. Allow notifications on the Alerts page from the installed app. Alerts fire only while the app is open (no push service or stored subscription).
- **Windows**: Chrome or Edge → install icon in the address bar.
- The phone reconnects the WebSocket and resubscribes as soon as the app returns to the foreground or the network comes back.

## 6. Security checklist

- Keys only in `.env` or platform secrets; the browser never receives them, and they never appear in logs or error messages.
- HTTPS only; container port bound to localhost behind the proxy.
- Public deployments: `ACCESS_MODE=public` and only the licences you actually hold in `PUBLIC_DISPLAY_LICENSES`.
- Per-IP in-memory rate limits on the API, backtests and the AI analyst (`RATE_LIMIT_PER_MIN`, `HEAVY_RATE_LIMIT_PER_MIN`, `ANALYST_PER_HOUR`); each browser connection may stream at most `MAX_SYMBOLS_PER_CLIENT` symbols.
- The AI analyst only receives data computed by this app (no browsing, no code execution).
- No accounts, cookies, analytics or tracking. `/api/*` responses are `Cache-Control: no-store`.

## 7. Configuration reference

All variables with defaults: [DATA_SOURCES.md §3](DATA_SOURCES.md#3-environment-variables). Template: `.env.example`.
