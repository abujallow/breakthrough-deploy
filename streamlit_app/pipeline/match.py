"""Layer 4 - Matching and Reconciliation.

Connects Jobs <-> Invoices <-> Payments and Jobs <-> Vendor Purchases.
Every record that cannot be confidently matched is flagged, not dropped and
not guessed. See Validation Rules.md -> Cross-File Rules.
"""

import pandas as pd

TOLERANCE = 0.01


def dedupe_invoices(invoices_df):
    """Flag exact duplicates (same JobID + AmountClean + InvoiceDateParsed).
    First occurrence kept as valid; subsequent occurrences flagged and
    excluded from revenue totals, but never deleted from the dataset."""
    if len(invoices_df) == 0:
        invoices_df["IsDuplicate"] = pd.Series(dtype=bool)
        return invoices_df
    df = invoices_df.copy()
    key_cols = ["JobID", "AmountClean", "InvoiceDateParsed"]
    df["_dup_rank"] = df.groupby(key_cols).cumcount()
    df["IsDuplicate"] = df["_dup_rank"] > 0
    df = df.drop(columns=["_dup_rank"])
    return df


def reconcile_jobs_invoices(jobs_df, invoices_df):
    job_ids = set(jobs_df["JobID"])
    invoices_df = invoices_df.copy()
    invoices_df["JobExists"] = invoices_df["JobID"].isin(job_ids)
    invoices_df["MatchStatus"] = invoices_df.apply(
        lambda r: "UnmatchedInvoice" if not r["JobExists"] else (
            "Duplicate" if r.get("IsDuplicate", False) else "Matched"
        ), axis=1
    )

    valid_invoice_job_ids = set(
        invoices_df.loc[(invoices_df["JobExists"]) & (~invoices_df.get("IsDuplicate", False)), "JobID"]
    )
    jobs_df = jobs_df.copy()
    jobs_df["HasInvoice"] = jobs_df["JobID"].isin(valid_invoice_job_ids)
    return jobs_df, invoices_df


def reconcile_purchases(jobs_df, purchases_df):
    if len(purchases_df) == 0:
        return purchases_df
    job_ids = set(jobs_df["JobID"])
    df = purchases_df.copy()

    def status(row):
        jid = str(row.get("JobID", "")).strip()
        if not jid:
            return "UnassignedCost"
        if jid not in job_ids:
            return "UnmatchedPurchaseJob"
        return "Assigned"

    df["MatchStatus"] = df.apply(status, axis=1)
    return df


def reconcile_payments(invoices_df, payments_df):
    """For each payment, first check whether its amount exactly matches a
    DIFFERENT invoice's outstanding balance than the one it references. If
    so, it is treated as MisappliedPayment regardless of whether the
    referenced InvoiceID exists (a misapplied payment can reference either a
    real-but-wrong invoice or a nonexistent one) - both are reconciliation
    problems and both must be caught. Only when no better-matching invoice
    exists does the payment apply against its own referenced invoice
    (Matched, or Overpayment if it exceeds that invoice's remaining
    balance), or get flagged PaymentReferencesUnknownInvoice if the
    reference doesn't exist and no misapplied candidate was found either."""
    if len(payments_df) == 0:
        return payments_df

    valid_invoices = invoices_df[(invoices_df.get("JobExists", True)) & (~invoices_df.get("IsDuplicate", False))]
    invoice_amounts = dict(zip(valid_invoices["InvoiceID"], valid_invoices["AmountClean"]))
    valid_invoice_ids = set(invoice_amounts.keys())

    df = payments_df.copy()
    applied = {iid: 0.0 for iid in valid_invoice_ids}
    statuses, notes, applied_to = [], [], []

    for _, row in df.iterrows():
        iid = str(row["InvoiceID"]).strip()
        paid = row.get("AmountPaidClean")
        if paid is None:
            statuses.append("InvalidAmount"); notes.append(""); applied_to.append(None); continue

        # Does the amount exactly match a DIFFERENT invoice's outstanding balance?
        candidate = None
        for other_iid, amt in invoice_amounts.items():
            if other_iid == iid:
                continue
            remaining_other = amt - applied.get(other_iid, 0.0)
            if abs(remaining_other - paid) <= TOLERANCE:
                candidate = other_iid
                break

        referenced_exists = iid in valid_invoice_ids
        referenced_remaining = (invoice_amounts[iid] - applied.get(iid, 0.0)) if referenced_exists else None
        fits_referenced = referenced_exists and (paid - referenced_remaining) <= TOLERANCE

        if not fits_referenced and candidate:
            statuses.append("MisappliedPayment")
            notes.append(f"Amount matches outstanding balance of {candidate}, not referenced InvoiceID {iid}")
            applied[candidate] = applied.get(candidate, 0.0) + paid
            applied_to.append(candidate)
        elif not referenced_exists:
            statuses.append("PaymentReferencesUnknownInvoice")
            notes.append(f"InvoiceID {iid} not found among valid invoices")
            applied_to.append(None)
        elif not fits_referenced:
            statuses.append("Overpayment")
            notes.append(f"Paid {paid:.2f} exceeds remaining balance {referenced_remaining:.2f} on {iid}")
            applied[iid] = applied.get(iid, 0.0) + paid
            applied_to.append(iid)
        else:
            statuses.append("Matched")
            notes.append("")
            applied[iid] = applied.get(iid, 0.0) + paid
            applied_to.append(iid)

    df["MatchStatus"] = statuses
    df["MatchNote"] = notes
    df["AppliedToInvoiceID"] = applied_to
    return df
