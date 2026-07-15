"""Layer 2 — Intake and Validation.

Loads the six raw source files for a month, applies Validation Rules.md,
and returns (accepted_df, rejected_df, raw_row_count) per file. Rejected
rows are never dropped silently — they carry a RejectReason and are written
to the run's output for audit. See Validation Rules.md for the exact rules
implemented here.
"""

import os
import pandas as pd

from common import parse_date_safe, clean_amount

REQUIRED_COLUMNS = {
    "job_log": ["JobID", "RawCustomerName", "ServiceTypeRaw", "TechnicianRawName",
                "LocationArea", "OpenDate", "CloseDate", "StatusRaw"],
    "invoice_export": ["InvoiceID", "JobID", "AmountRaw", "InvoiceDateRaw"],
    "vendor_purchases": ["PurchaseID", "VendorRawName", "AmountRaw", "PurchaseDateRaw", "JobID"],
    "technician_time": ["TechRawName", "JobID", "DateRaw", "HoursRaw"],
    "customer_list": ["RawName", "Phone", "ServiceArea"],
    "payment_status": ["PaymentID", "InvoiceID", "AmountPaidRaw", "PaymentDateRaw", "StatusRaw"],
}

FILE_NAMES = {
    "job_log": "job_log.csv", "invoice_export": "invoice_export.csv",
    "vendor_purchases": "vendor_purchases.csv", "technician_time": "technician_time.csv",
    "customer_list": "customer_list.csv", "payment_status": "payment_status.csv",
}


class IntakeError(Exception):
    pass


def load_month_files(month_dir):
    """Load all six files, verifying presence and required columns.
    Raises IntakeError (hard stop) on missing file/column/empty file."""
    raw = {}
    for key, fname in FILE_NAMES.items():
        path = os.path.join(month_dir, fname)
        if not os.path.exists(path):
            raise IntakeError(f"Required file missing: {fname}")
        df = pd.read_csv(path, dtype=str, keep_default_na=False)
        missing_cols = [c for c in REQUIRED_COLUMNS[key] if c not in df.columns]
        if missing_cols:
            raise IntakeError(f"{fname} missing required columns: {missing_cols}")
        if len(df) == 0:
            raise IntakeError(f"{fname} has zero data rows")
        raw[key] = df
    return raw


def _split(df, accepted_rows, rejected_rows, id_col):
    accepted = pd.DataFrame(accepted_rows) if accepted_rows else df.iloc[0:0].copy()
    rejected = pd.DataFrame(rejected_rows) if rejected_rows else pd.DataFrame(columns=list(df.columns) + ["RejectReason"])
    return accepted, rejected


def validate_job_log(df, as_of_date):
    accepted, rejected = [], []
    seen_ids = set()
    for _, row in df.iterrows():
        reasons = []
        jid = row["JobID"].strip()
        if not jid:
            reasons.append("MissingJobID")
        elif jid in seen_ids:
            reasons.append("DuplicateJobID")
        seen_ids.add(jid)

        open_dt, open_ok = parse_date_safe(row["OpenDate"])
        if not open_ok:
            reasons.append("UnparseableOpenDate")
        elif open_dt is None:
            reasons.append("MissingOpenDate")

        close_dt, close_ok = parse_date_safe(row["CloseDate"])
        if not close_ok:
            reasons.append("UnparseableCloseDate")
        if open_dt and close_dt and close_dt < open_dt:
            reasons.append("InvalidDateSequence")

        rec = row.to_dict()
        rec["OpenDateParsed"] = open_dt
        rec["CloseDateParsed"] = close_dt
        rec["IsFutureOpenDate"] = bool(open_dt and open_dt > as_of_date)

        if reasons:
            rec["RejectReason"] = ";".join(reasons)
            rejected.append(rec)
        else:
            accepted.append(rec)
    return _split(df, accepted, rejected, "JobID")


def validate_invoice_export(df, as_of_date):
    accepted, rejected = [], []
    seen_ids = set()
    for _, row in df.iterrows():
        reasons = []
        iid = row["InvoiceID"].strip()
        if not iid:
            reasons.append("MissingInvoiceID")
        elif iid in seen_ids:
            reasons.append("DuplicateInvoiceID")
        seen_ids.add(iid)

        amt, amt_ok = clean_amount(row["AmountRaw"])
        if not amt_ok:
            reasons.append("InvalidOrMissingAmount")

        dt, dt_ok = parse_date_safe(row["InvoiceDateRaw"])
        if not dt_ok:
            reasons.append("UnparseableInvoiceDate")
        elif dt is None:
            reasons.append("MissingInvoiceDate")

        rec = row.to_dict()
        rec["AmountClean"] = amt
        rec["InvoiceDateParsed"] = dt
        rec["IsNegativeAmount"] = bool(amt is not None and amt < 0)
        rec["IsFutureDate"] = bool(dt and dt > as_of_date)

        if reasons:
            rec["RejectReason"] = ";".join(reasons)
            rejected.append(rec)
        else:
            accepted.append(rec)
    return _split(df, accepted, rejected, "InvoiceID")


