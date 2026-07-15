"""
Breakthrough — Ferrous Home Services live demonstration.

A Streamlit app that runs the actual Breakthrough reconciliation pipeline
against synthetic data for a fictional home-service contractor, live in the
browser. Shows the four required reports (Executive Summary, Detailed
Operations, Exception Report, Data Quality Report) and lets a visitor add a
new month's files to see the refresh behavior for themselves.

ALL DATA IN THIS APP IS SYNTHETIC AND FICTIONAL. No real business,
customer, vendor, or employee data is used anywhere in this demonstration.
"""

import os
import sys
import tempfile
import shutil
from datetime import date, datetime

import pandas as pd
import streamlit as st

# --- make the bundled pipeline importable -----------------------------
APP_DIR = os.path.dirname(os.path.abspath(__file__))
PIPELINE_DIR = os.path.join(APP_DIR, "pipeline")
DATA_DIR = os.path.join(APP_DIR, "data")
sys.path.insert(0, PIPELINE_DIR)

import io_intake      # noqa: E402
import standardize     # noqa: E402
import match           # noqa: E402
import rules           # noqa: E402
import reports         # noqa: E402

st.set_page_config(
    page_title="Breakthrough — Ferrous Home Services Demo",
    page_icon="🧾",
    layout="wide",
)

BUNDLED_MONTHS = [
    {"label": "Month 1 — May 2026", "key": "Month_01_2026-05", "dir": os.path.join(DATA_DIR, "Month_01_2026-05"),
     "start": date(2026, 5, 1), "end": date(2026, 5, 31), "as_of": date(2026, 5, 31)},
    {"label": "Month 2 — June 2026 (refresh)", "key": "Month_02_2026-06", "dir": os.path.join(DATA_DIR, "Month_02_2026-06"),
     "start": date(2026, 6, 1), "end": date(2026, 6, 30), "as_of": date(2026, 6, 30)},
]

REQUIRED_FILES = ["job_log.csv", "invoice_export.csv", "vendor_purchases.csv",
                   "technician_time.csv", "customer_list.csv", "payment_status.csv"]


def in_range(d, start, end):
    return bool(d is not None and start <= d <= end)


