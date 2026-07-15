"""Breakthrough pipeline - shared helpers: normalization, parsing, fuzzy matching.

Implements the standardization primitives described in Data Dictionary.md,
Validation Rules.md, and Transformation Logic.md. No business-specific
canonical *answer keys* live here for vendors/customers/technicians - those
are discovered from data via canonicalize.py. Service-type/status/location
lists ARE legitimate fixed business taxonomies and are defined here.
"""

import re
import difflib
from datetime import datetime, date

# --- Closed business taxonomies (legitimate fixed reference lists) --------

SERVICE_TYPE_CANONICAL = [
    "Plumbing Repair", "Plumbing Installation", "Drain Cleaning",
    "Water Heater Service", "HVAC Repair", "HVAC Installation",
    "HVAC Maintenance", "Emergency Service",
]

STATUS_CANONICAL = {
    "open": "Open", "in progress": "Open",
    "completed": "Completed", "complete": "Completed", "closed": "Completed",
    "cancelled": "Cancelled", "canceled": "Cancelled",
}

DATE_FORMATS = ["%m/%d/%Y", "%Y-%m-%d", "%b %d, %Y"]

STOPWORDS = {"inc", "llc", "co", "corp", "company", "ltd"}


def normalize_text(value):
    if value is None:
        return ""
    text = str(value).strip().lower()
    text = re.sub(r"[.,]", "", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def core_tokens(value):
    text = normalize_text(value)
    tokens = [t for t in text.split(" ") if t and t not in STOPWORDS]
    return tokens


def core_key(value):
    return " ".join(sorted(core_tokens(value)))


def map_service_type(raw):
    if raw is None or str(raw).strip() == "":
        return None, False
    for canon in SERVICE_TYPE_CANONICAL:
        if normalize_text(raw) == normalize_text(canon):
            return canon, True
    return raw, False  # unknown - return raw for visibility, flag=False


def map_status(raw):
    if raw is None or str(raw).strip() == "":
        return None, False
    key = normalize_text(raw)
    if key in STATUS_CANONICAL:
        return STATUS_CANONICAL[key], True
    return raw, False


def parse_date_safe(raw):
    """Return (date_obj_or_None, ok_bool)."""
    if raw is None or str(raw).strip() == "":
        return None, True  # blank date is handled by required-field rule elsewhere, not a parse failure
    text = str(raw).strip()
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).date(), True
        except ValueError:
            continue
    return None, False


def clean_amount(raw):
    """Return (float_or_None, ok_bool). Blank -> (None, False) i.e. reject."""
    if raw is None or str(raw).strip() == "":
        return None, False
    text = str(raw).strip().replace("$", "").replace(",", "")
    negative = text.startswith("(") and text.endswith(")")
    if negative:
        text = text[1:-1]
    try:
        val = float(text)
    except ValueError:
        return None, False
    if negative:
        val = -val
    return val, True


def fuzzy_ratio(a, b):
    return difflib.SequenceMatcher(None, a, b).ratio()


def is_initial_variant(raw_tokens, full_tokens):
    """True if raw_tokens looks like an abbreviated form of full_tokens,
    e.g. ["r", "chen"] vs ["ray", "chen"] or ["t", "brooks"] vs ["tyler", "brooks"]."""
    if len(raw_tokens) != len(full_tokens) or not raw_tokens:
        return False
    if raw_tokens[-1] != full_tokens[-1]:
        return False
    first_raw, first_full = raw_tokens[0], full_tokens[0]
    return len(first_raw) == 1 and first_full.startswith(first_raw) and first_raw != first_full


def match_entity(raw_name, registry, use_fuzzy_tier=True):
    """Attempt to match raw_name against an existing registry of canonical
    entities. registry: dict canonical_id -> {"CanonicalName", "CoreKey",
    "Tokens": set, "TokensOrdered": list, "Aliases": set}.

    Returns (canonical_id_or_None, match_method, confidence, review_flag).
    Conservative: only exact/subset matches auto-merge with High confidence.
    Fuzzy and initial-abbreviation matches are returned with review_flag=True -
    callers decide whether to link provisionally or create a new entity and
    flag a possible duplicate. Nothing ambiguous is silently forced.
    """
    key = core_key(raw_name)
    tokens = set(core_tokens(raw_name))
    tokens_ordered = core_tokens(raw_name)
    if not tokens:
        return None, "Invalid", "N/A", True

    # 1. exact core-key match (covers case/punctuation/suffix variants)
    for cid, entry in registry.items():
        if entry["CoreKey"] == key:
            return cid, "ExactNormalized", "High", False

    # 2. alias map override (manually curated - see alias_map.csv)
    for cid, entry in registry.items():
        if key in entry.get("Aliases", set()):
            return cid, "AliasMap", "High", False

    # 3. token subset match (e.g. "Ferguson" within "Ferguson Plumbing Supply")
    for cid, entry in registry.items():
        etoks = entry["Tokens"]
        if tokens.issubset(etoks) or etoks.issubset(tokens):
            return cid, "TokenSubset", "High", False

    # 4. initial-abbreviation match (e.g. "R. Chen" vs "Ray Chen") - flagged, not auto-merged
    for cid, entry in registry.items():
        if is_initial_variant(tokens_ordered, entry["TokensOrdered"]) or \
           is_initial_variant(entry["TokensOrdered"], tokens_ordered):
            return cid, "InitialMatch", "Medium", True

    # 5. fuzzy ratio bands - deliberately restricted to entity types where
    # character-level similarity is a reasonable proxy for "same entity"
    # (business/vendor names). For person names (customers, technicians) this
    # tier produces false positives whenever two different people share a
    # first OR last name (e.g. "Daniel Moore" vs "Daniel Miller"), so it is
    # disabled for those entity types by the caller (use_fuzzy_tier=False).
    # Person-name near-duplicates are still caught precisely by tier 4
    # (InitialMatch) without this noise. See Test Plan and Results.md.
    if use_fuzzy_tier:
        best_cid, best_ratio = None, 0.0
        for cid, entry in registry.items():
            r = fuzzy_ratio(key, entry["CoreKey"])
            if r > best_ratio:
                best_cid, best_ratio = cid, r
        if best_ratio >= 0.85:
            return best_cid, "FuzzyHighConfidence", "High", False
        if best_ratio >= 0.65:
            return best_cid, "FuzzyReviewRequired", "Medium", True

    return None, "NewEntity", "N/A", False
