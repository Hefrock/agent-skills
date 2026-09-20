#!/usr/bin/env python3
"""
n2c2 adapter — loads real n2c2/i2b2 2014-format de-identification XML files into
manifest-schema.md's RECORD/SPAN shape (issue #126, Tier 2).

n2c2 doesn't fit person_sources.py's PersonSource registry: that registry swaps out
where synthetic DEMOGRAPHIC FIELDS come from, feeding generate_corpus.py's segment-
assembly pipeline. n2c2 is pre-existing, already-annotated real clinical narrative --
nothing to generate, an existing corpus to load. This module is that separate ingestion
path, deliberately NOT wired into generate_corpus.py's CLI yet (see load_records).

SCOPE AND CONFIDENCE (read before extending this file)

The corpus itself is DUA-gated (Harvard DBMI); this adapter was built without it, from
publicly available sources only, scoped by a premortem run before writing any code
(issue #126). What's confirmed vs. still open:

  CONFIRMED, cross-checked against a real working parser for this exact corpus
  (github.com/google/NeuroNER-CSPMC's xml_to_brat.py, read directly, not summarized):
  the file shape is a root element containing one <TEXT> element (the raw note) and one
  <TAGS> element whose children are one per PHI instance, each carrying start/end/text/
  TYPE attributes -- the child element's own tag name is the top-level category (NAME,
  LOCATION, ...), TYPE is the finer subcategory. Independently confirmed subcategory
  lists for two categories: LOCATION -> ROOM, DEPARTMENT, HOSPITAL, ORGANIZATION,
  STREET, CITY, STATE, COUNTRY, ZIP, OTHER; ID -> SOCIAL SECURITY NUMBER, MEDICAL
  RECORD NUMBER, HEALTH PLAN NUMBER, ACCOUNT NUMBER, LICENSE NUMBER, VEHICLE ID,
  DEVICE ID, BIOMETRIC ID, ID NUMBER. Multiple independent sources agree on 6 top-level
  categories, 25 subcategories, 28,872 total PHI instances across the real corpus.

  NOT confirmed: DATE, AGE, CONTACT, PROFESSION, and most of NAME's actual tag/TYPE
  structure -- ScienceDirect, ResearchGate, PMC, and the n2c2 portal itself (the
  primary sources: the Stubbs & Uzuner 2015 paper and the challenge's own site) are all
  blocked by this environment's egress proxy. DATE matters most here: it's the
  harness's own documented highest-yield leakage category (see
  references/safe-harbor-identifiers.md) and is exactly the one left unverified.
  Also NOT confirmed: how n2c2 encodes "these notes belong to the same patient" --
  the corpus is explicitly longitudinal (1,304 notes across 296 patients) but no
  source found described the filename/grouping convention.

  Consequence for this file: CATEGORY_MAP below is deliberately partial, covering only
  entries confirmed above. map_category() raises UnmappedCategoryError -- loud, not a
  silent default -- for anything else, the same "unsafe/unknown is never silent"
  discipline person_sources.py's MA_CITY_ZIP3 fallback and inference_attackers.py's
  compliance gate already use. identity_key is a placeholder (see parse_file) pending
  the grouping question above. Extend CATEGORY_MAP only after confirming an entry
  against a primary source or real data -- never guess one in.

  This has been validated against a hand-built fixture (fixtures/n2c2/) shaped to the
  confirmed schema above -- NOT against real n2c2 files. Per this project's own
  precedent (the Synthea FHIR reader was fixture-validated first and still surfaced two
  real bugs only at real scale -- see references/data-sources.md), expect at least one
  more surprise once real files are available, even for the categories mapped here.
"""
from __future__ import annotations
import glob, os
import xml.etree.ElementTree as ET

# (element tag, TYPE) -> hipaa_category, confirmed-only (see module docstring). TYPE is
# upper-cased before lookup since case in real files isn't confirmed either way.
CATEGORY_MAP = {
    ("LOCATION", "STREET"): "geo_subdivision",
    ("LOCATION", "CITY"): "geo_subdivision",
    ("LOCATION", "STATE"): "geo_subdivision",
    ("LOCATION", "COUNTRY"): "geo_subdivision",
    ("LOCATION", "ZIP"): "geo_subdivision",
    ("ID", "SOCIAL SECURITY NUMBER"): "ssn",
    ("ID", "MEDICAL RECORD NUMBER"): "mrn",
    ("ID", "HEALTH PLAN NUMBER"): "health_plan_id",
    ("ID", "ACCOUNT NUMBER"): "account_number",
    ("ID", "LICENSE NUMBER"): "license_number",
    ("ID", "VEHICLE ID"): "vehicle_id",
    ("ID", "DEVICE ID"): "device_id",
    ("ID", "BIOMETRIC ID"): "biometric_id",
    ("ID", "ID NUMBER"): "other_unique_id",
}


