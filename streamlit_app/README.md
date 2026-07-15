# Breakthrough — Ferrous Home Services (Streamlit App)

Interactive demonstration of the Breakthrough back-office reporting pipeline, running live against two months of synthetic data for a fictional home-service contractor.

## Run Locally

```bash
pip install -r requirements.txt
streamlit run app.py
```

## Deploy to Streamlit Community Cloud (free)

1. Push this repository to GitHub (public repo required for the free tier).
2. Go to [share.streamlit.io](https://share.streamlit.io), sign in with GitHub.
3. "New app" → select this repository → set the main file path to `streamlit_app/app.py` (if this folder is part of a larger repo) or `app.py` (if this folder is the repo root).
4. Deploy. The first load may take a minute; the app itself processes both demo months in a few seconds.

## What's Bundled

- `pipeline/` — a copy of the tested reconciliation pipeline (Layers 2–6: intake, standardization, canonicalization, matching, business rules, reporting).
- `data/` — two months of synthetic source files for "Ferrous Home Services" (fictional).

## Notes

- Each visitor's session gets its own isolated, ephemeral registry directory (via `tempfile`) — no state is shared between different visitors.
- The "Add a New Month" tab lets a visitor upload their own 6-file batch to see the refresh mechanism live. A visible warning instructs visitors not to upload real or confidential data — this is a public demo environment.
- All data is synthetic. See the "About & Boundaries" tab in the app itself.
