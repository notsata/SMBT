from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=(ROOT / ".env", ".env"), extra="ignore")

    database_url: str = f"sqlite:///{ROOT / 'backend' / 'smbt.db'}"
    # Auth is enforced whenever this is set. Always set it for any deployment reachable from the internet.
    app_access_token: str = ""
    cookie_secure: bool = True

    tiingo_api_key: str = ""
    fred_api_key: str = ""
    anthropic_api_key: str = ""
    analyst_model: str = "claude-opus-5-5"

    vapid_public_key: str = ""
    vapid_private_key: str = ""
    vapid_subject: str = "mailto:admin@localhost"

    benchmarks: str = "SPY,QQQ"
    universe_file: str = str(ROOT / "backend" / "app" / "universe.csv")
    sp500_constituents_url: str = (
        "https://raw.githubusercontent.com/datasets/s-and-p-500-companies/main/data/constituents.csv"
    )
    # Optional point-in-time membership file: CSV with columns date,tickers (comma-separated), e.g. fja05680/sp500.
    sp500_history_url: str = ""
    sample_data_dir: str = str(ROOT / "sample-data")
    frontend_dist: str = ""  # serve the static Next.js export from FastAPI when set / present at frontend/out
    breadth_enabled: bool = True

    http_min_interval_s: float = 0.4
    stale_after_days: int = 4
    commission_bps: float = 1.0
    slippage_bps: float = 2.0
    max_alerts_per_day: int = 10

    @property
    def benchmark_list(self) -> list[str]:
        return [s.strip().upper() for s in self.benchmarks.split(",") if s.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
