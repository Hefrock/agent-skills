#!/usr/bin/env python3
"""
Person sources — where the synthetic people come from.

`make_person()` was designed from day one as the ONE swap point for real data. This makes
that swap a registry, exactly like the de-id defenders and the inference attackers: the
corpus generator, the injection/manifest machinery, and every scorer stay put — only the
source of a person's fields changes.

  * SyntheticSource  — wraps make_person(). The default; unchanged and byte-identical.
  * FhirSynthaSource — reads a directory of Synthea FHIR R4 patient bundles (the output of
                       Synthea / fhir-synthea-lab) and maps each Patient to the person
                       dict the generator consumes. This is how REAL demographic structure
                       — real ZIP concentration, real age/sex distributions, real
                       comorbidity — enters the harness. It is what turns Track 2's
                       uniform-ZIP3 result from an acknowledged upper bound into a
                       defensible estimate once fed real data volume.

Contract:
    source.person(i, rng) -> person dict carrying every key in PERSON_KEYS.

The synthetic source draws from `rng` so the corpus stays reproducible and byte-identical.
A file-backed source ignores `rng` and returns its i-th record (deterministic already).

Design note: this module does NOT import generate_corpus at load time (that would be
circular — generate_corpus imports this). SyntheticSource is handed make_person by the
caller, and the FHIR diagnosis mapping imports the known-diagnosis tables lazily.
"""
from __future__ import annotations
import functools, glob, hashlib, json, os, re, sys

# The keys build_note / qi_profile / build_inference_case consume. A source that omits
# any of these would break generation, so every source is checked against this set.
PERSON_KEYS = ("first", "last", "sex", "age", "city", "zip3", "admission", "last_seen",
               "diagnosis", "rare", "mrn", "ssn", "phone", "facility")

REF_YEAR = 2026  # age = REF_YEAR - birth year, matching the corpus's 2026 admission window

HERE = os.path.dirname(os.path.abspath(__file__))
MA_CITY_ZIP3_PATH = os.path.join(HERE, "ma_city_zip3.json")

# Confirmed empirically, not theorized: Synthea v3.3.0's Massachusetts module emits a
# literal "postalCode": "00000" for patients in certain towns -- the same patient record
# still carries a real city, state, and lat/long, only the postal code is degenerate. On a
# real 300-patient pilot generation, ~24% of patients hit this, and it's systematic per
# TOWN (every patient from a given affected town gets "00000", confirmed by checking
# whether any patient from that same town anywhere in the same population had a real
# code -- none did), not per-patient randomness. Left unhandled, the naive
# `postalCode[:3]` extraction below puts a real quarter of the population into a fake
# "000" zip3 bucket, which would badly corrupt Track 2's k-anonymity numbers: that bucket
# looks artificially huge (safe) while every affected patient's TRUE zip3 is silently
# undercounted.
#
# MA_CITY_ZIP3 below maps a real Massachusetts city/town name to its most-populous ZIP3
# prefix -- built from a real, government-sourced (USPS/Census/ACS) city/ZIP dataset
# (the free tier of simplemaps.com's US Zips database), not guessed or hand-typed. Only
# the individual town/ZIP3 facts extracted from that source are embedded here (463 MA
# city-name -> ZIP3 pairs, ma_city_zip3.json) -- this module does not redistribute that
# database itself, so it isn't subject to that product's own database-license terms.
#
# Some town names Synthea emits don't appear verbatim in that dataset at all -- USPS's
# preferred city name for the ZIP is a directional variant (Dartmouth -> North/South
# Dartmouth) or a village/CDP name distinct from its own town (Cochituate is a village of
# Wayland; Green Harbor-Cedar Crest is a CDP split across Marshfield/Duxbury). Each entry
# below was individually confirmed against a real source before being added, not derived
# by fuzzy string matching -- an earlier attempt at generic word-overlap matching (e.g.
# matching on the word "Center" alone) produced confidently wrong answers for exactly
# this reason, which is why every entry here is a specific, checked fact instead.
MA_CITY_ZIP3_OVERRIDES = {
    "Manchester-by-the-Sea": "019",    # USPS city name is just "Manchester" (01944)
    "Cochituate": "017",               # village of Wayland (01778)
    "Ocean Grove": "027",              # CDP in Swansea (02777)
    "Green Harbor-Cedar Crest": "020",  # CDP split Marshfield/Duxbury; Marshfield's zip (02050)
}
# Several plausible entries are deliberately NOT here because they're dead, unreachable
# data — _zip3_from_city()'s generic transforms already resolve them, confirmed directly:
# "North Westport"/"West Concord" (directional-prefix strip), "Amherst Center"/
# "Marion Center" (village-suffix strip).

