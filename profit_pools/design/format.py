"""Number formatting — precision matched to what the data supports."""

from __future__ import annotations

import math

MINUS = "−"
DASH = "—"


def _bad(x) -> bool:
    return x is None or (isinstance(x, float) and math.isnan(x))


def _sign(x: float) -> str:
    return MINUS if x < 0 else ""


def money(x: float | None, signed: bool = False) -> str:
    """$693B, $42.7B, $812.4M, $4,200."""
    if _bad(x):
        return DASH
    s = "+" if signed and x > 0 else _sign(x)
    a = abs(x)
    if a >= 100e9:
        return f"{s}${a / 1e9:,.0f}B"
    if a >= 1e9:
        return f"{s}${a / 1e9:,.1f}B"
    if a >= 1e6:
        return f"{s}${a / 1e6:,.1f}M"
    if a >= 1e3:
        return f"{s}${a / 1e3:,.0f}K"
    return f"{s}${a:,.0f}"


def pmpm(x: float | None) -> str:
    return DASH if _bad(x) else f"{_sign(x)}${abs(x):,.0f}"


def pct(x: float | None, digits: int = 1, signed: bool = False) -> str:
    if _bad(x):
        return DASH
    s = "+" if signed and x > 0 else _sign(x)
    return f"{s}{abs(x) * 100:.{digits}f}%"


def pp(x: float | None, digits: int = 1) -> str:
    """Change in a rate, given as a fraction (0.006 → +0.6 pp)."""
    if _bad(x):
        return DASH
    s = "+" if x > 0 else _sign(x)
    return f"{s}{abs(x) * 100:.{digits}f} pp"


def count(x: float | None, unit: str = "", signed: bool = False) -> str:
    if _bad(x):
        return DASH
    a = abs(x)
    body = (f"{a / 1e6:,.1f}M" if a >= 1e6 else f"{a / 1e3:,.0f}K" if a >= 1e4 else f"{a:,.0f}")
    sign = "+" if signed and x > 0 else _sign(x)
    return f"{sign}{body}{(' ' + unit) if unit else ''}"


def members(x: float | None, signed: bool = False) -> str:
    return count(x, signed=signed)


def cagr(x: float | None) -> str:
    return "n/m" if _bad(x) else pct(x, 1)