def process_batch(source_dir, month_start, month_end, as_of_date, label, prior_values=None):
    """Runs Layers 2-6 of the pipeline against one month's six files.
    Mirrors Working Files/pipeline/run_pipeline.py, adapted for in-session
    (no-disk-persistence-across-runs) execution inside Streamlit."""
    accepted, rejected, raw_counts = io_intake.run_intake(source_dir, as_of_date)

    jobs_df = standardize.standardize_job_log(accepted["job_log"])
    jobs_df, purchases_df, time_df, registries, possible_dupes = standardize.canonicalize_entities(
        jobs_df, accepted["vendor_purchases"], accepted["technician_time"], accepted["customer_list"]
    )

    invoices_df = match.dedupe_invoices(accepted["invoice_export"])
    jobs_df, invoices_df = match.reconcile_jobs_invoices(jobs_df, invoices_df)
    purchases_df = match.reconcile_purchases(jobs_df, purchases_df)
    purchases_df = rules.flag_outlier_purchases(purchases_df)
    payments_df = match.reconcile_payments(invoices_df, accepted["payment_status"])

    invoices_df = rules.compute_invoice_balances(invoices_df, payments_df)
    jobs_df = rules.compute_job_flags(jobs_df, as_of_date)
    jobs_df = rules.flag_closed_job_missing_revenue(jobs_df)
    jobs_df = rules.compute_job_financials(jobs_df, invoices_df, purchases_df)

    jobs_df["OpenInMonth"] = jobs_df["OpenDateParsed"].apply(lambda d: in_range(d, month_start, month_end))
    jobs_df["CompletedInMonth"] = jobs_df.apply(
        lambda r: in_range(r["CloseDateParsed"], month_start, month_end) and r["StatusCanonical"] == "Completed", axis=1
    )
    if len(invoices_df):
        invoices_df["InvoiceInMonth"] = invoices_df["InvoiceDateParsed"].apply(lambda d: in_range(d, month_start, month_end))
    if len(purchases_df):
        purchases_df["PurchaseInMonth"] = purchases_df["PurchaseDateParsed"].apply(lambda d: in_range(d, month_start, month_end))
    if len(payments_df):
        payments_df["PaymentInMonth"] = payments_df["PaymentDateParsed"].apply(lambda d: in_range(d, month_start, month_end))

    standardize.persist_registries(registries)

    summary_df, current_values = reports.build_executive_summary(
        jobs_df, invoices_df, payments_df, purchases_df, label, prior=prior_values
    )
    detailed_df = reports.build_detailed_operations(jobs_df, invoices_df, payments_df, purchases_df)
    exception_df = reports.build_exception_report(jobs_df, invoices_df, payments_df, purchases_df, possible_dupes, rejected)
    dedupe_counts = {"invoice_export": int(invoices_df["IsDuplicate"].sum()) if len(invoices_df) else 0}
    file_mtimes = {k: "" for k in raw_counts}
    dq_df, dq_meta = reports.build_data_quality_report(raw_counts, accepted, rejected, dedupe_counts, label, as_of_date, file_mtimes)

    all_rejected = pd.concat(
        [df.assign(SourceFile=k) for k, df in rejected.items() if len(df)], ignore_index=True
    ) if any(len(df) for df in rejected.values()) else pd.DataFrame()

    return {
        "summary_df": summary_df, "current_values": current_values,
        "detailed_df": detailed_df, "exception_df": exception_df,
        "dq_df": dq_df, "dq_meta": dq_meta, "rejected_df": all_rejected,
        "registries": {k: len(v) for k, v in registries.items()},
        "raw_counts": raw_counts,
    }


def run_full_demo():
    """Fresh registry dir, process both bundled months in sequence."""
    registry_dir = tempfile.mkdtemp(prefix="breakthrough_registry_")
    os.environ["BREAKTHROUGH_REGISTRY_DIR"] = registry_dir
    results = {}
    prior_values = None
    for m in BUNDLED_MONTHS:
        res = process_batch(m["dir"], m["start"], m["end"], m["as_of"], m["key"], prior_values=prior_values)
        results[m["key"]] = res
        prior_values = res["current_values"]
    return results, registry_dir


if "demo_results" not in st.session_state:
    with st.spinner("Running the reconciliation pipeline on both months..."):
        st.session_state.demo_results, st.session_state.registry_dir = run_full_demo()

results = st.session_state.demo_results

# --- header ---------------------------------------------------------------
st.title("🧾 Breakthrough — Ferrous Home Services")
st.caption(
    "A live demonstration of a recurring small-business back-office reporting system. "
    "**All data on this page is synthetic and fictional** — no real business, customer, "
    "vendor, or employee information is used anywhere in this demo."
)

tabs = st.tabs([
    "📋 Walkthrough", "📊 Executive Summary", "🧰 Detailed Operations",
    "⚠️ Exception Report", "✅ Data Quality", "🔄 Add a New Month", "ℹ️ About & Boundaries",
])

