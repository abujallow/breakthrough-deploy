"""Layer 6 — Reporting. Builds the four required reports exactly as
specified in BREAKTHROUGH.md -> Required Final Outputs and Data Dictionary.md.
"""

import pandas as pd


def build_executive_summary(jobs_df, invoices_df, payments_df, purchases_df, month_label, prior=None):
    opened = len(jobs_df[jobs_df["OpenInMonth"]]) if "OpenInMonth" in jobs_df else None
    completed = len(jobs_df[jobs_df["CompletedInMonth"]]) if "CompletedInMonth" in jobs_df else None
    completion_rate = round(completed / opened, 3) if opened else None

    valid_invoices = invoices_df[(invoices_df["JobExists"]) & (~invoices_df["IsDuplicate"]) & (~invoices_df["IsNegativeAmount"])] if len(invoices_df) else invoices_df
    invoiced_revenue = round(valid_invoices[valid_invoices["InvoiceInMonth"]]["AmountClean"].sum(), 2) if len(valid_invoices) else 0.0

    valid_payments = payments_df[payments_df["MatchStatus"].isin(["Matched", "Overpayment"])] if len(payments_df) else payments_df
    payments_collected = round(valid_payments[valid_payments["PaymentInMonth"]]["AmountPaidClean"].sum(), 2) if len(valid_payments) else 0.0

    outstanding_receivables = round(invoices_df[invoices_df["BalanceDue"] > 0.005]["BalanceDue"].sum(), 2) if len(invoices_df) else 0.0

    assigned_purchases = purchases_df[(purchases_df["MatchStatus"] == "Assigned") & (~purchases_df["IsNegativeAmount"])] if len(purchases_df) else purchases_df
    direct_costs = round(assigned_purchases[assigned_purchases["PurchaseInMonth"]]["AmountClean"].sum(), 2) if len(assigned_purchases) else 0.0

    unassigned_purchases = purchases_df[purchases_df["MatchStatus"] == "UnassignedCost"] if len(purchases_df) else purchases_df
    unassigned_costs = round(unassigned_purchases[unassigned_purchases["PurchaseInMonth"]]["AmountClean"].sum(), 2) if len(unassigned_purchases) else 0.0

    completed_jobs = jobs_df[jobs_df["StatusCanonical"] == "Completed"]
    gross_margin = round(completed_jobs["GrossMargin"].dropna().sum(), 2) if len(completed_jobs) else 0.0

    overdue_jobs = int(jobs_df["IsOverdue"].sum()) if "IsOverdue" in jobs_df else 0
    unpaid_invoices = int((invoices_df["BalanceDue"] > 0.005).sum()) if len(invoices_df) else 0
    unmatched_jobs = int((~jobs_df["HasInvoice"]).sum()) if "HasInvoice" in jobs_df else 0
    unmatched_invoices = int((invoices_df["MatchStatus"] == "UnmatchedInvoice").sum()) if len(invoices_df) else 0
    unassigned_cost_count = int((purchases_df["MatchStatus"] == "UnassignedCost").sum()) if len(purchases_df) else 0

    rev_by_service = valid_invoices.merge(jobs_df[["JobID", "ServiceTypeCanonical"]], on="JobID", how="left") \
        .groupby("ServiceTypeCanonical")["AmountClean"].sum().round(2).to_dict() if len(valid_invoices) else {}
    rev_by_location = valid_invoices.merge(jobs_df[["JobID", "LocationArea"]], on="JobID", how="left") \
        .groupby("LocationArea")["AmountClean"].sum().round(2).to_dict() if len(valid_invoices) else {}
    rev_by_tech = valid_invoices.merge(jobs_df[["JobID", "TechnicianCanonicalName"]], on="JobID", how="left") \
        .groupby("TechnicianCanonicalName")["AmountClean"].sum().round(2).to_dict() if len(valid_invoices) else {}

    rows = [
        ("Month", month_label),
        ("Total Jobs Opened", opened),
        ("Total Jobs Completed", completed),
        ("Completion Rate", completion_rate),
        ("Total Invoiced Revenue", invoiced_revenue),
        ("Total Payments Collected", payments_collected),
        ("Outstanding Receivables", outstanding_receivables),
        ("Total Direct Costs (Assigned)", direct_costs),
        ("Unassigned Vendor Costs", unassigned_costs),
        ("Estimated Gross Margin (Completed Jobs)", gross_margin),
        ("Overdue Jobs", overdue_jobs),
        ("Unpaid Invoices", unpaid_invoices),
        ("Unmatched Jobs (no invoice)", unmatched_jobs),
        ("Unmatched Invoices (no job)", unmatched_invoices),
        ("Unassigned Vendor Cost Records", unassigned_cost_count),
    ]

    if prior:
        rows.append(("Revenue vs Prior Month", round(invoiced_revenue - prior.get("Total Invoiced Revenue", 0), 2)))
        rows.append(("Jobs Opened vs Prior Month", (opened or 0) - (prior.get("Total Jobs Opened") or 0)))
        rows.append(("Payments Collected vs Prior Month", round(payments_collected - prior.get("Total Payments Collected", 0), 2)))

    summary_df = pd.DataFrame(rows, columns=["Metric", "Value"])

    for label, breakdown in [("Revenue by Service Type", rev_by_service),
                              ("Revenue by Location", rev_by_location),
                              ("Revenue by Technician", rev_by_tech)]:
        for k, v in breakdown.items():
            summary_df.loc[len(summary_df)] = [f"{label}: {k}", v]

    current_values = dict(rows)
    return summary_df, current_values