_DIRECTIONAL_PREFIXES = ("North ", "South ", "East ", "West ")

# Village/CDP-name suffixes that mark a settlement inside a larger town Synthea (and the
# ZIP dataset) already knows under its plain name — e.g. "Wareham Center" is a village of
# "Wareham". Confirmed against a real 22,754-patient pilot: every one of these specific
# suffixes, stripped, resolved to an already-known town; this is a real, observed pattern
# in how Synthea names Massachusetts CDPs, not a guessed generalization of it.
_VILLAGE_SUFFIXES = (" Center", " Common", " Corner", " Neck")


def _strip_directional_prefix(name: str) -> "str | None":
    for prefix in _DIRECTIONAL_PREFIXES:
        if name.startswith(prefix):
            return name[len(prefix):]
    return None


def _add_directional_prefixes(name: str) -> list:
    return [prefix + name for prefix in _DIRECTIONAL_PREFIXES]


def _borough_to_boro(name: str) -> "str | None":
    """USPS's preferred city name for a New England "-borough" town is usually the
    abbreviated "-boro" (Middleborough -> Middleboro, Tyngsborough -> Tyngsboro,
    North Attleborough -> North Attleboro) — Synthea uses the town's full legal name,
    the ZIP dataset uses USPS's postal name. Confirmed against the real dataset for all
    three of the above before being added here, not assumed to generalize blindly."""
    if name.endswith("borough"):
        return name[: -len("borough")] + "boro"
    return None


def _strip_village_suffix(name: str) -> "str | None":
    for suffix in _VILLAGE_SUFFIXES:
        if name.endswith(suffix):
            return name[: -len(suffix)]
    return None


@functools.lru_cache(maxsize=1)
def _load_ma_city_zip3() -> dict:
    try:
        with open(MA_CITY_ZIP3_PATH, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}


def _zip3_from_city(city: str) -> "str | None":
    """Looks up a real ZIP3 for a Massachusetts city/town name against MA_CITY_ZIP3 —
    trying the name as-is, then every combination of up to two of a small, specific set
    of real naming-convention transforms (a directional-prefix add/strip, a "-borough"
    -> "-boro" postal-name swap, a village/CDP-suffix strip), then the small hand-
    verified override table above. Returns None, not a guess, when nothing matches — the
    caller decides what a genuine miss should fall back to.

    Confirmed necessary on real pilot data at two different scales, not assumed: a
    300-patient pilot needed the directional transform (e.g. "Dartmouth" only exists in
    the dataset as "North Dartmouth"/"South Dartmouth"); scaling to a real 22,754-patient
    background population surfaced two more real patterns single-transform matching
    missed — "Middleborough" (needs the borough->boro swap) and, needing BOTH transforms
    chained, "Middleborough Center" (strip " Center" -> "Middleborough", then still needs
    the borough->boro swap to become "Middleboro" before it's found). Every transform
    here is a specific, confirmed real-world naming convention, not a generic fuzzy
    match — an earlier attempt at generic word-overlap matching produced confidently
    wrong answers (see git history), which is why this stays a small, explicit,
    individually-justified set instead."""
    table = _load_ma_city_zip3()
    transforms = (_strip_directional_prefix, _add_directional_prefixes, _borough_to_boro, _strip_village_suffix)

    seen = {city}
    frontier = [city]
    for _ in range(2):  # up to two chained transforms (e.g. suffix-strip then borough->boro)
        next_frontier = []
        for name in frontier:
            if name in table:
                return table[name]
            for transform in transforms:
                result = transform(name)
                results = result if isinstance(result, list) else [result] if result else []
                for r in results:
                    if r not in seen:
                        seen.add(r)
                        next_frontier.append(r)
        frontier = next_frontier
    for name in frontier:
        if name in table:
            return table[name]

    return MA_CITY_ZIP3_OVERRIDES.get(city)