# --- Walkthrough tab --------------------------------------------------------
with tabs[0]:
    st.header("The Problem")
    st.markdown(
        "Ferrous Home Services is a fictional single-location residential plumbing & HVAC "
        "contractor with 4–6 technicians. Every month, someone on staff has to manually "
        "open six separate files — a job log, an invoice export, a vendor purchase export, "
        "a technician time log, a customer list, and a payment status file — and hand-build "
        "one accurate report for the owner. This is slow, error-prone, and gets worse as the "
        "business grows."
    )
    st.header("The Six Messy Source Files")
    cols = st.columns(3)
    file_descriptions = [
        ("Job Log", "Every job: customer, service type, technician, dates, status."),
        ("Invoice Export", "Every invoice issued — sometimes missing or mismatched job references."),
        ("Vendor Purchase Export", "Parts/materials costs — often not tied back to a job."),
        ("Technician Time Log", "Hours logged per technician per job per day."),
        ("Customer List", "Customer names and contact info — with real-world near-duplicates."),
        ("Payment Status File", "Payments received — occasionally applied to the wrong invoice."),
    ]
    for i, (name, desc) in enumerate(file_descriptions):
        with cols[i % 3]:
            st.markdown(f"**{name}**  \n{desc}")

    st.header("The Realistic Problems Inside These Files")
    st.markdown(
        "- Vendor and customer names entered inconsistently (\"Ferguson\" vs \"FERGUSON PLBG SUPPLY\")\n"
        "- Dates stored in three different formats\n"
        "- Amounts stored as text, with `$` and commas\n"
        "- Duplicate invoices, missing job IDs, jobs with no invoice, invoices with no job\n"
        "- A payment recorded against the wrong invoice\n"
        "- Closed jobs with no revenue recorded, open jobs that are overdue\n"
        "- Vendor costs that can't be tied to any job\n"
        "- An unusually large cost, a negative amount, a future-dated record, a stale open job\n"
    )

    st.header("The Repeatable Workflow")
    st.markdown(
        "1. **Intake & validation** — every file is checked for required columns, "
        "valid dates, and valid amounts. Bad rows are rejected with a reason, never silently dropped.\n"
        "2. **Standardization** — service categories and job statuses are mapped to a fixed list; "
        "anything that doesn't map is flagged, never guessed.\n"
        "3. **Canonicalization** — customer, vendor, and technician name variants are matched to a single "
        "canonical identity, conservatively — ambiguous matches are flagged for human review, never silently merged.\n"
        "4. **Reconciliation** — jobs are matched to invoices, invoices to payments, purchases to jobs.\n"
        "5. **Business rules** — revenue, cost, margin, overdue status, and data-quality flags are calculated.\n"
        "6. **Reporting** — four reports come out the other end, automatically.\n"
    )

    st.header("What Comes Out")
    st.markdown(
        "- **Executive Monthly Summary** — the owner-facing numbers, one page.\n"
        "- **Detailed Operations Report** — every job, with its financials and any exception flags.\n"
        "- **Exception Report** — a focused review queue of everything that needs a human's attention.\n"
        "- **Data Quality Report** — proof that every source record was accounted for.\n"
    )
    st.info("Use the tabs above to explore the actual reports this system just generated from the raw files, live.")

# --- Executive Summary tab -------------------------------------------------
with tabs[1]:
    st.header("Executive Monthly Summary")
    month_choice = st.selectbox("Select month", [m["label"] for m in BUNDLED_MONTHS], key="exec_month")
    month_key = next(m["key"] for m in BUNDLED_MONTHS if m["label"] == month_choice)
    res = results[month_key]

    df = res["summary_df"]
    headline = df[~df["Metric"].str.contains(":", regex=False)]
    breakdowns = df[df["Metric"].str.contains(":", regex=False)]

    c1, c2, c3, c4 = st.columns(4)
    def metric_val(name):
        row = headline[headline["Metric"] == name]
        return row["Value"].iloc[0] if len(row) else "—"
    c1.metric("Total Invoiced Revenue", f"${metric_val('Total Invoiced Revenue'):,.2f}")
    c2.metric("Payments Collected", f"${metric_val('Total Payments Collected'):,.2f}")
    c3.metric("Outstanding Receivables", f"${metric_val('Outstanding Receivables'):,.2f}")
    c4.metric("Est. Gross Margin", f"${metric_val('Estimated Gross Margin (Completed Jobs)'):,.2f}")

    c5, c6, c7, c8 = st.columns(4)
    c5.metric("Jobs Opened", metric_val("Total Jobs Opened"))
    c6.metric("Jobs Completed", metric_val("Total Jobs Completed"))
    c7.metric("Overdue Jobs", metric_val("Overdue Jobs"))
    c8.metric("Unpaid Invoices", metric_val("Unpaid Invoices"))

    display_headline = headline.copy()
    display_headline["Value"] = display_headline["Value"].astype(str)
    st.dataframe(display_headline, use_container_width=True, hide_index=True)

    if len(breakdowns):
        st.subheader("Revenue Breakdowns")
        for label in ["Revenue by Service Type", "Revenue by Location", "Revenue by Technician"]:
            sub = breakdowns[breakdowns["Metric"].str.startswith(label)].copy()
            if len(sub):
                sub["Metric"] = sub["Metric"].str.replace(f"{label}: ", "", regex=False)
                st.caption(label)
                st.bar_chart(sub.set_index("Metric")["Value"])

    st.download_button("Download Executive Summary (CSV)", df.to_csv(index=False), f"executive_summary_{month_key}.csv")

