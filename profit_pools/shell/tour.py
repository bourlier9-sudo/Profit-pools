"""Guided tour — a spotlight walkthrough of the workspace.

The spotlight is pure CSS: the target element (found by its Streamlit `st-key-…` class) is lifted above a
dimmed page and outlined in the accent color, and a fixed step card sits in the corner. A tiny script (run in
Streamlit's same-origin HTML frame) scrolls the target into view and remembers in the browser that the tour
was seen. If the script is blocked (e.g. a locked-down warehouse runtime) the tour still works; the reader
just scrolls to the highlighted element.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Callable

import streamlit as st
import streamlit.components.v1 as components

from profit_pools.design.tokens import COLOR
from profit_pools.shell import state as S

K = "pp.tour."
SEEN_KEY = "pp_tour_seen"     # browser localStorage flag


def css_class(key: str) -> str:
    """Streamlit's class for a keyed element: non [A-Za-z0-9_-] characters become '-'."""
    return "st-key-" + "".join(ch if ch.isalnum() or ch in "_-" else "-" for ch in key.strip())


@dataclass(frozen=True)
class Step:
    title: str
    body: str
    selector: str | None = None                       # None = centered welcome / closing card
    when: Callable[[], bool] = lambda: True             # only include the step if its target exists


def _has_scenario() -> bool:
    return S.has_scenario()


def steps() -> list[Step]:
    k = css_class
    return [s for s in [
        Step("Welcome to Profit Pools Explorer",
             "See where revenue and operating gain sit across a fictional healthcare economy, how money flows "
             "between plans, providers and pharmacy, and how scenarios change it. This tour takes about a minute."),
        Step("Pick the dataset",
             "Total market, or the enterprise's own book (it excludes manufacturers, health services and PBM "
             "services). Every chart below follows this choice.", f".{k(S.K_DATASET)}"),
        Step("Year, geography and view",
             "Choose a year (2026 onward is forecast) and a state or county. Once a scenario is applied, View "
             "switches between baseline, scenario, compare and impact only.", f".{k('pp_globalbar')}"),
        Step("The profit-pool map",
             "Column width is each sector's share of revenue; tile height is the sub-pool's share within it; color "
             "is operating margin (red below 0%, teal above). Click a tile to inspect it, then drill in.",
             f"[class*='{k('pp.mekko')}']"),
        Step("Build a scenario",
             "Open the scenario builder to add assumptions step by step: where, what changes, how much and when. "
             "They are worded as trends with a payer, so knock-on effects are clear.", f".{k('pp.chip')}"),
        Step("Compare or isolate the change",
             "Compare shows scenario vs baseline; Impact only greys out everything the scenario does not touch.",
             f".{k(S.K_VIEW)}", _has_scenario),
        Step("Your own pool groupings",
             "Rename pools, move them between groups or create groups. Groupings change roll-ups in every chart, "
             "never the numbers.", f".{k('pp.groupings')}"),
        Step("Trends and why things changed",
             "Revenue, gain and margin over time for the pools in view, with the inspector on the right explaining "
             "the selected pool.", f".{k('pp.trend')}"),
        Step("Follow the money",
             "The Sankey shows who pays whom. Open pools level by level with Expand pools, arrange nodes, and in the "
             "enterprise view follow each premium dollar into enterprise operating gain.",
             f"[class*='{k('pp.sankey')}']"),
        Step("Where to play",
             "Growth vs margin for each pool, bubble size = operating gain. Top-right pools grow faster and earn "
             "more than average.", f".{k('pp.growth.attract')}"),
        Step("What drives operating gain",
             "The bridge splits the change into volume, price, cost and margin drivers. Choose a single pool in "
             "“Bridge for” to see the detail that fits its economics.", f".{k('pp.growth.bridge')}"),
        Step("Compare scenarios",
             "Pick up to three saved scenarios to see them side by side with the baseline.", f".{k(S.K_COMPARE)}"),
        Step("Export",
             "Download the rows behind the view, all results as CSV, a self-contained HTML snapshot or an editable "
             "PowerPoint summary.", f".{k('pp_export')}"),
        Step("Data dictionary",
             "Every definition, pool, lever and data table, plus where data is missing.",
             "[data-testid='stSidebar'] a[href*='data-dictionary']"),
        Step("You're set",
             "Replay this tour any time with “Take the tour” in the sidebar. All data is fictional."),
    ] if s.when()]


def start() -> None:
    st.session_state[K + "step"] = 0


def _go(delta: int) -> None:
    st.session_state[K + "step"] += delta


def _end() -> None:
    st.session_state[K + "step"] = None
    st.session_state[K + "mark_seen"] = True


