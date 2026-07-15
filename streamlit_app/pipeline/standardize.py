"""Layer 3 - Standardization.

Applies the closed ServiceType/Status taxonomies to the Job Log, and runs
canonical entity matching (Customer, Vendor, Technician) using the
persistent registries in canonicalize.py. Every unmapped category/status is
flagged, never guessed. Every canonicalization decision is recorded with
RawName, NormalizedName, CanonicalID, MatchMethod, MatchConfidence,
ReviewFlag - the exact field set specified in BREAKTHROUGH.md's Canonical
Data and Alias Framework.
"""

import pandas as pd

from common import map_service_type, map_status, match_entity, core_key
from canonicalize import (
    load_registry, save_registry, load_alias_overrides, register,
    seed_registry_from_alias_map,
)


def standardize_job_log(jobs_df):
    svc_canon, svc_ok, status_canon, status_ok = [], [], [], []
    for _, row in jobs_df.iterrows():
        sc, ok = map_service_type(row["ServiceTypeRaw"])
        svc_canon.append(sc); svc_ok.append(ok)
        stc, sok = map_status(row["StatusRaw"])
        status_canon.append(stc); status_ok.append(sok)
    jobs_df = jobs_df.copy()
    jobs_df["ServiceTypeCanonical"] = svc_canon
    jobs_df["ServiceTypeKnown"] = svc_ok
    jobs_df["StatusCanonical"] = status_canon
    jobs_df["StatusKnown"] = status_ok
    return jobs_df


def canonicalize_entities(jobs_df, purchases_df, time_df, customers_df):
    """Runs Customer, Vendor, and Technician canonicalization. Returns the
    updated dataframes (with canonical ID/name/match columns added) plus the
    three registries (persisted to disk by the caller after review)."""

    cust_registry = seed_registry_from_alias_map("Customer", load_registry("Customer"))
    vend_registry = seed_registry_from_alias_map("Vendor", load_registry("Vendor"))
    tech_registry = seed_registry_from_alias_map("Technician", load_registry("Technician"))
    cust_overrides = load_alias_overrides("Customer")
    vend_overrides = load_alias_overrides("Vendor")
    tech_overrides = load_alias_overrides("Technician")

    possible_duplicates = []  # collected here, surfaced in Exception Report

    FUZZY_TIER_ENABLED = {"Customer": False, "Technician": False, "Vendor": True}

    def resolve(raw_name, entity_type, registry, overrides):
        cid, cname, method, conf, review, is_new = register(
            raw_name, entity_type, registry, overrides, match_entity,
            use_fuzzy_tier=FUZZY_TIER_ENABLED.get(entity_type, True),
        )
        if review and not is_new:
            possible_duplicates.append({
                "EntityType": entity_type, "RawName": raw_name,
                "LikelyCanonicalID": cid, "LikelyCanonicalName": cname,
                "MatchMethod": method, "MatchConfidence": conf,
            })
        return cid, cname, method, conf, review

    # Customers: derive raw names from BOTH the customer list and job log
    # (a customer may appear in jobs before/without a separate list entry).
    all_customer_raw = pd.concat([
        customers_df["RawName"], jobs_df["RawCustomerName"]
    ]).dropna().unique() if len(customers_df) or len(jobs_df) else []

    cust_lookup = {}
    for raw in all_customer_raw:
        cid, cname, method, conf, review = resolve(raw, "Customer", cust_registry, cust_overrides)
        cust_lookup[raw] = (cid, cname, method, conf, review)

    jobs_df = jobs_df.copy()
    jobs_df["CanonicalCustomerID"] = jobs_df["RawCustomerName"].map(lambda r: cust_lookup.get(r, (None, None, None, None, None))[0])
    jobs_df["CanonicalCustomerName"] = jobs_df["RawCustomerName"].map(lambda r: cust_lookup.get(r, (None, None, None, None, None))[1])
    jobs_df["CustomerMatchMethod"] = jobs_df["RawCustomerName"].map(lambda r: cust_lookup.get(r, (None, None, None, None, None))[2])
    jobs_df["CustomerMatchConfidence"] = jobs_df["RawCustomerName"].map(lambda r: cust_lookup.get(r, (None, None, None, None, None))[3])
    jobs_df["CustomerReviewFlag"] = jobs_df["RawCustomerName"].map(lambda r: cust_lookup.get(r, (None, None, None, None, None))[4])

    # Vendors
    vend_lookup = {}
    for raw in (purchases_df["VendorRawName"].dropna().unique() if len(purchases_df) else []):
        cid, cname, method, conf, review = resolve(raw, "Vendor", vend_registry, vend_overrides)
        vend_lookup[raw] = (cid, cname, method, conf, review)

    purchases_df = purchases_df.copy()
    if len(purchases_df):
        purchases_df["VendorCanonicalID"] = purchases_df["VendorRawName"].map(lambda r: vend_lookup.get(r, (None, None, None, None, None))[0])
        purchases_df["VendorCanonicalName"] = purchases_df["VendorRawName"].map(lambda r: vend_lookup.get(r, (None, None, None, None, None))[1])
        purchases_df["VendorMatchMethod"] = purchases_df["VendorRawName"].map(lambda r: vend_lookup.get(r, (None, None, None, None, None))[2])
        purchases_df["VendorMatchConfidence"] = purchases_df["VendorRawName"].map(lambda r: vend_lookup.get(r, (None, None, None, None, None))[3])
        purchases_df["VendorReviewFlag"] = purchases_df["VendorRawName"].map(lambda r: vend_lookup.get(r, (None, None, None, None, None))[4])

    # Technicians: derive from job log + time log
    all_tech_raw = pd.concat([
        jobs_df["TechnicianRawName"], (time_df["TechRawName"] if len(time_df) else pd.Series(dtype=str))
    ]).dropna().unique()

    tech_lookup = {}
    for raw in all_tech_raw:
        cid, cname, method, conf, review = resolve(raw, "Technician", tech_registry, tech_overrides)
        tech_lookup[raw] = (cid, cname, method, conf, review)

    jobs_df["TechnicianID"] = jobs_df["TechnicianRawName"].map(lambda r: tech_lookup.get(r, (None, None, None, None, None))[0])
    jobs_df["TechnicianCanonicalName"] = jobs_df["TechnicianRawName"].map(lambda r: tech_lookup.get(r, (None, None, None, None, None))[1])

    if len(time_df):
        time_df = time_df.copy()
        time_df["TechnicianID"] = time_df["TechRawName"].map(lambda r: tech_lookup.get(r, (None, None, None, None, None))[0])
        time_df["TechnicianCanonicalName"] = time_df["TechRawName"].map(lambda r: tech_lookup.get(r, (None, None, None, None, None))[1])

    registries = {"Customer": cust_registry, "Vendor": vend_registry, "Technician": tech_registry}
    return jobs_df, purchases_df, time_df, registries, pd.DataFrame(possible_duplicates)


def persist_registries(registries):
    for entity_type, reg in registries.items():
        save_registry(entity_type, reg)