# --- Detailed Operations tab ------------------------------------------------
with tabs[2]:
    st.header("Detailed Operations Report")
    month_choice2 = st.selectbox("Select month", [m["label"] for m in BUNDLED_MONTHS], key="detail_month")
    month_key2 = next(m["key"] for m in BUNDLED_MONTHS if m["label"] == month_choice2)
    detail_df = results[month_key2]["detailed_df"]
    st.caption(f"{len(detail_df)} job-level records — every job, its financials, and any exception flags.")
    st.dataframe(detail_df, use_container_width=True, hide_index=True)
    st.download_button("Download Detailed Operations (CSV)", detail_df.to_csv(index=False), f"detailed_operations_{month_key2}.csv")

# --- Exception Report tab ---------------------------------------------------
with tabs[3]:
    st.header("Exception Report")
    month_choice3 = st.selectbox("Select month", [m["label"] for m in BUNDLED_MONTHS], key="exc_month")
    month_key3 = next(m["key"] for m in BUNDLED_MONTHS if m["label"] == month_choice3)
    exc_df = results[month_key3]["exception_df"]
    st.caption(f"{len(exc_df)} items flagged for review — nothing here was silently dropped or guessed.")
    type_filter = st.multiselect("Filter by exception type", sorted(exc_df["ExceptionType"].unique()))
    show_df = exc_df[exc_df["ExceptionType"].isin(type_filter)] if type_filter else exc_df
    st.dataframe(show_df, use_container_width=True, hide_index=True)
    st.download_button("Download Exception Report (CSV)", exc_df.to_csv(index=False), f"exception_report_{month_key3}.csv")

# --- Data Quality tab --------------------------------------------------------
with tabs[4]:
    st.header("Data Quality Report")
    month_choice4 = st.selectbox("Select month", [m["label"] for m in BUNDLED_MONTHS], key="dq_month")
    month_key4 = next(m["key"] for m in BUNDLED_MONTHS if m["label"] == month_choice4)
    res4 = results[month_key4]
    st.caption("Every source file, reconciled: raw rows in = accepted + rejected out. Nothing disappears unexplained.")
    st.dataframe(res4["dq_df"], use_container_width=True, hide_index=True)
    all_reconciled = bool(res4["dq_df"]["ReconciledCount"].all())
    if all_reconciled:
        st.success("All source files fully reconciled — every raw record is accounted for.")
    else:
        st.warning("At least one source file did not fully reconcile — see the table above.")
    if len(res4["rejected_df"]):
        st.subheader("Rejected Records (never silently dropped)")
        st.dataframe(res4["rejected_df"], use_container_width=True, hide_index=True)

