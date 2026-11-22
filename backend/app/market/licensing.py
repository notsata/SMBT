"""Display policy. In public mode a value reaches anonymous visitors only when every provider in its lineage is
licensed for public display by the operator (PUBLIC_DISPLAY_LICENSES). Raw values and analytics derived from
them are treated the same way, because exchange and vendor derived-data rules vary.

"fred" covers public-domain FRED series; copyrighted third-party series carry "fred-third-party" and need the
owner's permission (FRED's terms say FRED itself cannot grant it).
"""

from ..config import get_settings
from .model import LicenseBlocked

NAMES = {
    "alpaca": "Alpaca Markets",
    "massive": "Massive (formerly Polygon.io)",
    "tiingo": "Tiingo",
    "fmp": "Financial Modeling Prep",
    "cboe": "Cboe Global Markets",
    "fred": "FRED (public-domain series)",
    "fred-third-party": "Copyrighted third-party series on FRED",
    "anthropic": "Claude (AI analyst)",
}
ALWAYS_PUBLIC = {"fred", "anthropic"}


def blocked(sources: set[str] | list[str]) -> list[str]:
    st = get_settings()
    if st.access_mode == "personal":
        return []
    return sorted(p for p in set(sources) if p and p not in ALWAYS_PUBLIC and p not in st.licensed)


def check(sources: set[str] | list[str], what: str = "This data") -> None:
    if b := blocked(sources):
        raise LicenseBlocked(
            f"{what} is not shown on this public site: its source ({', '.join(NAMES.get(x, x) for x in b)}) is not licensed "
            "here for public display. The operator can enable it after obtaining display rights (PUBLIC_DISPLAY_LICENSES)."
        )


def describe() -> dict:
    st = get_settings()
    return {
        "access_mode": st.access_mode,
        "licensed_for_public_display": sorted(st.licensed | ALWAYS_PUBLIC) if st.access_mode == "public" else "all (personal use)",
    }