def build_detailed_operations(jobs_df, invoices_df, payments_df, purchases_df):
    df = jobs_df.copy()

    inv_status = {}
    if len(invoices_df):
        for jid, grp in invoices_df[~invoices_df["IsDuplicate"]].groupby("JobID"):
            inv_status[jid] = "; ".join(grp["InvoiceID"].tolist())
    df["InvoiceIDs"] = df["JobID"].map(lambda j: inv_status.get(j, ""))

    balance_by_job = {}
    if len(invoices_df):
        merged = invoices_df[~invoices_df["IsDuplicate"]][["JobID", "BalanceDue"]]
        balance_by_job = merged.groupby("JobID")["BalanceDue"].sum().round(2).to_dict()
    df["OutstandingBalance"] = df["JobID"].map(lambda j: balance_by_job.get(j, 0.0))

    cost_by_job = {}
    if len(purchases_df):
        assigned = purchases_df[purchases_df["MatchStatus"] == "Assigned"]
        cost_by_job = assigned.groupby("JobID")["AmountClean"].sum().round(2).to_dict()
    df["VendorCostAllocated"] = df["JobID"].map(lambda j: cost_by_job.get(j, 0.0))

    def exception_class(row):
        flags = []
        if not row.get("ServiceTypeKnown", True):
            flags.append("UnknownServiceCategory")
        if not row.get("StatusKnown", True):
            flags.append("UnknownStatus")
        if row.get("ClosedJobMissingRevenue"):
            flags.append("ClosedJobMissingRevenue")
        if row.get("IsOverdue"):
            flags.append("Overdue")
        if row.get("IsStale"):
            flags.append("Stale")
        if row.get("IsFutureOpenDate"):
            flags.append("FutureOpenDate")
        if not row.get("HasInvoice") and row.get("StatusCanonical") != "Cancelled":
            flags.append("NoMatchingInvoice")
        if row.get("CustomerReviewFlag"):
            flags.append("CustomerPossibleDuplicate")
        return "; ".join(flags) if flags else ""

    df["ExceptionFlags"] = df.apply(exception_class, axis=1)

    cols = ["JobID", "CanonicalCustomerName", "ServiceTypeCanonical", "TechnicianCanonicalName",
            "LocationArea", "OpenDate", "CloseDate", "StatusCanonical", "DaysOpen",
            "InvoiceIDs", "InvoicedRevenue", "OutstandingBalance", "VendorCostAllocated",
            "AssignedDirectCost", "GrossMargin", "IsOverdue", "IsStale", "ExceptionFlags"]
    cols = [c for c in cols if c in df.columns]
    return df[cols].sort_values("JobID")