def validate_vendor_purchases(df, as_of_date):
    accepted, rejected = [], []
    seen_ids = set()
    for _, row in df.iterrows():
        reasons = []
        pid = row["PurchaseID"].strip()
        if not pid:
            reasons.append("MissingPurchaseID")
        elif pid in seen_ids:
            reasons.append("DuplicatePurchaseID")
        seen_ids.add(pid)

        if not row["VendorRawName"].strip():
            reasons.append("MissingVendorName")

        amt, amt_ok = clean_amount(row["AmountRaw"])
        if not amt_ok:
            reasons.append("InvalidOrMissingAmount")

        dt, dt_ok = parse_date_safe(row["PurchaseDateRaw"])
        if not dt_ok:
            reasons.append("UnparseablePurchaseDate")
        elif dt is None:
            reasons.append("MissingPurchaseDate")

        rec = row.to_dict()
        rec["AmountClean"] = amt
        rec["PurchaseDateParsed"] = dt
        rec["IsNegativeAmount"] = bool(amt is not None and amt < 0)

        if reasons:
            rec["RejectReason"] = ";".join(reasons)
            rejected.append(rec)
        else:
            accepted.append(rec)
    return _split(df, accepted, rejected, "PurchaseID")


def validate_technician_time(df, as_of_date):
    accepted, rejected = [], []
    for _, row in df.iterrows():
        reasons = []
        if not row["TechRawName"].strip():
            reasons.append("MissingTechnician")
        if not row["JobID"].strip():
            reasons.append("MissingJobID")

        dt, dt_ok = parse_date_safe(row["DateRaw"])
        if not dt_ok:
            reasons.append("UnparseableDate")

        try:
            hours = float(row["HoursRaw"])
        except (ValueError, TypeError):
            hours = None
            reasons.append("InvalidHours")

        rec = row.to_dict()
        rec["DateParsed"] = dt
        rec["HoursClean"] = hours
        rec["IsImplausibleHours"] = bool(hours is not None and (hours <= 0 or hours > 16))

        if reasons:
            rec["RejectReason"] = ";".join(reasons)
            rejected.append(rec)
        else:
            accepted.append(rec)
    return _split(df, accepted, rejected, None)


def validate_customer_list(df, as_of_date):
    accepted, rejected = [], []
    for _, row in df.iterrows():
        reasons = []
        if not row["RawName"].strip():
            reasons.append("MissingCustomerName")
        rec = row.to_dict()
        if reasons:
            rec["RejectReason"] = ";".join(reasons)
            rejected.append(rec)
        else:
            accepted.append(rec)
    return _split(df, accepted, rejected, None)


def validate_payment_status(df, as_of_date):
    accepted, rejected = [], []
    seen_ids = set()
    for _, row in df.iterrows():
        reasons = []
        pid = row["PaymentID"].strip()
        if not pid:
            reasons.append("MissingPaymentID")
        elif pid in seen_ids:
            reasons.append("DuplicatePaymentID")
        seen_ids.add(pid)

        if not row["InvoiceID"].strip():
            reasons.append("MissingInvoiceReference")

        amt, amt_ok = clean_amount(row["AmountPaidRaw"])
        if not amt_ok:
            reasons.append("InvalidOrMissingAmount")

        dt, dt_ok = parse_date_safe(row["PaymentDateRaw"])
        if not dt_ok:
            reasons.append("UnparseablePaymentDate")
        elif dt is None:
            reasons.append("MissingPaymentDate")

        rec = row.to_dict()
        rec["AmountPaidClean"] = amt
        rec["PaymentDateParsed"] = dt
        rec["IsFutureDate"] = bool(dt and dt > as_of_date)

        if reasons:
            rec["RejectReason"] = ";".join(reasons)
            rejected.append(rec)
        else:
            accepted.append(rec)
    return _split(df, accepted, rejected, "PaymentID")


VALIDATORS = {
    "job_log": validate_job_log,
    "invoice_export": validate_invoice_export,
    "vendor_purchases": validate_vendor_purchases,
    "technician_time": validate_technician_time,
    "customer_list": validate_customer_list,
    "payment_status": validate_payment_status,
}


def run_intake(month_dir, as_of_date):
    raw = load_month_files(month_dir)
    accepted, rejected, raw_counts = {}, {}, {}
    for key, df in raw.items():
        raw_counts[key] = len(df)
        acc, rej = VALIDATORS[key](df, as_of_date)
        accepted[key] = acc
        rejected[key] = rej
    return accepted, rejected, raw_counts
