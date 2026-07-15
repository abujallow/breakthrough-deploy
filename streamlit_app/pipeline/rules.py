"""Layer 5 — Business Rules.

Revenue, cost, outstanding balance, job status, days overdue, gross margin,
exceptions, missing-information flags, data-quality flags. Thresholds
(overdue/stale days, outlier z-score) are documented working assumptions —
see Data Dictionary.md -> Known Limitations and Assumptions and Unknowns.md.
"""

import statistics as stats

OVERDUE_DAYS = 21
STALE_DAYS = 60
OUTLIER_STD_MULTIPLIER = 3


def compute_job_flags(jobs_df, as_of_date):
    jobs_df = jobs_df.copy()
    days_open, is_overdue, is_stale = [], [], []
    for _, row in jobs_df.iterrows():
        open_dt = row.get("OpenDateParsed")
        status = row.get("StatusCanonical")
        if open_dt is None:
            days_open.append(None); is_overdue.append(False); is_stale.append(False)
            continue
        d = (as_of_date - open_dt).days
        days_open.append(d)
        is_open = (status == "Open")
        is_overdue.append(bool(is_open and d > OVERDUE_DAYS))
        is_stale.append(bool(is_open and d > STALE_DAYS))
    jobs_df["DaysOpen"] = days_open
    jobs_df["IsOverdue"] = is_overdue
    jobs_df["IsStale"] = is_stale
    return jobs_df


def compute_invoice_balances(invoices_df, payments_df):
    """BalanceDue per valid, non-duplicate invoice using AppliedToInvoiceID
    from match.reconcile_payments (so misapplied payments correctly relieve
    the invoice they actually paid, and the invoice they were WRONGLY
    referenced against stays outstanding — the whole point of catching it)."""
    invoices_df = invoices_df.copy()
    paid_map = {}
    if len(payments_df):
        valid_payments = payments_df[payments_df["MatchStatus"].isin(["Matched", "MisappliedPayment", "Overpayment"])]
        for _, r in valid_payments.iterrows():
            iid = r.get("AppliedToInvoiceID")
            if iid:
                paid_map[iid] = paid_map.get(iid, 0.0) + r["AmountPaidClean"]

    balances, paid_amounts = [], []
    for _, row in invoices_df.iterrows():
        iid = row["InvoiceID"]
        amt = row.get("AmountClean") or 0.0
        paid = paid_map.get(iid, 0.0)
        paid_amounts.append(paid)
        balances.append(round(amt - paid, 2))
    invoices_df["AmountPaidTotal"] = paid_amounts
    invoices_df["BalanceDue"] = balances
    return invoices_df


def flag_outlier_purchases(purchases_df):
    if len(purchases_df) < 3:
        purchases_df = purchases_df.copy()
        purchases_df["IsOutlier"] = False
        return purchases_df
    amounts = purchases_df["AmountClean"].dropna().tolist()
    mean = stats.mean(amounts)
    stdev = stats.pstdev(amounts) if len(amounts) > 1 else 0
    threshold = mean + OUTLIER_STD_MULTIPLIER * stdev
    purchases_df = purchases_df.copy()
    purchases_df["IsOutlier"] = purchases_df["AmountClean"].apply(
        lambda a: bool(a is not None and a > threshold)
    )
    return purchases_df


def flag_closed_job_missing_revenue(jobs_df):
    jobs_df = jobs_df.copy()
    jobs_df["ClosedJobMissingRevenue"] = (
        (jobs_df["StatusCanonical"] == "Completed") & (~jobs_df["HasInvoice"])
    )
    return jobs_df


def compute_job_financials(jobs_df, invoices_df, purchases_df):
    """Per-job invoiced revenue (valid, non-duplicate invoices), assigned
    direct cost (valid purchases with a matching JobID), and gross margin
    for Completed jobs. Open jobs show margin as None (Pending)."""
    valid_invoices = invoices_df[(invoices_df["JobExists"]) & (~invoices_df["IsDuplicate"])] if len(invoices_df) else invoices_df
    revenue_by_job = valid_invoices.groupby("JobID")["AmountClean"].sum().to_dict() if len(valid_invoices) else {}

    valid_purchases = purchases_df[purchases_df["MatchStatus"] == "Assigned"] if len(purchases_df) else purchases_df
    cost_by_job = valid_purchases.groupby("JobID")["AmountClean"].sum().to_dict() if len(valid_purchases) else {}

    jobs_df = jobs_df.copy()
    jobs_df["InvoicedRevenue"] = jobs_df["JobID"].map(lambda j: round(revenue_by_job.get(j, 0.0), 2))
    jobs_df["AssignedDirectCost"] = jobs_df["JobID"].map(lambda j: round(cost_by_job.get(j, 0.0), 2))
    jobs_df["GrossMargin"] = jobs_df.apply(
        lambda r: round(r["InvoicedRevenue"] - r["AssignedDirectCost"], 2) if r["StatusCanonical"] == "Completed" else None,
        axis=1
    )
    return jobs_df
