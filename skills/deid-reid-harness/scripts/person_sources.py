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
import glob, hashlib, json, os, re, sys

# The keys build_note / qi_profile / build_inference_case consume. A source that omits
# any of these would break generation, so every source is checked against this set.
PERSON_KEYS = ("first", "last", "sex", "age", "city", "zip3", "admission", "last_seen",
               "diagnosis", "rare", "mrn", "ssn", "phone", "facility")

REF_YEAR = 2026  # age = REF_YEAR - birth year, matching the corpus's 2026 admission window


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
        zip3 = (re.sub(r"\D", "", addr.get("postalCode", "")) + "000")[:3]

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