class PersonSource:
    name = "base"

    def person(self, i: int, rng) -> dict:
        raise NotImplementedError

    @staticmethod
    def _check(p: dict) -> dict:
        missing = [k for k in PERSON_KEYS if k not in p]
        if missing:
            raise ValueError(f"person source produced a record missing {missing}")
        return p


class SyntheticSource(PersonSource):
    """The original generator, behind the source interface. Byte-identical by design."""
    name = "synthetic-v0"

    def __init__(self, make_fn):
        self._make = make_fn  # generate_corpus.make_person, injected to avoid a cycle

    def person(self, i: int, rng) -> dict:
        return self._make(rng)


# --- FHIR / Synthea ------------------------------------------------------------------
# Map a real condition's text to one of the harness's known diagnoses, so Track 3's
# signature-based inference still has a target. Real open-vocabulary conditions are the
# LLM attacker's job; this keyword map keeps the deterministic pipeline whole.
_CONDITION_KEYWORDS = [
    ("fabry", "Fabry disease"),
    ("porphyria", "acute intermittent porphyria"),
    ("erdheim", "Erdheim-Chester disease"),
    ("pneumonia", "community-acquired pneumonia"),
    ("diabetes", "type 2 diabetes"),
    ("gout", "acute gout flare"),
    ("atrial fibrillation", "atrial fibrillation"),
    ("cellulitis", "cellulitis of the left leg"),
    ("migraine", "migraine"),
]


def _strip_digits(s: str) -> str:
    return re.sub(r"\d+$", "", s or "").strip() or "Unknown"


def _stable_int(seed: str, lo: int, hi: int) -> int:
    h = int(hashlib.sha256(seed.encode()).hexdigest(), 16)
    return lo + (h % (hi - lo + 1))


