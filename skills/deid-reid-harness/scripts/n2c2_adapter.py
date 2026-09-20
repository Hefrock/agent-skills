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

The corpus itself is DUA-gated (Harvard DBMI); this adapter was built without it. Two
rounds of sourcing, in order:

  Round 1 (secondary sources only -- ScienceDirect/PMC/the n2c2 portal were all
  blocked by this environment's egress proxy): cross-checked the file SHAPE against a
  real working parser for this exact corpus (github.com/google/NeuroNER-CSPMC's
  xml_to_brat.py, read directly) -- a root element with one <TEXT> (the raw note) and
  one <TAGS> element whose children are one per PHI instance, each carrying
  start/end/text/TYPE attributes. That structural finding still stands and is what
  parse_file() below implements. Category/subcategory coverage from this round was
  partial (LOCATION, ID only) and is superseded by round 2.

  Round 2 (the primary source itself -- Stubbs & Uzuner, "Annotating longitudinal
  clinical narratives for de-identification: The 2014 i2b2/UTHealth corpus", J Biomed
  Inform 58 (2015) S20-S29, doi:10.1016/j.jbi.2015.07.020 -- supplied directly and read
  in full): resolved every category/subcategory gap round 1 left open, AND corrected
  an assumption round 1 got wrong. Specifically:

  - The paper's own illustrative XML figure (Fig. 3) shows PHI tags INLINE in the text
    (e.g. <PATIENT>HOLCOMB,DENNIS</PATIENT>), which looked like it contradicted the
    separate TEXT+TAGS+offsets structure round 1 found -- but the paper explicitly
    calls this "a simplified XML representation for readability" and "in-line to
    simplify the presentation" (sec 4). The offset-based structure from round 1, which
    real distributed-file-parsing code actually implements, is what this adapter uses;
    Fig. 3 corroborates the SUBCATEGORY NAMES (PATIENT, DOCTOR, USERNAME, MEDICALRECORD,
    IDNUM, HOSPITAL, AGE, DATE) rather than the file layout.
  - The paper gives its own literal category:subcategory vocabulary twice, independently
    (the annotation guidelines appendix, and sec 8's list of "HIPAA-identified
    categories": "NAME:PATIENT, AGE, LOCATION:CITY, LOCATION:STREET, LOCATION:ZIP,
    LOCATION:ORGANIZATION, DATE, CONTACT:PHONE, CONTACT:FAX, CONTACT:EMAIL, ID:SSN,
    ID:MEDICALRECORD, ID:HEALTHPLAN, ID:ACCOUNT, ID:LICENSE, ID:VEHICLE, ID:DEVICE,
    ID:BIOID, and ID:IDNUM") -- this is the set SAFE_HARBOR_MAP below is built from.
  - Sec 5.2/8: "we unmarked some of the PHI categories, i.e., ROOM, DEPARTMENT, OTHER"
    before the 2014 release -- these three LOCATION subcategories are in the annotation
    guidelines but CONFIRMED ABSENT from the actual released files. A real file
    containing one would mean the wrong corpus version, not a gap in this table.
  - The paper's own "HIPAA-identified" list (above) deliberately excludes
    NAME:DOCTOR, NAME:USERNAME, PROFESSION, LOCATION:STATE, LOCATION:COUNTRY, and
    LOCATION:HOSPITAL even though all are annotated in the corpus (Table 3). This
    matches actual Safe Harbor rules, not an oversight: a doctor's/username's name
    isn't the *patient's* name in the HIPAA sense, PROFESSION isn't one of HIPAA's 18
    categories at all (it's an n2c2-specific addition -- "the 18 categories have been
    expanded", sec 1), and a US state or country is not "smaller than a state", so
    neither is Safe Harbor's `geo_subdivision`. SAFE_HARBOR_MAP follows the paper's own
    split rather than the more naive "every LOCATION subtype -> geo_subdivision"
    round 1 would have produced. CONTACT:URL and CONTACT:IPADDRESS are the one
    deliberate departure from the paper's list -- included here despite the paper
    omitting them, because HIPAA's own 18 categories explicitly list URLs and IP
    addresses (categories 14-15; see references/safe-harbor-identifiers.md) and Table 3
    shows both are genuinely annotated in the corpus (2 and 0 instances respectively) --
    the paper's omission looks like negligible real-world frequency in this particular
    corpus, not a claim that they aren't Safe Harbor identifiers.

  Still NOT confirmed even after the primary source: the exact literal TYPE-attribute
  string a real released file uses (the paper documents the ANNOTATION SCHEME, not a
  byte-exact file format spec -- e.g. is it TYPE="MEDICALRECORD" or something else?
  map_category() below checks both the tag's own element name and its TYPE attribute
  against the same normalized table for exactly this reason, so it isn't betting on
  which one the real convention actually uses). Also still not confirmed: how n2c2
  encodes "these notes belong to the same patient" -- the corpus is explicitly
  longitudinal (1,304 notes across 296 patients, sec 3) but this paper is about
  annotation methodology, not the file distribution's naming convention; that would be
  in the corpus's own accompanying documentation, not here.

  Consequence for this file: SAFE_HARBOR_MAP is comprehensive for what n2c2 actually
  calls a HIPAA-identified category, and deliberately excludes categories the corpus
  annotates but that are not genuine Safe Harbor identifiers (see above) -- both are
  distinct from "not yet reviewed". map_category() raises UnmappedCategoryError --
  loud, never a silent default -- with a specific reason for each case, the same
  "unsafe/unknown is never silent" discipline person_sources.py's MA_CITY_ZIP3 fallback
  and inference_attackers.py's compliance gate already use. identity_key is still a
  placeholder (see parse_file) pending the grouping question above.

  This has been validated against a hand-built fixture (fixtures/n2c2/) shaped to the
  confirmed schema above -- NOT against real n2c2 files. Per this project's own
  precedent (the Synthea FHIR reader was fixture-validated first and still surfaced two
  real bugs only at real scale -- see references/data-sources.md), expect at least one
  more surprise once real files are available, even for the categories mapped here.
"""
from __future__ import annotations
import glob, os, re
import xml.etree.ElementTree as ET


def _normalize(s: "str | None") -> str:
    return re.sub(r"[ _-]+", "", (s or "").upper())


# n2c2 subcategory -> harness hipaa_category. Exactly the set Stubbs & Uzuner 2015 (sec
# 8) call "HIPAA-identified categories", plus CONTACT:URL/IPADDRESS (see module
# docstring for why those two are a deliberate, documented departure from that list).
# Keys are normalized (spaces/hyphens/underscores stripped, upper-cased) since the real
# TYPE-attribute spelling isn't independently confirmed byte-for-byte.
SAFE_HARBOR_MAP = {
    "PATIENT": "name",
    "AGE": "date",              # Safe Harbor category 3 covers ages > 89
    "CITY": "geo_subdivision",
    "STREET": "geo_subdivision",
    "ZIP": "geo_subdivision",
    "ORGANIZATION": "geo_subdivision",
    "DATE": "date",
    "PHONE": "phone_fax",
    "FAX": "fax",
    "EMAIL": "email",
    "SSN": "ssn",
    "MEDICALRECORD": "mrn",
    "HEALTHPLAN": "health_plan_id",
    "ACCOUNT": "account_number",
    "LICENSE": "license_number",
    "VEHICLE": "vehicle_id",
    "DEVICE": "device_id",
    "BIOID": "biometric_id",
    "IDNUM": "other_unique_id",
    "URL": "url",                  # documented departure -- see module docstring
    "IPADDRESS": "ip_address",     # documented departure -- see module docstring
}

# Annotated in the corpus (Table 3) but NOT one of the paper's own "HIPAA-identified
# categories" -- a doctor's/username's name is not the patient's own name in the HIPAA
# sense, and a US state or country is never "smaller than a state" (Safe Harbor's own
# geo_subdivision wording). Distinct from "not yet reviewed": these were reviewed and
# excluded on purpose.
_NOT_A_SAFE_HARBOR_IDENTIFIER = {"DOCTOR", "USERNAME", "STATE", "COUNTRY", "HOSPITAL"}

# PROFESSION is an n2c2-specific addition beyond HIPAA's 18 categories (sec 1: "the 18
# categories have been expanded to include more specific identifiers"). Track 1
# measures Safe Harbor's checklist specifically, so PROFESSION has no honest
# hipaa_category home here.
_NOT_A_HIPAA_CATEGORY = {"PROFESSION"}

# Confirmed present in the annotation GUIDELINES but confirmed REMOVED from the
# released gold standard before the 2014 shared task (Stubbs & Uzuner 2015, sec 5.2/8:
# "we unmarked some of the PHI categories, i.e., ROOM, DEPARTMENT, OTHER"). A real
# released file containing one of these means the wrong corpus version is in hand, not
# a gap in this table.
_REMOVED_BEFORE_RELEASE = {"ROOM", "DEPARTMENT", "OTHER"}


class UnmappedCategoryError(Exception):
    """An n2c2 tag with no hipaa_category mapping -- for a specific, stated reason (see
    map_category). Raised, never caught-and-defaulted internally."""


def map_category(element_tag: str, type_attr: "str | None") -> str:
    """Checks BOTH the tag's own element name and its TYPE attribute against the same
    table, since the real file's convention for which one carries the subcategory
    isn't independently confirmed (see module docstring) -- either matching is
    accepted, so this doesn't bet on which convention real files actually use."""
    candidates = {_normalize(element_tag), _normalize(type_attr)}
    for c in candidates:
        if c in SAFE_HARBOR_MAP:
            return SAFE_HARBOR_MAP[c]
    if candidates & _REMOVED_BEFORE_RELEASE:
        raise UnmappedCategoryError(
            f"n2c2 category ({element_tag!r}, TYPE={type_attr!r}) was confirmed REMOVED "
            f"from the released gold standard before the 2014 shared task (Stubbs & "
            f"Uzuner 2015, sec 5.2/8) -- a real file should never contain this; check "
            f"that the corpus version in hand is the actual release.")
    if candidates & _NOT_A_HIPAA_CATEGORY:
        raise UnmappedCategoryError(
            f"n2c2 category ({element_tag!r}, TYPE={type_attr!r}) is an n2c2-specific "
            f"addition beyond HIPAA Safe Harbor's 18 categories (Stubbs & Uzuner 2015, "
            f"sec 1) -- Track 1 measures Safe Harbor coverage specifically, so this has "
            f"no honest hipaa_category home; do not fold it into other_unique_id.")
    if candidates & _NOT_A_SAFE_HARBOR_IDENTIFIER:
        raise UnmappedCategoryError(
            f"n2c2 category ({element_tag!r}, TYPE={type_attr!r}) is annotated in the "
            f"corpus but deliberately excluded from Stubbs & Uzuner 2015's own "
            f"'HIPAA-identified categories' list (sec 8) -- not a genuine Safe Harbor "
            f"identifier in this context (see module docstring for the specific reason).")
    raise UnmappedCategoryError(
        f"no reviewed hipaa_category mapping for n2c2 category ({element_tag!r}, "
        f"TYPE={type_attr!r}) -- not in SAFE_HARBOR_MAP or any of the documented "
        f"exclusions. Add one only after confirming it against a primary source or "
        f"real data (issue #126, Tier 2). Never default this silently.")


def parse_file(path: str) -> dict:
    """One n2c2-format XML file -> a manifest-schema.md RECORD dict.

    Track 1 (Safe Harbor leakage) ONLY, by deliberate scope decision (issue #126, Tier
    3) -- not "not yet built". `quasi_identifiers` is never populated: n2c2 has no
    structured demographic export, so a QI profile could only come from free-text
    extraction, which is an open-ended NLP problem with an unmeasured error rate, not a
    documentation gap the way the hipaa_category mapping was. Track 2's k-anonymity
    math is only as trustworthy as the QI values feeding it -- a silently-wrong
    extractor would corrupt every downstream risk number the same way the population-
    path bug (#162) did, but continuously across every record rather than as a single
    fixable mistake, and with no primary source available to catch it the way Stubbs &
    Uzuner 2015 caught this file's own category-mapping errors. Track 2 against n2c2 is
    out of scope for v1; revisit only if a validated extraction approach exists.
    `identity_key` is a PLACEHOLDER (the filename stem) regardless -- n2c2's real
    patient-grouping convention is unconfirmed (see module docstring).
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
            "surface_form": (type_attr or tag.tag or "").lower() or "unknown",
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
    this module's docstring and references/data-sources.md) -- identity_key is a
    placeholder, and the byte-exact TYPE-string convention is still unconfirmed against
    real data. This is ingestion infrastructure to build and test against now, not a
    ready-to-run corpus source.
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
