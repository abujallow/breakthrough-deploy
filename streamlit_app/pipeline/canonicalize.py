"""Layer 3/4 - canonical entity registries for Customers, Vendors, Technicians.

Registries persist to disk between monthly runs (registry/*.json), so
canonical IDs are STABLE across a refresh: a vendor or customer identified
in Month 1 keeps the same CanonicalID in Month 2, and brand-new raw names
extend the registry rather than rebuilding it. This directly demonstrates
BREAKTHROUGH.md's "new aliases can be added without rewriting core logic."

A manually curated alias_map.csv (AliasSource=Manual) can force a match the
automatic logic can't confidently make (e.g. an abbreviation) - this models
the real maintenance workflow ("Recurring Support: Alias updates").
"""

import csv
import json
import os

from common import core_key, core_tokens

PIPELINE_DIR = os.path.dirname(os.path.abspath(__file__))
# Deployment override: use a per-Streamlit-session writable temp directory
# instead of writing next to the code (Streamlit Community Cloud sessions
# should not share or persist registry state across different visitors).
REGISTRY_DIR = os.environ.get("BREAKTHROUGH_REGISTRY_DIR") or os.path.join(PIPELINE_DIR, "registry")
ALIAS_MAP_PATH = os.path.join(PIPELINE_DIR, "alias_map.csv")

ENTITY_PREFIX = {"Customer": "CUST", "Vendor": "VEND", "Technician": "TECH"}
ID_WIDTH = {"Customer": 3, "Vendor": 2, "Technician": 2}


def _registry_path(entity_type):
    return os.path.join(REGISTRY_DIR, f"{entity_type.lower()}_registry.json")


def load_registry(entity_type):
    path = _registry_path(entity_type)
    if not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8") as f:
        raw = json.load(f)
    registry = {}
    for cid, entry in raw.items():
        registry[cid] = {
            "CanonicalName": entry["CanonicalName"],
            "CoreKey": entry["CoreKey"],
            "Tokens": set(entry["Tokens"]),
            "TokensOrdered": entry["TokensOrdered"],
            "Aliases": set(entry.get("Aliases", [])),
        }
    return registry


def save_registry(entity_type, registry):
    os.makedirs(REGISTRY_DIR, exist_ok=True)
    serializable = {}
    for cid, entry in registry.items():
        serializable[cid] = {
            "CanonicalName": entry["CanonicalName"],
            "CoreKey": entry["CoreKey"],
            "Tokens": sorted(entry["Tokens"]),
            "TokensOrdered": entry["TokensOrdered"],
            "Aliases": sorted(entry["Aliases"]),
        }
    with open(_registry_path(entity_type), "w", encoding="utf-8") as f:
        json.dump(serializable, f, indent=2)


def load_alias_overrides(entity_type):
    """Return dict: core_key(raw alias) -> canonical_id, for manual overrides."""
    overrides = {}
    if not os.path.exists(ALIAS_MAP_PATH):
        return overrides
    with open(ALIAS_MAP_PATH, "r", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if row["EntityType"] == entity_type:
                overrides[core_key(row["RawNameOrAlias"])] = row["CanonicalID"]
    return overrides


def seed_registry_from_alias_map(entity_type, registry):
    """Pre-create canonical entities named explicitly in alias_map.csv so a
    manually confirmed vendor/customer/technician exists BEFORE raw data is
    processed. This makes manual alias overrides deterministic regardless
    of which raw variant happens to appear first in the source file."""
    if not os.path.exists(ALIAS_MAP_PATH):
        return registry
    with open(ALIAS_MAP_PATH, "r", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if row["EntityType"] != entity_type:
                continue
            cid = row["CanonicalID"]
            cname = row.get("CanonicalName", "").strip()
            if cid and cname and cid not in registry:
                registry[cid] = {
                    "CanonicalName": cname,
                    "CoreKey": core_key(cname),
                    "Tokens": set(core_tokens(cname)),
                    "TokensOrdered": core_tokens(cname),
                    "Aliases": set(),
                }
    return registry


def smart_title(name):
    """Title-case a raw name for display while preserving short acronyms
    (ABC, HD, HVAC) that naive .title() would otherwise mangle."""
    out = []
    for w in str(name).split():
        core = w.strip(".,")
        if core.isupper() and 1 < len(core) <= 3:
            out.append(w)
        else:
            out.append(w.capitalize())
    return " ".join(out)


def _is_better_display(candidate, current):
    """True if candidate is a fuller / better-formatted name than the
    currently stored canonical display name (prefers full names over
    initials, and proper case over ALL CAPS / all lowercase)."""
    cand_tokens = [t.strip(".,") for t in candidate.split()]
    curr_tokens = [t.strip(".,") for t in current.split()]
    cand_has_initial = any(len(t) == 1 for t in cand_tokens)
    curr_has_initial = any(len(t) == 1 for t in curr_tokens)
    if curr_has_initial and not cand_has_initial:
        return True
    if curr_has_initial == cand_has_initial:
        curr_is_shouty_or_flat = (current == current.upper() or current == current.lower())
        cand_is_proper = not (candidate == candidate.upper() or candidate == candidate.lower())
        if curr_is_shouty_or_flat and cand_is_proper:
            return True
    return False


def next_id(entity_type, registry):
    prefix = ENTITY_PREFIX[entity_type]
    width = ID_WIDTH[entity_type]
    existing = [int(cid.split("-")[1]) for cid in registry if cid.startswith(prefix)]
    n = (max(existing) + 1) if existing else 1
    return f"{prefix}-{str(n).zfill(width)}"


def register(raw_name, entity_type, registry, alias_overrides, match_fn, use_fuzzy_tier=True):
    """Return (canonical_id, canonical_name, match_method, confidence,
    review_flag, is_new). Applies manual alias override first, then the
    automatic match_fn (common.match_entity), then creates a new entity.
    use_fuzzy_tier=False for person-name entities (Customer, Technician) to
    avoid first/last-name-collision false positives - see common.py."""
    key = core_key(raw_name)

    if key in alias_overrides and alias_overrides[key] in registry:
        cid = alias_overrides[key]
        return cid, registry[cid]["CanonicalName"], "AliasMap-Manual", "High", False, False

    cid, method, confidence, review_flag = match_fn(raw_name, registry, use_fuzzy_tier)
    if cid is not None:
        candidate_display = smart_title(raw_name)
        if not review_flag and _is_better_display(candidate_display, registry[cid]["CanonicalName"]):
            registry[cid]["CanonicalName"] = candidate_display
        return cid, registry[cid]["CanonicalName"], method, confidence, review_flag, False

    # new entity
    new_id = next_id(entity_type, registry)
    registry[new_id] = {
        "CanonicalName": smart_title(raw_name),
        "CoreKey": key,
        "Tokens": set(core_tokens(raw_name)),
        "TokensOrdered": core_tokens(raw_name),
        "Aliases": set(),
    }
    return new_id, registry[new_id]["CanonicalName"], "NewEntity", "N/A", False, True