# --- Add a New Month tab ------------------------------------------------------
with tabs[5]:
    st.header("Add a New Month — See the Refresh Work")
    st.markdown(
        "Upload a new month's six source files (same structure as the demo files: `job_log.csv`, "
        "`invoice_export.csv`, `vendor_purchases.csv`, `technician_time.csv`, `customer_list.csv`, "
        "`payment_status.csv`) to see the pipeline process a fresh refresh, live, using the same "
        "canonical customer/vendor/technician matching built up from Months 1 and 2."
    )
    st.warning(
        "⚠️ **Do not upload real business, customer, or confidential data.** This is a public "
        "demonstration environment. Use only synthetic or clearly non-sensitive test files. "
        "You can re-download and re-upload the bundled demo files from the tabs above to see the "
        "refresh mechanism without needing your own data."
    )

    uploaded = {}
    ok = True
    for fname in REQUIRED_FILES:
        f = st.file_uploader(fname, type="csv", key=f"upload_{fname}")
        uploaded[fname] = f
        if f is None:
            ok = False

    as_of = st.date_input("Report as-of date", value=date(2026, 7, 31))

    if st.button("Run Refresh", disabled=not ok):
        with st.spinner("Processing the new month..."):
            tmp_dir = tempfile.mkdtemp(prefix="breakthrough_upload_")
            for fname, f in uploaded.items():
                with open(os.path.join(tmp_dir, fname), "wb") as out:
                    out.write(f.getvalue())
            try:
                os.environ["BREAKTHROUGH_REGISTRY_DIR"] = st.session_state.registry_dir
                prior_vals = results[BUNDLED_MONTHS[-1]["key"]]["current_values"]
                new_res = process_batch(
                    tmp_dir, date(as_of.year, as_of.month, 1), as_of, as_of,
                    f"Uploaded_{as_of.isoformat()}", prior_values=prior_vals
                )
                st.success(
                    f"Refresh complete. Registries after this run — "
                    f"Customers: {new_res['registries'].get('Customer')}, "
                    f"Vendors: {new_res['registries'].get('Vendor')}, "
                    f"Technicians: {new_res['registries'].get('Technician')} "
                    "(carried forward from Months 1–2, extended with anything new in your file, "
                    "with no code changes required)."
                )
                st.subheader("Executive Summary — Your Uploaded Month")
                st.dataframe(new_res["summary_df"], use_container_width=True, hide_index=True)
                st.subheader("Exception Report — Your Uploaded Month")
                st.dataframe(new_res["exception_df"], use_container_width=True, hide_index=True)
            except io_intake.IntakeError as e:
                st.error(f"Intake validation failed: {e}")
            finally:
                shutil.rmtree(tmp_dir, ignore_errors=True)

# --- About & Boundaries tab ---------------------------------------------------
with tabs[6]:
    st.header("About This Demonstration")
    st.markdown(
        "This is a working proof of concept for **Breakthrough**, a proposed recurring back-office "
        "reporting service for small service businesses. It is built and maintained by an individual "
        "founder with experience building operational reporting automation, data standardization "
        "logic, and recurring reporting systems.\n\n"
        "**What this system does:** ingests messy operational spreadsheets, standardizes and "
        "reconciles them, and produces a clean, accurate, auditable monthly report — automatically, "
        "every month, once set up.\n\n"
        "**What this system does not claim to do:**\n"
        "- It does not guarantee legal, tax, or financial-statement compliance.\n"
        "- It does not replace a CPA, bookkeeper, or attorney.\n"
        "- It does not eliminate all errors — it flags what it cannot confidently resolve for a human to review.\n"
        "- It has not been validated against a real business's actual data or workflow.\n\n"
        "**All data used in this demonstration is synthetic and fictional**, generated with a seeded "
        "random process. No real business, customer, vendor, or employee data appears anywhere on "
        "this page.\n\n"
        "**Boundaries of this demo:** this deployment is for demonstration purposes only. It is not "
        "connected to any real business system and should not be used with real, confidential, or "
        "sensitive data of any kind."
    )
    st.caption(f"Registry state for this session is stored in an ephemeral, session-scoped temp directory and is not shared between visitors. Session started {datetime.now().isoformat(timespec='seconds')}.")
