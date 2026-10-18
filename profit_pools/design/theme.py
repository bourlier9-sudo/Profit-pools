"""CSS and Plotly template generated from tokens."""

from __future__ import annotations

import plotly.graph_objects as go
import plotly.io as pio

from profit_pools.design.tokens import COLOR, FONT_FAMILY, FONT_URL, TYPE, margin_gradient_css

C = COLOR


def _type(name: str) -> str:
    size, lh, weight = TYPE[name]
    return f"font-size:{size}px;line-height:{lh}px;font-weight:{weight};"


def css() -> str:
    return f"""
<style>
@import url('{FONT_URL}');
:root {{
  --bg:{C['bg']}; --surface:{C['surface']}; --ink-1:{C['ink_1']}; --ink-2:{C['ink_2']}; --ink-3:{C['ink_3']};
  --line:{C['line']}; --line-strong:{C['line_strong']}; --accent:{C['accent']}; --accent-weak:{C['accent_weak']};
  --good:{C['good']}; --warn:{C['warn']}; --bad:{C['bad']};
}}
html, body, .stApp, .stMarkdown, button, input, select, textarea, [data-baseweb] {{ font-family:{FONT_FAMILY}; }}
[data-testid="stIconMaterial"], .material-symbols-rounded {{ font-family:"Material Symbols Rounded" !important; }}
[data-testid="stAppViewContainer"] {{ background:var(--bg); }}
[data-testid="stHeader"] {{ background:transparent; height:0; }}
[data-testid="stToolbar"], [data-testid="stDecoration"], [data-testid="stStatusWidget"] {{ display:none; }}
[data-testid="stMainBlockContainer"], .block-container {{
  padding:8px 32px 56px 32px; max-width:1600px;
}}
/* Sidebar: restrained navigation */
[data-testid="stSidebar"] {{ background:var(--surface); border-right:1px solid var(--line); min-width:216px; max-width:216px; }}
[data-testid="stSidebar"] [data-testid="stSidebarContent"] {{ padding-top:8px; }}
[data-testid="stSidebarNav"] {{ display:none; }}
.pp-brand {{ {_type('micro')} text-transform:uppercase; letter-spacing:.06em; color:var(--ink-3); padding:8px 4px 12px; }}
.pp-brand b {{ color:var(--ink-1); letter-spacing:.02em; }}
.pp-nav-group {{ {_type('micro')} text-transform:uppercase; letter-spacing:.06em; color:var(--ink-3); margin:16px 4px 4px; }}
.pp-sidebar-foot {{ {_type('small')} color:var(--ink-3); border-top:1px solid var(--line); padding-top:12px; margin-top:24px; }}
[data-testid="stSidebar"] [data-testid="stPageLink-NavLink"] {{ border-radius:4px; padding:4px 8px; }}
[data-testid="stSidebar"] [data-testid="stPageLink-NavLink"][aria-current="page"],
[data-testid="stSidebar"] a[aria-current="page"] {{ background:var(--accent-weak); }}

/* Typography */
.pp-title {{ {_type('display')} color:var(--ink-1); margin:12px 0 0; padding:0; letter-spacing:-.01em; }}
.pp-subtitle {{ {_type('body')} color:var(--ink-2); margin:2px 0 0; }}
.pp-h2 {{ {_type('h2')} color:var(--ink-1); margin:0; }}
.pp-h3 {{ {_type('h3')} color:var(--ink-1); margin:0 0 4px; }}
.pp-caption {{ {_type('small')} color:var(--ink-2); margin:0; }}
.pp-muted {{ color:var(--ink-3); }}

/* Sticky global bar */
.st-key-pp_globalbar {{
  position:sticky; top:0; z-index:50; background:var(--bg);
  border-bottom:1px solid var(--line); padding:6px 0 8px; margin-bottom:4px;
}}
.st-key-pp_globalbar label p {{ {_type('micro')} text-transform:uppercase; letter-spacing:.06em; color:var(--ink-3); }}
.pp-scenario {{ display:inline-flex; align-items:center; gap:8px; {_type('small')} color:var(--ink-2);
  border:1px solid var(--line-strong); border-radius:4px; padding:6px 10px; background:var(--surface); white-space:nowrap; }}
.pp-dot {{ width:8px; height:8px; border-radius:50%; background:var(--ink-3); display:inline-block; }}
.pp-dot.active {{ background:var(--accent); }}

/* Section header */
.pp-section {{ margin:40px 0 12px; padding-top:16px; border-top:1px solid var(--line); scroll-margin-top:80px; }}
.pp-section:first-of-type {{ border-top:none; }}
.pp-section-nav {{ {_type('small')} color:var(--ink-3); margin-top:10px; }}
.pp-section-nav a {{ color:var(--ink-2); text-decoration:none; margin-right:16px; }}
.pp-section-nav a:hover {{ color:var(--accent); }}

/* KPI strip */
.pp-kpis {{ display:flex; gap:0; border-top:1px solid var(--line); border-bottom:1px solid var(--line); background:var(--surface); margin:12px 0 4px; }}
.pp-kpi {{ flex:1; padding:12px 20px; border-left:1px solid var(--line); min-width:0; }}
.pp-kpi:first-child {{ border-left:none; }}
.pp-kpi-label {{ {_type('micro')} text-transform:uppercase; letter-spacing:.06em; color:var(--ink-3); white-space:nowrap; }}
.pp-kpi-value {{ {_type('kpi')} color:var(--ink-1); margin-top:4px; font-variant-numeric:tabular-nums; }}
.pp-kpi-delta {{ {_type('small')} color:var(--ink-2); margin-top:2px; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }}
.pp-up {{ color:var(--good); }} .pp-down {{ color:var(--bad); }}

/* Badges */
.pp-badge {{ display:inline-block; {_type('micro')} padding:2px 8px; border-radius:10px; margin:0 4px 4px 0;
  background:#EEF1F4; color:var(--ink-2); border:1px solid var(--line); white-space:nowrap; }}
.pp-badge.estimated {{ background:#FFF7ED; color:#9A4A0A; border-color:#F5D9BC; }}
.pp-badge.observed {{ background:#EEF6FF; color:#1E4E8C; border-color:#CFE0F5; }}
.pp-badge.allocated {{ background:#F1F5F9; color:var(--ink-2); }}
.pp-badge.projected {{ background:#F5F3FF; color:#5B3FA8; border-color:#DED5F7; }}
.pp-badge.unavailable {{ background:#F8F9FA; color:var(--ink-3); border-style:dashed; }}

/* Legend for the Marimekko */
.pp-legend {{ display:flex; align-items:center; flex-wrap:wrap; gap:20px; {_type('small')} color:var(--ink-2); margin:4px 0 0; }}
.pp-grad {{ width:180px; height:8px; border-radius:2px; background:{margin_gradient_css()}; }}
.pp-grad-wrap {{ display:flex; flex-direction:column; gap:2px; }}
.pp-grad-ticks {{ display:flex; justify-content:space-between; width:180px; font-size:10px; color:var(--ink-3); font-variant-numeric:tabular-nums; }}
.pp-swatch {{ width:14px; height:10px; display:inline-block; vertical-align:middle; margin-right:6px; border:1px solid var(--line-strong); }}
.pp-hatch {{ background:repeating-linear-gradient(45deg, {C['missing']}, {C['missing']} 3px, {C['missing_hatch']} 3px, {C['missing_hatch']} 4px); }}
.pp-dotted {{ background:#fff; border:1px dashed var(--ink-3); }}

/* Inspector */
.pp-inspector {{ background:var(--surface); border:1px solid var(--line); border-radius:4px; padding:16px 18px; }}
.pp-path {{ {_type('small')} color:var(--ink-3); margin-bottom:2px; }}
.pp-kv {{ display:grid; grid-template-columns:1fr auto; column-gap:12px; row-gap:6px; margin:12px 0 4px; {_type('body')} }}
.pp-kv .k {{ color:var(--ink-2); }}
.pp-kv .v {{ color:var(--ink-1); text-align:right; font-variant-numeric:tabular-nums; }}
.pp-kv .sep {{ grid-column:1 / span 2; border-top:1px solid var(--line); margin:4px 0; }}
.pp-note {{ {_type('small')} color:var(--ink-2); margin:4px 0; }}
.pp-note::before {{ content:"ⓘ "; color:var(--ink-3); }}
.pp-comp {{ width:100%; border-collapse:collapse; {_type('small')} font-variant-numeric:tabular-nums; margin-top:6px; }}
.pp-comp th {{ {_type('micro')} text-transform:uppercase; letter-spacing:.05em; color:var(--ink-3); text-align:right; padding:4px 0; border-bottom:1px solid var(--line); }}
.pp-comp th:first-child, .pp-comp td:first-child {{ text-align:left; }}
.pp-comp td {{ padding:4px 0; text-align:right; color:var(--ink-1); border-bottom:1px solid #F1F3F5; }}

/* Breadcrumb buttons */
.st-key-pp_crumbs button {{ border:none !important; background:transparent !important; padding:0 2px !important;
  color:var(--accent) !important; min-height:0 !important; box-shadow:none !important; }}
.st-key-pp_crumbs button p {{ {_type('body')} font-weight:500; }}
.st-key-pp_crumbs button:disabled p {{ color:var(--ink-1) !important; font-weight:600; }}
.pp-crumb-sep {{ color:var(--ink-3); padding:0 2px; line-height:32px; }}

/* Widgets */
[data-testid="stSegmentedControl"] button p, [data-baseweb="select"] div {{ font-size:13px; }}
div[data-testid="stDataFrame"] {{ border:1px solid var(--line); border-radius:4px; }}
.stButton > button[kind="primary"] {{ background:var(--accent); border-color:var(--accent); }}
</style>
"""


