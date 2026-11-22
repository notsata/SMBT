from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[2]

# Tickers the scanner covers. This is product configuration (which names to look at), not market data:
# sector, market cap and every price come from the providers at runtime.
DEFAULT_UNIVERSE = (
    "SPY,QQQ,IWM,DIA,RSP,XLK,XLF,XLE,XLV,XLY,XLP,XLI,XLB,XLU,XLRE,XLC,SMH,SOXX,IGV,XBI,KRE,XHB,ITB,EEM,EFA,TLT,IEF,HYG,LQD,GLD,USO,"
    "AAPL,MSFT,NVDA,AVGO,AMD,INTC,MU,TSM,ORCL,CRM,ADBE,GOOG,META,NFLX,AMZN,TSLA,HD,MCD,NKE,COST,WMT,PG,KO,JPM,BAC,GS,V,MA,BRK.B,"
    "UNH,LLY,JNJ,MRK,ABBV,XOM,CVX,CAT,GE,BA,UNP,LIN,NEE,PLTR,COIN"
)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=(ROOT / ".env", ".env"), extra="ignore")

    # ---- access & licensing -------------------------------------------------------------------
    # personal: the operator's own use (desktop app, private server); every configured provider is shown.
    # public: anonymous visitors; data is shown only from providers listed in public_display_licenses
    #         (the operator attests it holds display/redistribution rights for them). See docs/DATA_SOURCES.md.
    access_mode: Literal["personal", "public"] = "personal"
    public_display_licenses: str = ""

    # ---- providers (all keys stay server-side) ---------------------------------------------------
    alpaca_api_key_id: str = ""
    alpaca_api_secret_key: str = ""
    alpaca_feed: Literal["iex", "sip", "delayed_sip"] = "iex"
    alpaca_data_url: str = "https://data.alpaca.markets"
    alpaca_stream_url: str = "wss://stream.data.alpaca.markets/v2"

    massive_api_key: str = ""
    massive_realtime: bool = False  # plan includes real-time stocks (Advanced/Business); else the 15-min delayed socket
    massive_indices: bool = False  # Indices plan (I:VIX values)
    massive_options: bool = False  # Options plan with chain snapshots (greeks, IV, open interest)
    massive_rest_url: str = "https://api.massive.com"
    massive_ws_realtime_url: str = "wss://socket.massive.com"
    massive_ws_delayed_url: str = "wss://delayed.massive.com"

    tiingo_api_key: str = ""
    tiingo_url: str = "https://api.tiingo.com"
    fred_api_key: str = ""
    fmp_api_key: str = ""
    fmp_url: str = "https://financialmodelingprep.com/stable"
    cboe_url: str = "https://cdn.cboe.com/api/global/us_indices/daily_prices"

    stream_providers: str = "alpaca,massive"  # priority order; failover only to a configured, licensed provider
    history_providers: str = "massive,tiingo,alpaca"

    anthropic_api_key: str = ""
    analyst_model: str = "claude-opus-5-5"

    # ---- product configuration -----------------------------------------------------------------
    benchmarks: str = "SPY,QQQ"
    scanner_universe: str = DEFAULT_UNIVERSE
    history_start: str = "1993-01-29"
    breadth_enabled: bool = True
    frontend_dist: str = ""

    # ---- runtime limits ------------------------------------------------------------------------
    max_symbols_per_client: int = 12
    upstream_unsubscribe_grace_s: int = 60
    stale_regular_s: int = 120  # no event for this long during regular hours on a subscribed symbol -> STALE
    stale_extended_s: int = 1800
    score_refresh_s: int = 60
    breadth_refresh_s: int = 120
    http_min_interval_s: float = 0.25
    rate_limit_per_min: int = 120
    heavy_rate_limit_per_min: int = 6  # backtests, signal reports
    analyst_per_hour: int = 10
    commission_bps: float = 1.0
    slippage_bps: float = 2.0

    @staticmethod
    def _list(v: str) -> list[str]:
        return [s.strip() for s in v.split(",") if s.strip()]

    @property
    def benchmark_list(self) -> list[str]:
        return [s.upper() for s in self._list(self.benchmarks)]

    @property
    def universe(self) -> list[str]:
        return [s.upper() for s in self._list(self.scanner_universe)]

    @property
    def licensed(self) -> set[str]:
        return {s.lower() for s in self._list(self.public_display_licenses)}

    @property
    def stream_order(self) -> list[str]:
        return [s.lower() for s in self._list(self.stream_providers)]

    @property
    def history_order(self) -> list[str]:
        return [s.lower() for s in self._list(self.history_providers)]

    @property
    def has_alpaca(self) -> bool:
        return bool(self.alpaca_api_key_id and self.alpaca_api_secret_key)


@lru_cache
def get_settings() -> Settings:
    return Settings()
