"""Desktop launcher (the Windows .exe entry point): runs the terminal on 127.0.0.1 in personal mode and opens
the browser. Provider API keys are read from %LOCALAPPDATA%\\SMBT\\settings.env (operator configuration; no market
data or user data is stored). Close the window to stop."""

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
SETTINGS_TEMPLATE = """# SMBT desktop settings. Add your provider keys, save, then restart SMBT.
# Without at least one price provider the app shows UNAVAILABLE instead of data. See docs/DATA_SOURCES.md.
#
# Streaming + recent history (free Basic plan: real-time IEX, 30 symbols; or ALPACA_FEED=sip / delayed_sip)
ALPACA_API_KEY_ID=
ALPACA_API_SECRET_KEY=
ALPACA_FEED=iex
# Long dividend-adjusted daily history for backtests (free key)
TIINGO_API_KEY=
# Alternative/extra: Massive (formerly Polygon.io). Set the flags to match your plan.
MASSIVE_API_KEY=
MASSIVE_REALTIME=false
MASSIVE_INDICES=false
MASSIVE_OPTIONS=false
# Macro first-release vintages (free key)
FRED_API_KEY=
# Fundamentals, market cap, sector, S&P 500 membership
FMP_API_KEY=
# AI analyst
ANTHROPIC_API_KEY=
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
    if healthy(PORT):
        webbrowser.open(f"http://127.0.0.1:{PORT}/")
        return 0
    load_settings(d / "settings.env")
    os.environ.setdefault("ACCESS_MODE", "personal")  # local, single user: personal-use provider plans apply
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s", handlers=[logging.StreamHandler(sys.stdout)])

    import uvicorn

    from .main import app

    port = free_port(PORT)
    url = f"http://127.0.0.1:{port}/"

    def after_start():
        for _ in range(150):
            if healthy(port):
                break
            time.sleep(0.2)
        print(f"\n  SMBT is running at {url}\n  Provider keys: {d / 'settings.env'}\n  Keep this window open while you use the app; close it to stop.\n", flush=True)
        if os.environ.get("SMBT_NO_BROWSER") != "1":
            webbrowser.open(url)

    threading.Thread(target=after_start, daemon=True).start()
    uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")).run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
