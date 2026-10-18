# Profit Pools Explorer

A healthcare market analytics prototype built with Streamlit and Plotly on **entirely fictional data**.
It shows where revenue and operating gain are generated across sectors, how spending flows between
participants, and how scenarios would change those economics, all from one analytical model.

> Independently developed. It contains no corporate code, data, credentials or proprietary rules.

## Quick start

```bash
pip install -r requirements.txt
python -m pytest                              # unit + reconciliation + UI smoke tests
python -m profit_pools.core.data.synthetic    # fictional dataset summary + contract validation (market + enterprise)
```

Run the app with `streamlit run app.py` (see [BUILD_PLAN.md](BUILD_PLAN.md) for status).

## Documentation

| File | Contents |
|---|---|
| [PRODUCT_SPEC.md](PRODUCT_SPEC.md) | Scope, behaviors, acceptance criteria |
| [ARCHITECTURE.md](ARCHITECTURE.md) | Modules, scenario pipeline, hierarchical resolution |
| [DATA_CONTRACTS.md](DATA_CONTRACTS.md) | Fictional schemas, units, identities, validations |
| [DESIGN_SYSTEM.md](DESIGN_SYSTEM.md) | Tokens, components, chart specifications |
| [BUILD_PLAN.md](BUILD_PLAN.md) | Phases and progress |
| [CLAUDE.md](CLAUDE.md) | Engineering guidance |