TEMPLATE_NAME = "profit_pools"


def register_plotly_template() -> str:
    t = go.layout.Template()
    t.layout = go.Layout(
        font=dict(family=FONT_FAMILY, size=12, color=C["ink_2"]),
        paper_bgcolor=C["surface"], plot_bgcolor=C["surface"],
        margin=dict(t=8, r=8, b=32, l=48),
        colorway=["#1F6FB2", "#E07A2F", "#159A8C", "#8A63D2", "#C9A227"],
        xaxis=dict(showgrid=False, linecolor=C["line_strong"], ticks="outside", tickcolor=C["line_strong"],
                   tickfont=dict(color=C["ink_3"]), zeroline=False, title=dict(font=dict(size=12))),
        yaxis=dict(showgrid=True, gridcolor=C["line"], gridwidth=1, zeroline=False, linecolor=C["line_strong"],
                   tickfont=dict(color=C["ink_3"]), title=dict(font=dict(size=12))),
        hoverlabel=dict(bgcolor="#FFFFFF", bordercolor=C["line_strong"], font=dict(family=FONT_FAMILY, size=12, color=C["ink_1"]),
                        align="left"),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0, font=dict(size=12, color=C["ink_2"]),
                    bgcolor="rgba(0,0,0,0)"),
        hovermode="closest",
    )
    pio.templates[TEMPLATE_NAME] = t
    return TEMPLATE_NAME


PLOTLY_CONFIG = {"displayModeBar": False, "responsive": True}
