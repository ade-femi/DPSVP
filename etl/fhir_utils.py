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


def as_dict(value) -> dict:
    return value if isinstance(value, dict) else {}


def as_list(value) -> list:
    return value if isinstance(value, list) else []
