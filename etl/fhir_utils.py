"""
fhir_utils.py — shared helpers for the FHIR staging DataFrames.

fhir_loader.py wraps raw FHIR resource dicts directly in a DataFrame
(etl/fhir_loader.py:89), so any field that isn't present on every resource of
that type becomes NaN (a float) for the rows missing it, instead of None or
{}. `field.get("x") or {}` does not catch this — NaN is truthy in Python, so
`nan or {}` evaluates to `nan`, not `{}` — and the next `.get(...)` call on it
raises AttributeError. as_dict()/as_list() are the guard every mapper needs
before treating an optional nested FHIR field as a dict/list.
"""
from __future__ import annotations

import hashlib

# OMOP surrogate-key columns (person_id, visit_occurrence_id, ...) are
# `integer` in the CDM DDL, so keys must stay below 2^31-1.
_ID_MODULUS = 10**9


def as_dict(value) -> dict:
    return value if isinstance(value, dict) else {}


def as_list(value) -> list:
    return value if isinstance(value, list) else []


def as_str(value) -> str:
    """Coerce a possibly-missing FHIR scalar to a string, NaN included.

    Same NaN hazard as as_dict, one level down: `resource.get("onsetDateTime")`
    is float NaN when the field is absent from some resources of that type, and
    `(nan or "")[:10]` raises TypeError: 'float' object is not subscriptable
    rather than yielding "". Every date/scalar field this pipeline slices needs
    to go through here.
    """
    return value if isinstance(value, str) else ""


def stable_id(*parts: str) -> int:
    """Deterministic positive integer surrogate key derived from source ids.

    Uses SHA-256 rather than Python's builtin hash(): string hashing is
    salted per interpreter process (PYTHONHASHSEED), so hash("abc") differs
    between runs. Surrogate keys built on it are not reproducible, which
    breaks the idempotency this pipeline documents — re-running would insert
    the same patients again under different person_ids.
    """
    digest = hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()
    return int(digest, 16) % _ID_MODULUS
