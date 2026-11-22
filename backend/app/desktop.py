"""Desktop launcher (the Windows .exe entry point): local server + daily scheduler + browser.

Data lives in %LOCALAPPDATA%\\SMBT (smbt.db, settings.env, smbt.log), so reinstalling or upgrading keeps it.
Listens on 127.0.0.1 only, so no access token is needed. Close the window to stop.
"""

import logging
import os
import socket
import sys
import threading
import time
import urllib.request
import webbrowser
from pathlib import Path

PORT = 8765
SETTINGS_TEMPLATE = """# SMBT desktop settings. Edit, save, then restart SMBT.
# Optional free keys improve data quality (see docs/DATA_SOURCES.md):
FRED_API_KEY=
TIINGO_API_KEY=
# Optional: enables the AI analyst page.
ANTHROPIC_API_KEY=
# Optional: historical S&P 500 membership CSV (date,tickers) to remove breadth survivorship bias.
SP500_HISTORY_URL=
"""


def data_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / ".local" / "share")
    d = Path(base) / "SMBT"
    d.mkdir(parents=True, exist_ok=True)
    return d


def load_settings(path: Path) -> None:
    if not path.exists():
        path.write_text(SETTINGS_TEMPLATE, encoding="utf-8")
    for line in path.read_text(encoding="utf-8").splitlines():
        k, sep, v = line.partition("=")
        if sep and not line.lstrip().startswith("#") and v.strip():
            os.environ.setdefault(k.strip(), v.strip())


def healthy(port: int) -> bool:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=1) as r:
            return r.status == 200
    except OSError:
        return False


def free_port(preferred: int) -> int:
    for port in [preferred, *range(preferred + 1, preferred + 20)]:
        with socket.socket() as s:
            if s.connect_ex(("127.0.0.1", port)) != 0:
                return port
    raise RuntimeError("no free local port found")


def main() -> int:
    d = data_dir()
    if os.name == "nt":
        import ctypes

        ctypes.windll.kernel32.SetConsoleTitleW("SMBT - close this window to stop")
    if healthy(PORT):  # already running: just bring up the UI
        webbrowser.open(f"http://127.0.0.1:{PORT}/")
        return 0
    load_settings(d / "settings.env")
    os.environ.setdefault("DATABASE_URL", f"sqlite:///{(d / 'smbt.db').as_posix()}")
    os.environ.setdefault("COOKIE_SECURE", "false")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s",
                        handlers=[logging.FileHandler(d / "smbt.log", encoding="utf-8"), logging.StreamHandler(sys.stdout)])
    log = logging.getLogger("smbt.desktop")

    import uvicorn
    from apscheduler.schedulers.background import BackgroundScheduler

    from .cli import add_schedule
    from .data.ingest import load_sample_data
    from .db import init_db, kv_get, session
    from .main import _bg_ingest, app

    init_db()
    s = session()
    first_run = not (kv_get(s, "data_version") or {}).get("v")
    if first_run:
        log.info("first run: loading bundled sample data so the app works immediately")
        load_sample_data(s)
    s.close()

    port = free_port(PORT)
    url = f"http://127.0.0.1:{port}/"
    sched = BackgroundScheduler(timezone="America/New_York")
    add_schedule(sched, _bg_ingest)  # same lock as the in-app "Run ingestion now" button
    sched.start()

    def after_start():
        for _ in range(100):
            if healthy(port):
                break
            time.sleep(0.2)
        print(f"\n  SMBT is running at {url}\n  Data folder: {d}\n  Keep this window open while you use the app; close it to stop.\n", flush=True)
        if os.environ.get("SMBT_NO_BROWSER") != "1":
            webbrowser.open(url)
        log.info("refreshing market data in the background (first run takes several minutes)")
        _bg_ingest()

    threading.Thread(target=after_start, daemon=True).start()
    uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")).run()
    sched.shutdown(wait=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