def sidebar_button() -> None:
    st.button("Take the tour", key=K + "start", icon=":material/tour:", on_click=start, width="stretch",
              help="A one-minute walkthrough of the workspace.")


def render() -> None:
    """Call at the end of the page, after every target has been drawn."""
    ss = st.session_state
    autostart = False
    if not ss.get(K + "offered"):
        ss[K + "offered"] = True
        ss[K + "step"] = 0
        autostart = True
    step = ss.get(K + "step")
    script = None
    if step is None:
        if ss.pop(K + "mark_seen", False):
            script = f"try{{window.parent.localStorage.setItem({json.dumps(SEEN_KEY)},'1')}}catch(e){{}}"
        if script:
            components.html(f"<script>{script}</script>", height=0)
        return
    all_steps = steps()
    step = max(0, min(step, len(all_steps) - 1))
    ss[K + "step"] = step
    s = all_steps[step]
    target = s.selector
    st.markdown(_css(target), unsafe_allow_html=True)
    with st.container(key="pp_tour_card"):
        st.markdown(f"<div class='pp-tour-count'>{step + 1} of {len(all_steps)}</div>"
                    f"<div class='pp-tour-title'>{s.title}</div><div class='pp-tour-body'>{s.body}</div>",
                    unsafe_allow_html=True)
        with st.container(horizontal=True, gap="small", vertical_alignment="center"):
            st.button("Skip tour", key=K + "skip", type="tertiary", on_click=_end)
            st.space("stretch")
            st.button("Back", key=K + "back", disabled=step == 0, on_click=_go, args=(-1,))
            last = step == len(all_steps) - 1
            st.button("Done" if last else "Next", key=K + "next", type="primary",
                      on_click=_end if last else _go, args=() if last else (1,))
    # Scroll the target into view; on an automatic start, close at once if this browser has seen the tour.
    js = []
    if autostart:
        js.append(f"try{{if(window.parent.localStorage.getItem({json.dumps(SEEN_KEY)})){{"
                  f"var b=window.parent.document.querySelector('.{css_class(K + 'skip')} button');if(b)b.click();}}}}"
                  f"catch(e){{}}")
    if target:
        js.append(f"setTimeout(function(){{try{{var el=window.parent.document.querySelector({json.dumps(target)});"
                  f"if(el)el.scrollIntoView({{behavior:'smooth',block:'center'}});}}catch(e){{}}}},250);")
    components.html(f"<script>/* step {step} */{''.join(js)}</script>", height=0)


def _css(target: str | None) -> str:
    dim = "rgba(20, 32, 43, 0.55)"
    card = (f".st-key-pp_tour_card {{ position: fixed; right: 24px; bottom: 24px; width: min(380px, calc(100vw - 32px));"
            f" z-index: 1000002; background: {COLOR['surface']}; border: 1px solid {COLOR['line_strong']};"
            f" border-radius: 8px; padding: 16px 18px 12px; box-shadow: 0 12px 32px rgba(20,32,43,.25); }}"
            f".pp-tour-count {{ font-size: 11px; letter-spacing: .04em; text-transform: uppercase;"
            f" color: {COLOR['ink_3']}; }}"
            f".pp-tour-title {{ font-size: 16px; font-weight: 600; color: {COLOR['ink_1']}; margin: 2px 0 6px; }}"
            f".pp-tour-body {{ font-size: 13px; line-height: 19px; color: {COLOR['ink_2']}; }}")
    # The sticky global bar has its own stacking context, so it would stay bright above the dim: let it
    # scroll with the page during the tour. The sidebar is dimmed separately unless the target is in it.
    flat = ".st-key-pp_globalbar { position: static !important; z-index: auto !important; }"
    in_sidebar = bool(target) and "stSidebar" in target
    side = (f"[data-testid='stMain']::after {{ content: ''; position: fixed; inset: 0; background: {dim};"
            f" z-index: 999990; pointer-events: none; }}" if in_sidebar else
            f"[data-testid='stSidebar']::after {{ content: ''; position: absolute; inset: 0; background: {dim};"
            f" z-index: 1000000; pointer-events: none; }}")
    if not target:
        # Centered card over a dimmed page.
        return (f"<style>{card}{flat}{side} .st-key-pp_tour_card {{ right: 50%; bottom: 50%;"
                f" transform: translate(50%, 50%); box-shadow: 0 0 0 100vmax {dim}; }}</style>")
    return (f"<style>{card}{flat}{side} {target} {{ position: relative; z-index: 1000001 !important;"
            f" box-shadow: 0 0 0 4px {COLOR['accent']}, 0 0 0 100vmax {dim} !important; border-radius: 6px;"
            f" background: {COLOR['surface']}; }}</style>")
