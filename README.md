# Profit Pools Explorer

A healthcare market analytics prototype built with Streamlit and Plotly on **entirely fictional data**.
It shows where revenue and operating gain are generated across sectors, how spending flows between
participants, and how scenarios would change those economics, all from one analytical model.

> Independently developed. It contains no corporate code, data, credentials or proprietary rules.

## Quick start

```bash
pip install -r requirements.txt
python -m pytest                              # 446 unit, reconciliation, scenario, Sankey, growth and UI tests
python -m profit_pools.core.data.synthetic    # fictional dataset summary + contract validation (market + enterprise)
```

Run the app with `streamlit run app.py` (see [BUILD_PLAN.md](BUILD_PLAN.md) for status).

## Viewing it on a phone (or sharing a link)

The app needs a running Python server. The simplest hosted option is **Streamlit Community Cloud**
(free; deploys straight from this GitHub repo):

1. Open **share.streamlit.io** and sign in with GitHub.
2. Choose **Create app → Deploy from GitHub** and set:
   * repository `bourlier9-sudo/profit-pools`
   * branch `claude/profit-pools-explorer-awklkq` (or `main` once merged)
   * main file `app.py`
   * Python 3.12 (under advanced settings)
3. Deploy. It installs `requirements.txt` and gives you a `*.streamlit.app` URL that works in a phone browser.
   App visibility (public, or restricted to invited viewers) is set in the app's sharing settings.

All data is fictional and generated at start-up. No secrets or credentials are needed.

The layout is designed for desktop (1440px). On a phone the controls wrap and the KPIs stack two per row.
Landscape orientation works best for the Marimekko.

## Documentation

| File | Contents |
|---|---|
| [PRODUCT_SPEC.md](PRODUCT_SPEC.md) | Scope, behaviors, acceptance criteria |
| [ARCHITECTURE.md](ARCHITECTURE.md) | Modules, scenario pipeline, hierarchical resolution |
| [DATA_CONTRACTS.md](DATA_CONTRACTS.md) | Fictional schemas, units, identities, validations |
| [DESIGN_SYSTEM.md](DESIGN_SYSTEM.md) | Tokens, components, chart specifications |
| [BUILD_PLAN.md](BUILD_PLAN.md) | Phases and progress |
| [CLAUDE.md](CLAUDE.md) | Engineering guidance |