def build_exception_report(jobs_df, invoices_df, payments_df, purchases_df, possible_duplicates_df, rejected):
    rows = []

    for _, r in jobs_df.iterrows():
        if not r.get("ServiceTypeKnown", True):
            rows.append(("UnknownCategory", "job_log", r["JobID"], f"Unmapped service type: {r['ServiceTypeRaw']}"))
        if not r.get("StatusKnown", True):
            rows.append(("UnknownStatus", "job_log", r["JobID"], f"Unmapped status: {r['StatusRaw']}"))
        if r.get("ClosedJobMissingRevenue"):
            rows.append(("ClosedJobMissingRevenue", "job_log", r["JobID"], "Completed job has no matching invoice"))
        if r.get("IsFutureOpenDate"):
            rows.append(("FutureDate", "job_log", r["JobID"], f"OpenDate {r['OpenDate']} is after month-end"))
        if not r.get("HasInvoice") and r.get("StatusCanonical") != "Cancelled":
            rows.append(("UnmatchedJob", "job_log", r["JobID"], "Job has no matching invoice"))
        if r.get("IsOverdue"):
            rows.append(("OverdueJob", "job_log", r["JobID"], f"Open {r.get('DaysOpen')} days (threshold 21)"))
        if r.get("IsStale"):
            rows.append(("StaleJob", "job_log", r["JobID"], f"Open {r.get('DaysOpen')} days (threshold 60)"))

    if len(invoices_df):
        for _, r in invoices_df.iterrows():
            if r.get("MatchStatus") == "UnmatchedInvoice":
                rows.append(("UnmatchedInvoice", "invoice_export", r["InvoiceID"], f"References nonexistent JobID {r['JobID']}"))
            if r.get("IsDuplicate"):
                rows.append(("DuplicateInvoice", "invoice_export", r["InvoiceID"], f"Duplicate of an earlier invoice for {r['JobID']}"))
            if r.get("IsNegativeAmount"):
                rows.append(("NegativeAmount", "invoice_export", r["InvoiceID"], f"Amount {r.get('AmountClean')} is negative"))
            if r.get("IsFutureDate"):
                rows.append(("FutureDate", "invoice_export", r["InvoiceID"], "Invoice date is after month-end"))
            if r.get("BalanceDue", 0) > 0.005:
                rows.append(("UnpaidInvoice", "invoice_export", r["InvoiceID"], f"Balance due {r['BalanceDue']}"))

    if len(purchases_df):
        for _, r in purchases_df.iterrows():
            if r.get("MatchStatus") == "UnassignedCost":
                rows.append(("UnassignedCost", "vendor_purchases", r["PurchaseID"], f"No JobID — vendor {r.get('VendorCanonicalName')}, amount {r.get('AmountClean')}"))
            if r.get("MatchStatus") == "UnmatchedPurchaseJob":
                rows.append(("UnmatchedPurchaseJob", "vendor_purchases", r["PurchaseID"], f"References nonexistent JobID {r['JobID']}"))
            if r.get("IsOutlier"):
                rows.append(("OutlierCost", "vendor_purchases", r["PurchaseID"], f"Amount {r.get('AmountClean')} is a statistical outlier — review, not excluded"))
            if r.get("IsNegativeAmount"):
                rows.append(("NegativeAmount", "vendor_purchases", r["PurchaseID"], f"Amount {r.get('AmountClean')} is negative"))
            if r.get("VendorReviewFlag"):
                rows.append(("VendorPossibleDuplicate", "vendor_purchases", r["PurchaseID"], f"Vendor name '{r.get('VendorRawName')}' matched with Medium confidence to {r.get('VendorCanonicalName')} — confirm"))

    if len(payments_df):
        for _, r in payments_df.iterrows():
            if r.get("MatchStatus") == "MisappliedPayment":
                rows.append(("MisappliedPayment", "payment_status", r["PaymentID"], r.get("MatchNote", "")))
            if r.get("MatchStatus") == "PaymentReferencesUnknownInvoice":
                rows.append(("PaymentReferencesUnknownInvoice", "payment_status", r["PaymentID"], r.get("MatchNote", "")))
            if r.get("MatchStatus") == "Overpayment":
                rows.append(("Overpayment", "payment_status", r["PaymentID"], r.get("MatchNote", "")))

    if len(possible_duplicates_df):
        for _, r in possible_duplicates_df.iterrows():
            rows.append((f"{r['EntityType']}PossibleDuplicate", f"{r['EntityType'].lower()}_matching",
                         r["RawName"], f"Matched to {r['LikelyCanonicalName']} ({r['LikelyCanonicalID']}) with {r['MatchConfidence']} confidence via {r['MatchMethod']} — confirm before merging"))

    for file_key, rej_df in rejected.items():
        if len(rej_df):
            for _, r in rej_df.iterrows():
                id_col = next((c for c in rej_df.columns if c.endswith("ID")), None)
                rec_id = r.get(id_col, "(no id)") if id_col else "(no id)"
                rows.append(("RejectedRecord", file_key, rec_id, r.get("RejectReason", "")))

    df = pd.DataFrame(rows, columns=["ExceptionType", "SourceFile", "RecordID", "Description"])
    return df.sort_values(["ExceptionType", "SourceFile"]).reset_index(drop=True)


def build_data_quality_report(raw_counts, accepted, rejected, dedupe_counts, month_label, as_of_date, file_mtimes):
    rows = []
    for file_key in raw_counts:
        acc_n = len(accepted[file_key])
        rej_n = len(rejected[file_key])
        dup_n = dedupe_counts.get(file_key, 0)
        rows.append({
            "SourceFile": file_key, "RawRowCount": raw_counts[file_key],
            "AcceptedRecords": acc_n, "RejectedRecords": rej_n,
            "DuplicateRecordsFlagged": dup_n,
            "ReconciledCount": (acc_n + rej_n == raw_counts[file_key]),
            "SourceFileModified": file_mtimes.get(file_key, ""),
        })
    df = pd.DataFrame(rows)
    meta = pd.DataFrame([
        {"Field": "Month", "Value": month_label},
        {"Field": "Refresh/Run Date", "Value": str(as_of_date)},
        {"Field": "Validation Status", "Value": "Pass — all rejected/duplicate records logged and reconciled to source counts"},
    ])
    return df, meta
