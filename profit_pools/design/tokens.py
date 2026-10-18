"""Design tokens — the single source for CSS variables, the Plotly template and components.

See DESIGN_SYSTEM.md for rationale and Figma-equivalent specs.
"""

from __future__ import annotations

FONT_FAMILY = "Inter, system-ui, -apple-system, 'Segoe UI', Roboto, sans-serif"
FONT_URL = "https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap"

COLOR = {
    "bg": "#FAFBFB",
    "surface": "#FFFFFF",
    "ink_1": "#14202B",
    "ink_2": "#4A5866",
    "ink_3": "#7D8A96",
    "line": "#E3E8EC",
    "line_strong": "#C9D1D8",
    "accent": "#0F766E",
    "accent_weak": "#E6F2F1",
    "focus": "#2563EB",
    "good": "#15803D",
    "warn": "#B45309",
    "bad": "#B42318",
    "forecast_band": "#F3F5F7",
    "missing": "#F1F3F5",
    "missing_hatch": "#B9C2CA",
}

# Categorical identity — fixed order, never cycled (validated: worst adjacent CVD ΔE 11.4).
CATEGORICAL = ["#1F6FB2", "#E07A2F", "#159A8C", "#8A63D2", "#C9A227"]
OTHER = "#B8C0C8"
LAYER_COLOR = {
    "PAYER": CATEGORICAL[0],
    "PROVIDER": CATEGORICAL[1],
    "PHARMACY": CATEGORICAL[2],
    "MANUFACTURER": CATEGORICAL[3],
    "HEALTH_SERVICES": CATEGORICAL[4],
}
FUNDING_COLOR = {"FS_UNINSURED": "#8A97A3", "FS_MCR_FFS": "#9FAAB4", "FS_MCD_FFS": "#B4BEC7", "FS_SUPPLEMENTAL": "#C9D1D8"}

# Operating margin — diverging, anchored at 0%, clamped. (stop, hex)
MARGIN_MIN, MARGIN_MAX = -0.10, 0.20
MARGIN_STOPS = [
    (-0.10, "#B42318"),
    (-0.04, "#E8A39C"),
    (0.00, "#ECEFF1"),
    (0.06, "#9CCFC8"),
    (0.12, "#3E9A90"),
    (0.20, "#0F5F58"),
]

TYPE = {  # px size, line-height, weight
    "display": (28, 34, 600),
    "h2": (18, 24, 600),
    "h3": (14, 20, 600),
    "body": (14, 20, 400),
    "small": (12, 16, 400),
    "micro": (11, 14, 500),
    "kpi": (24, 28, 600),
}

SPACE = [0, 2, 4, 8, 12, 16, 24, 32, 40, 56]
RADIUS = {"control": 4, "dialog": 6}
CHART = {"marimekko_height": 520, "trend_height": 300, "composition_height": 260}


def _hex_to_rgb(h: str) -> tuple[int, int, int]:
    h = h.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def _rgb_to_hex(rgb) -> str:
    return "#{:02X}{:02X}{:02X}".format(*(max(0, min(255, round(c))) for c in rgb))


def margin_color(margin: float | None) -> str:
    """Hex for an operating margin on the diverging scale; missing → neutral missing fill."""
    if margin is None or margin != margin:  # NaN
        return COLOR["missing"]
    m = min(max(margin, MARGIN_MIN), MARGIN_MAX)
    for (x0, c0), (x1, c1) in zip(MARGIN_STOPS, MARGIN_STOPS[1:]):
        if m <= x1:
            t = 0 if x1 == x0 else (m - x0) / (x1 - x0)
            a, b = _hex_to_rgb(c0), _hex_to_rgb(c1)
            return _rgb_to_hex([a[i] + (b[i] - a[i]) * t for i in range(3)])
    return MARGIN_STOPS[-1][1]


DELTA_MARGIN_CLAMP = 0.02  # ±2 pp


def diverging(t: float) -> str:
    """Position t ∈ [−1, 1] on the shared red–neutral–teal scale (0 = neutral)."""
    t = max(-1.0, min(1.0, t))
    return margin_color(t * (-MARGIN_MIN if t < 0 else MARGIN_MAX))


def delta_margin_color(d: float | None) -> str:
    """Change in margin (fraction) on the same diverging palette, clamped at ±2 pp."""
    if d is None or d != d:
        return COLOR["missing"]
    t = max(-1.0, min(1.0, d / DELTA_MARGIN_CLAMP))
    return margin_color(t * (-MARGIN_MIN if t < 0 else MARGIN_MAX))


def relative_luminance(hex_color: str) -> float:
    def ch(c):
        c = c / 255
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = (ch(v) for v in _hex_to_rgb(hex_color))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def text_on(fill_hex: str) -> str:
    """Ink or white, whichever reads better on the fill."""
    return "#FFFFFF" if relative_luminance(fill_hex) < 0.36 else COLOR["ink_1"]


def margin_gradient_css() -> str:
    span = MARGIN_MAX - MARGIN_MIN
    stops = ", ".join(f"{c} {100 * (x - MARGIN_MIN) / span:.1f}%" for x, c in MARGIN_STOPS)
    return f"linear-gradient(90deg, {stops})"