class UnmappedCategoryError(Exception):
    """An n2c2 (element, TYPE) pair with no reviewed hipaa_category mapping yet. Raised,
    never caught-and-defaulted internally -- see CATEGORY_MAP's docstring in the module
    header for why guessing one in here would be the wrong fix."""


def map_category(element_tag: str, type_attr: "str | None") -> str:
    key = (element_tag, (type_attr or "").upper())
    if key not in CATEGORY_MAP:
        raise UnmappedCategoryError(
            f"no reviewed hipaa_category mapping for n2c2 category ({element_tag!r}, "
            f"TYPE={type_attr!r}). Add one to CATEGORY_MAP only after confirming it "
            f"against a primary source (the Stubbs & Uzuner 2015 paper, or the n2c2 "
            f"portal) or real data -- issue #126, Tier 2. Never default this silently.")
    return CATEGORY_MAP[key]


def parse_file(path: str) -> dict:
    """One n2c2-format XML file -> a manifest-schema.md RECORD dict.

    Track 1 (Safe Harbor leakage) scope only: `identifiers` is populated and self-
    tested; `quasi_identifiers`/`inference_case` are not attempted here (Tier 3/4).
    `identity_key` is a PLACEHOLDER (the filename stem) -- n2c2's real patient-grouping
    convention is unconfirmed (see module docstring), so do not trust any Track 2
    linkage number built from records this function returns until it's replaced with a
    confirmed grouping.
    """
    root = ET.parse(path).getroot()
    text = root.findtext("TEXT")
    if text is None:
        raise ValueError(f"{path}: no <TEXT> element found")
    tags_elem = root.find("TAGS")
    if tags_elem is None:
        raise ValueError(f"{path}: no <TAGS> element found")

    record_id = os.path.splitext(os.path.basename(path))[0]
    spans = []
    for j, tag in enumerate(tags_elem):
        start_raw, end_raw, span_text = tag.get("start"), tag.get("end"), tag.get("text")
        if start_raw is None or end_raw is None or span_text is None:
            # Never silently skip or default a missing offset/text -- that's exactly
            # the shape of bug this whole adapter exists to avoid (see module docstring).
            raise ValueError(
                f"{path}: <{tag.tag}> tag missing a required attribute "
                f"(start={start_raw!r} end={end_raw!r} text={span_text!r})")
        type_attr = tag.get("TYPE")
        spans.append({
            "span_id": f"{record_id}:s{j:02d}",
            "start": int(start_raw), "end": int(end_raw), "text": span_text,
            "hipaa_category": map_category(tag.tag, type_attr),
            # Placeholders, not reviewed against how Track 1 slices its report (category
            # x surface_form x context) -- unlike the synthetic generator, n2c2's real
            # text wasn't rendered by this harness, so there's no injected "how was this
            # value written" metadata to carry. Revisit once Track 1 is actually run on
            # n2c2 output and it's clear whether this granularity is useful or noise.
            "surface_form": (type_attr or "").lower() or "unknown",
            "context": "n2c2",
        })

    return {
        "record_id": record_id,
        "identity_key": record_id,  # PLACEHOLDER -- see docstring above
        "note_text": text,
        "identifiers": spans,
    }


def self_test(record: dict) -> None:
    """The harness's own core invariant, unconditionally: note_text[start:end] == text
    for every span. Mirrors generate_corpus.self_test -- an n2c2-sourced record is never
    trusted without this, exactly like a generated one."""
    t = record["note_text"]
    bad = [s["span_id"] for s in record["identifiers"] if t[s["start"]:s["end"]] != s["text"]]
    if bad:
        raise AssertionError(f"{record['record_id']}: offset invariant failed for spans {bad}")


def load_records(directory: str) -> list:
    """Parse every *.xml file in `directory` (non-recursive) into a self-tested RECORD.

    Deliberately NOT wired into generate_corpus.py's CLI: Tier 2 isn't complete (see
    this module's docstring and references/data-sources.md) -- the category mapping is
    partial by design and identity_key is a placeholder. This is ingestion
    infrastructure to build and test against now, not a ready-to-run corpus source.
    """
    if not directory or not os.path.isdir(directory):
        raise SystemExit(f"n2c2 directory not found: {directory!r}")
    records = []
    for path in sorted(glob.glob(os.path.join(directory, "*.xml"))):
        rec = parse_file(path)
        self_test(rec)
        records.append(rec)
    if not records:
        raise SystemExit(f"no .xml files found under {directory!r}")
    return records
