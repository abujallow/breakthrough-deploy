"""Layer 7 — Audit and Maintenance. Appends one JSON line per pipeline run
to a persistent audit log, independent of the per-month Data Quality Report,
so the full refresh history is visible over time."""

import json
import os
from datetime import datetime

PIPELINE_DIR = os.path.dirname(os.path.abspath(__file__))
AUDIT_LOG_PATH = os.path.join(PIPELINE_DIR, "audit_log.jsonl")


def record_run(month_label, as_of_date, raw_counts, accepted_counts, rejected_counts,
                exception_count, registry_sizes, warnings=None):
    entry = {
        "RunTimestampUTC": datetime.utcnow().isoformat(),
        "Month": month_label,
        "AsOfDate": str(as_of_date),
        "RawRecordCounts": raw_counts,
        "AcceptedRecordCounts": accepted_counts,
        "RejectedRecordCounts": rejected_counts,
        "TotalExceptionsLogged": exception_count,
        "RegistrySizes": registry_sizes,
        "Warnings": warnings or [],
    }
    with open(AUDIT_LOG_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")
    return entry