class FhirSynthaSource(PersonSource):
    """Reads Synthea FHIR R4 patient bundles into person dicts.

    One bundle (or one file containing a Patient) = one person. Files with no Patient
    resource (Synthea's hospital/practitioner bundles) are skipped. Fields FHIR does not
    carry are derived deterministically from the patient id so a record is always complete
    and reproducible.
    """
    name = "fhir-synthea"

    def __init__(self, fhir_dir: str):
        if not fhir_dir or not os.path.isdir(fhir_dir):
            raise SystemExit(f"--fhir-dir not found: {fhir_dir!r}")
        self.persons = []
        skipped = []
        for path in sorted(glob.glob(os.path.join(fhir_dir, "*.json"))):
            try:
                with open(path) as f:
                    doc = json.load(f)
            except (json.JSONDecodeError, UnicodeDecodeError) as e:
                skipped.append((path, str(e)))
                continue
            person = self._bundle_to_person(doc)
            if person:
                self.persons.append(self._check(person))
        for path, err in skipped:
            print(f"warning: skipping unparseable FHIR file {path!r}: {err}", file=sys.stderr)
        if not self.persons:
            raise SystemExit(f"no FHIR Patient resources found under {fhir_dir!r}")

    def __len__(self):
        return len(self.persons)

    def person(self, i: int, rng) -> dict:
        return self.persons[i]

    # --- mapping ---------------------------------------------------------------------
    def _bundle_to_person(self, doc: dict):
        resources = ([e.get("resource", {}) for e in doc.get("entry", [])]
                     if doc.get("resourceType") == "Bundle" else [doc])
        patient = next((r for r in resources if r.get("resourceType") == "Patient"), None)
        if not patient:
            return None
        conditions = [r for r in resources if r.get("resourceType") == "Condition"]
        encounters = [r for r in resources if r.get("resourceType") == "Encounter"]
        pid = patient.get("id", "unknown")

        name = (patient.get("name") or [{}])[0]
        first = _strip_digits((name.get("given") or ["Unknown"])[0])
        last = _strip_digits(name.get("family", "Unknown"))
        sex = "F" if patient.get("gender") == "female" else "M"
        age = self._age(patient.get("birthDate"), pid)
        addr = (patient.get("address") or [{}])[0]
        city = addr.get("city") or "Unknown"
        raw_zip3 = re.sub(r"\D", "", addr.get("postalCode", ""))[:3]
        if raw_zip3 and raw_zip3 != "000":
            zip3 = raw_zip3
        else:
            # postalCode was missing or Synthea's own known "00000" placeholder (see
            # MA_CITY_ZIP3's docstring) -- recover a real zip3 from the city instead of
            # silently lumping this patient into a fake "000" bucket.
            zip3 = _zip3_from_city(city)
            if zip3 is None:
                print(f"warning: no ZIP3 known for Massachusetts city {city!r} (patient {pid}) "
                      f"-- falling back to \"000\". Add it to MA_CITY_ZIP3_OVERRIDES in "
                      f"person_sources.py once confirmed against a real source.", file=sys.stderr)
                zip3 = "000"

        ssn = self._identifier(patient, "us-ssn", "SS") or \
            f"{_stable_int(pid+'ssn', 100, 899)}-{_stable_int(pid+'s2',10,99)}-{_stable_int(pid+'s3',1000,9999)}"
        mrn = self._identifier(patient, None, "MR") or str(_stable_int(pid + "mrn", 1000000, 9999999))
        phone = self._telecom(patient, "phone") or f"555-{_stable_int(pid+'ph',1000,9999)}"

        diagnosis, rare = self._diagnosis(conditions, pid)
        admission, last_seen = self._encounter_dates(encounters, pid)
        facility = self._facility(encounters) or f"fac-{_stable_int(pid+'fac',1,12):02d}"

        return {
            "first": first, "last": last, "sex": sex, "age": age, "city": city,
            "zip3": zip3, "admission": admission, "last_seen": last_seen,
            "diagnosis": diagnosis, "rare": rare, "mrn": mrn, "ssn": ssn,
            "phone": phone, "facility": facility, "email": None,
        }

    @staticmethod
    def _age(birth_date, pid):
        try:
            return max(0, REF_YEAR - int(str(birth_date)[:4]))
        except (TypeError, ValueError):
            return _stable_int(pid + "age", 19, 92)

    @staticmethod
    def _identifier(patient, system_suffix, type_code):
        for ident in patient.get("identifier", []):
            sys_ok = system_suffix and str(ident.get("system", "")).endswith(system_suffix)
            type_ok = any(c.get("code") == type_code
                          for c in (ident.get("type", {}).get("coding") or []))
            if (sys_ok or type_ok) and ident.get("value"):
                return ident["value"]
        return None

    @staticmethod
    def _telecom(patient, system):
        for t in patient.get("telecom", []):
            if t.get("system") == system and t.get("value"):
                return t["value"]
        return None

    @staticmethod
    def _diagnosis(conditions, pid):
        from generate_corpus import DIAGNOSES, RARE  # lazy: avoid import cycle
        texts = []
        for c in conditions:
            cc = c.get("code", {})
            texts.append(cc.get("text", ""))
            texts += [co.get("display", "") for co in (cc.get("coding") or [])]
        blob = " ".join(texts).lower()
        for kw, dx in _CONDITION_KEYWORDS:
            if kw in blob:
                return dx, dx in RARE
        # No known condition matched: pick a common diagnosis deterministically so Track 3
        # still has a signature target. (Documented simplification — see reference doc.)
        return DIAGNOSES[_stable_int(pid + "dx", 0, len(DIAGNOSES) - 1)], False

    @staticmethod
    def _encounter_dates(encounters, pid):
        starts = sorted(e.get("period", {}).get("start", "")[:10]
                        for e in encounters if e.get("period", {}).get("start"))
        starts = [s for s in starts if s]
        if len(starts) >= 2:
            return starts[0], starts[-1]
        if len(starts) == 1:
            return starts[0], starts[0]
        y = REF_YEAR
        return (f"{y}-{_stable_int(pid+'ad',1,12):02d}-{_stable_int(pid+'ad2',1,28):02d}",
                f"{y-1}-{_stable_int(pid+'ls',1,12):02d}-{_stable_int(pid+'ls2',1,28):02d}")

    @staticmethod
    def _facility(encounters):
        for e in encounters:
            disp = e.get("serviceProvider", {}).get("display")
            if disp:
                return disp
        return None


def get_source(name: str, make_fn=None, fhir_dir=None) -> PersonSource:
    if name in ("synthetic", "synthetic-v0"):
        return SyntheticSource(make_fn)
    if name in ("fhir", "fhir-synthea"):
        return FhirSynthaSource(fhir_dir)
    raise KeyError(f"unknown person source '{name}'; have synthetic-v0, fhir-synthea")
