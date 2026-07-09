#!/usr/bin/env python3
"""
Quasi-identifier model — shared by the population generator and the Track 2
(Expert Determination) scorer.

Both sides MUST bucket QIs identically or the equivalence-class counts are
meaningless, so the generalization lives here, in one place, imported by both.

The QI set and the "generalized" strategy (chosen deliberately over raw
full-precision, which makes almost everyone trivially unique):

  * age             -> 5-year band, with ages >= 90 collapsed to "90+"
                       (ages > 89 are themselves a Safe Harbor identifier, so the
                        cap mirrors the regulation's own aggregation)
  * sex             -> raw
  * zip3            -> raw (already Safe Harbor's coarsest geographic unit; further
                       generalization would be non-standard)
  * rare_diagnosis  -> raw boolean (a rare disease is strongly identifying)

Deliberately NOT in the key:
  * admission_date  -> in the v0 generator every admission is in 2026, so its year
                       is constant and cannot distinguish records. Left in the
                       manifest for a future, configurable QI set.
  * facility_id     -> a data-custodian attribute, not a personal quasi-identifier.

Change this file and BOTH the population counts and the sample counts move together;
that coupling is the point.
"""
from __future__ import annotations

# A record whose equivalence class is smaller than this is flagged high-risk.
K_ANONYMITY_THRESHOLD = 5

# The fields (raw names, as they appear in a manifest QI profile) that feed the key.
QI_FIELDS = ("age", "sex", "zip3", "rare_diagnosis")


def age_band(age: int) -> str:
    """5-year bands, with the Safe Harbor 90+ aggregation applied at the top."""
    if age >= 90:
        return "90+"
    lo = (age // 5) * 5
    return f"{lo}-{lo + 4}"


def generalize(qi: dict) -> tuple:
    """Map a raw quasi_identifier profile to its generalized equivalence-class key.

    Accepts either a record's `quasi_identifiers` block or a population profile row;
    they share the same field names by construction (see generate_corpus.qi_profile).
    """
    return (
        age_band(int(qi["age"])),
        qi["sex"],
        qi["zip3"],
        bool(qi["rare_diagnosis"]),
    )
