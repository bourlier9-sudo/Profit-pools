"""Value capture — the integrated enterprise's consolidated view (Phase 4b). Reads ModelResult.ownership
through views.ownership_view; computes nothing itself."""

from __future__ import annotations

import numpy as np
import streamlit as st

from profit_pools.core.model import ownership as O
from profit_pools.core.model import views as V
from profit_pools.core.model.views import Geo
from profit_pools.core.taxonomy import Metadata
from profit_pools.design import components as ui
from profit_pools.design import format as fmt
from profit_pools.shell import state as S


def _d(cur: float, base: float | None, kind: str = "money") -> str:
    if base is None or not np.isfinite(base) or not np.isfinite(cur):
        return ui.delta_html("", None)
    d = cur - base
    txt = fmt.pp(d) if kind == "pp" else fmt.money(d, signed=True)
    return ui.delta_html(f"{txt} vs baseline", 1 if d > 0 else -1 if d < 0 else 0)


def section_ownership(result, meta: Metadata, f: S.Filters, geo: Geo, cmp=None) -> None:
    org = O.config()["organization"]
    st.markdown(ui.section_header("ownership", "Value capture",
                                  f"{org['label']} · {f.year} · owned care-delivery assets and the consolidated P&L"),
                unsafe_allow_html=True)
    if f.dataset != O.dataset():
        st.markdown(ui.note(f"Value capture describes the enterprise's own assets. Switch Dataset to "
                            f"{meta.datasets[O.dataset()].label} to see it."), unsafe_allow_html=True)
        return
    v = V.ownership_view(result, meta, f.year, geo)
    if v.note:
        st.info(v.note)
        return
    b = V.ownership_view(cmp, meta, f.year, geo) if cmp is not None else None
    t, bt = v.totals, (b.totals if b is not None and not b.note else {})
    owned_margin = t["owned_gain"] / t["owned_revenue"] if t["owned_revenue"] > 0 else np.nan
    items = [
        ("Revenue", fmt.money(t["consolidated_revenue"]), _d(t["consolidated_revenue"], bt.get("consolidated_revenue"))),
        ("Operating gain", fmt.money(t["consolidated_gain"]), _d(t["consolidated_gain"], bt.get("consolidated_gain"))),
        ("Operating margin", fmt.pct(t["consolidated_margin"]), _d(t["consolidated_margin"], bt.get("consolidated_margin"), "pp")),
        ("Owned-asset gain", fmt.money(t["owned_gain"]),
         ui.delta_html(f"margin {fmt.pct(owned_margin)}", None) if cmp is None else _d(t["owned_gain"], bt.get("owned_gain"))),
        ("Routed to owned assets", fmt.pct(t["routed_share"]), ui.delta_html("of member spend", None)),
    ]
    st.markdown(ui.kpi_strip(items), unsafe_allow_html=True)
    st.markdown(ui.note("Consolidated: plans plus owned assets, with claims the plans pay their own assets eliminated."),
                unsafe_allow_html=True)

    left, right = st.columns([5, 7], gap="large")
    with left:
        st.markdown('<div class="pp-h3">Consolidation</div>', unsafe_allow_html=True)
        st.markdown(ui.table(["", "Amount"], [
            ["Plan premium revenue", fmt.money(t["payer_revenue"])],
            ["+ Owned-asset revenue", fmt.money(t["owned_revenue"])],
            ["− Plan claims paid to own assets (eliminated)", fmt.money(-t["elimination"])],
            ["= Consolidated revenue", fmt.money(t["consolidated_revenue"])],
            ["Plan operating gain", fmt.money(t["payer_gain"])],
            ["+ Owned-asset gain", fmt.money(t["owned_gain"])],
            ["= Consolidated gain", fmt.money(t["consolidated_gain"])],
        ]), unsafe_allow_html=True)
        notes = ["Claims a plan pays its own assets are intra-organization: they leave both consolidated revenue "
                 "and expense, so consolidated gain = plan gain + owned-asset gain. Member cost sharing and other "
                 "payers' patients are external revenue."]
        if t["owned_margin_coverage"] < 0.9995:
            notes.append(f"Owned-asset margin is available for {fmt.pct(t['owned_margin_coverage'], 1)} of owned "
                         f"revenue; the rest is shown without gain.")
        st.markdown("".join(ui.note(n) for n in notes), unsafe_allow_html=True)
    with right:
        st.markdown('<div class="pp-h3">Owned assets</div>', unsafe_allow_html=True)
        a = v.assets
        head = ["Asset", "Routed share", "Owned revenue", "Margin (market)", "Owned gain"]
        bg = b.assets.set_index("asset_id")["owned_gain"] if b is not None and not b.note else None
        if bg is not None:
            head.append("Δ gain")
        rows = []
        for r in a.itertuples():
            row = [f"{r.label} · {r.pool}", fmt.pct(r.routing_share), fmt.money(r.owned_revenue),
                   f"{fmt.pct(r.owned_margin)} ({fmt.pct(r.market_margin)})", fmt.money(r.owned_gain)]
            if bg is not None:
                row.append(fmt.money(r.owned_gain - bg.get(r.asset_id, 0.0), signed=True))
            rows.append(row)
        st.markdown(ui.table(head, rows), unsafe_allow_html=True)
        st.markdown(ui.note(
            f"Owned revenue = our members' routed care ({fmt.money(t['routed'])}) + other payers' patients "
            f"({fmt.money(t['owned_third_party'])}). Member spend in these pools still going to external providers: "
            f"{fmt.money(t['external_spend'])}. "
            "Routing changes who keeps the gain, not how much is spent: pool views value enterprise spend at "
            "market margins, this view at the owned asset's margin. Asset figures are fictional estimates "
            "(ownership.yaml); the owned share and margin can be changed in the scenario builder."),
            unsafe_allow_html=True)
