#!/usr/bin/env python3
"""
Corpus generator for the de-id/re-id harness — Track 1 slice.

Produces a synthetic clinical-note corpus with a ground-truth manifest whose
identifier spans are EXACT BY CONSTRUCTION: notes are assembled from segments, and
each identifier's char offset is recorded at placement time, so we never rely on a
tagger to recover spans. A self-test asserts note_text[start:end] == text for every
span before anything is written.

This v0 uses a self-contained synthetic person generator so it runs with zero external
setup. It is deliberately structured so a real Synthea driver can replace
`make_person()` without touching the injection/manifest machinery — that is the only
function that would change to swap in fhir-synthea-lab output.

Usage:
    python generate_corpus.py --n 50 --seed 20260101 --out corpus.json
    # also emit a background population for Track 2 (Expert Determination):
    python generate_corpus.py --n 50 --seed 20260101 --population 100000 --out corpus.json
    # also attach diagnosis-free inference vignettes for Track 3:
    python generate_corpus.py --n 50 --seed 20260101 --inference --out corpus.json
    # also mark clinical spans for the utility axis / privacy-utility frontier:
    python generate_corpus.py --n 50 --seed 20260101 --utility --out corpus.json
"""
from __future__ import annotations
import argparse, json, os, random, string
from dataclasses import dataclass, field
from typing import Callable
from qi_model import age_band

# --- Segment-based note builder -------------------------------------------------
# A note is a list of Segment objects. Plain segments are literal text; identifier
# segments additionally carry the metadata needed for the manifest. Offsets are
# computed by walking the assembled list, so they are always correct.

@dataclass
class Seg:
    text: str
    is_identifier: bool = False
    hipaa_category: str = ""
    surface_form: str = ""
    context: str = ""
    is_clinical: bool = False
    clinical_category: str = ""

@dataclass
class Note:
    segments: list = field(default_factory=list)

    def add(self, text): self.segments.append(Seg(text))

    def add_id(self, text, category, surface_form, context):
        self.segments.append(Seg(text, True, category, surface_form, context))

    def add_clinical(self, text, category):
        """A clinically-necessary, non-identifying span that a good scrubber must LEAVE
        intact. This is the utility mirror of add_id: identifiers must not survive,
        clinical content must. Adds metadata only — the text is placed exactly as add()."""
        self.segments.append(Seg(text, is_clinical=True, clinical_category=category))

    def render(self):
        """Return (note_text, spans, clinical_spans). Offsets are exact by construction."""
        parts, spans, clinical, cursor = [], [], [], 0
        for i, seg in enumerate(self.segments):
            start = cursor
            parts.append(seg.text)
            cursor += len(seg.text)
            if seg.is_identifier:
                spans.append({
                    "start": start, "end": cursor, "text": seg.text,
                    "hipaa_category": seg.hipaa_category,
                    "surface_form": seg.surface_form, "context": seg.context,
                })
            if seg.is_clinical:
                clinical.append({
                    "start": start, "end": cursor, "text": seg.text,
                    "clinical_category": seg.clinical_category,
                })
        return "".join(parts), spans, clinical


# --- Synthetic person (swap this for a Synthea driver) --------------------------

FIRST = ["Maria", "James", "Aisha", "Robert", "Chen", "Fatima", "David", "Elena",
         "Kwame", "Sarah", "Diego", "Priya", "Thomas", "Yuki", "Omar", "Grace"]
LAST  = ["Alvarez", "Okafor", "Nguyen", "Patel", "Smith", "Rossi", "Haddad",
         "Kim", "Johnson", "Silva", "Muller", "Tanaka", "Abebe", "Cohen"]
CITIES = ["Springfield", "Riverton", "Fairview", "Lakeside", "Georgetown"]
DIAGNOSES = ["community-acquired pneumonia", "type 2 diabetes", "acute gout flare",
             "atrial fibrillation", "cellulitis of the left leg", "migraine"]
RARE = ["Fabry disease", "acute intermittent porphyria", "Erdheim-Chester disease"]

# --- Clinical signatures for the inference track (Track 3) -----------------------
# Each diagnosis maps to an `anchor` (a near-pathognomonic finding) and `supporting`
# features (some deliberately shared across diagnoses — e.g. "fever and chills"). The
# inference note (see build_inference_case) renders a diagnosis-FREE vignette from
# these, so the target diagnosis is DERIVABLE from context but never stated. That is
# the whole point of Track 3: inference, not leakage. None of these phrases contains
# its own diagnosis name — enforced by inference_self_test().
DIAGNOSIS_SIGNATURES = {
    "community-acquired pneumonia": {
        "anchor": "a lobar infiltrate on chest radiograph",
        "supporting": ["a productive cough", "fever and chills",
                       "focal crackles on auscultation", "pleuritic chest pain"]},
    "type 2 diabetes": {
        "anchor": "an HbA1c of 8.9% with a fasting glucose over 200",
        "supporting": ["polyuria and polydipsia", "unintentional weight loss",
                       "blurred vision", "fatigue"]},
    "acute gout flare": {
        "anchor": "monosodium urate crystals on joint aspiration",
        "supporting": ["acute pain and swelling of the first toe", "onset overnight",
                       "a markedly elevated serum urate", "a single warm red joint"]},
    "atrial fibrillation": {
        "anchor": "an irregularly irregular rhythm with absent P waves on ECG",
        "supporting": ["palpitations", "a rapid ventricular rate", "lightheadedness",
                       "exertional breathlessness"]},
    "cellulitis of the left leg": {
        "anchor": "a sharply demarcated area of spreading erythema over the lower leg",
        "supporting": ["warmth and tenderness", "fever and chills", "swelling of the limb",
                       "a skin portal of entry"]},
    "migraine": {
        "anchor": "a recurrent unilateral throbbing headache with photophobia",
        "supporting": ["nausea", "relief with rest in a dark room",
                       "a visual aura preceding the pain", "phonophobia"]},
    "Fabry disease": {
        "anchor": "reduced alpha-galactosidase A activity on enzyme assay",
        "supporting": ["acroparesthesias of the hands and feet", "clusters of angiokeratomas",
                       "corneal verticillata on slit-lamp exam", "hypohidrosis"]},
    "acute intermittent porphyria": {
        "anchor": "elevated urinary porphobilinogen with urine that darkens on standing",
        "supporting": ["severe diffuse abdominal pain without peritoneal signs",
                       "hyponatremia", "tachycardia and hypertension", "a peripheral neuropathy"]},
    "Erdheim-Chester disease": {
        "anchor": "bilateral symmetric osteosclerosis of the long bones with a BRAF V600E mutation",
        "supporting": ["xanthomatous infiltration", "retroperitoneal fibrosis",
                       "diabetes insipidus", "periaortic sheathing"]},
}

def make_person(rng: random.Random) -> dict:
    rare = rng.random() < 0.15
    return {
        "first": rng.choice(FIRST), "last": rng.choice(LAST),
        "sex": rng.choice(["M", "F"]),
        "age": rng.randint(19, 92),
        "city": rng.choice(CITIES),
        "zip3": f"{rng.randint(1,999):03d}",  # leading zeros are real (e.g. 021)
        "admission": f"2026-{rng.randint(1,12):02d}-{rng.randint(1,28):02d}",
        "last_seen": f"2025-{rng.randint(1,12):02d}-{rng.randint(1,28):02d}",
        "diagnosis": rng.choice(RARE) if rare else rng.choice(DIAGNOSES),
        "rare": rare,
        "mrn": f"{rng.randint(1000000,9999999)}",
        "ssn": f"{rng.randint(100,899)}-{rng.randint(10,99)}-{rng.randint(1000,9999)}",
        "phone": f"555-{rng.randint(1000,9999)}",
        "email": None,  # filled below
        "facility": f"fac-{rng.randint(1,12):02d}",
    }


# --- Surface-form renderers -----------------------------------------------------
# The same logical identifier is rendered many realistic ways. This is what makes the
# corpus a genuine test: coverage = category × surface_form × context.

MONTHS = ["January","February","March","April","May","June","July","August",
          "September","October","November","December"]

def _ordinal(n: int) -> str:
    if 10 <= n % 100 <= 20:
        suf = "th"
    else:
        suf = {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suf}"

def date_forms(iso: str, rng):
    y, m, d = iso.split("-")
    m_i = int(m)
    return rng.choice([
        (iso, "iso"),
        (f"{int(m)}/{int(d)}/{y}", "slashed"),
        (f"{MONTHS[m_i-1]} {int(d)}, {y}", "spelled"),
        (f"the {_ordinal(int(d))} of {MONTHS[m_i-1]}", "narrative"),
    ])

def phone_forms(p: str, rng):
    digits = p.replace("-", "")
    return rng.choice([
        (p, "dashed"),
        (f"{digits[:3]} {digits[3:]}", "spaced"),
        (f"{digits[:3]}.{digits[3:]}", "dotted"),
    ])

def name_forms(first, last, rng):
    return rng.choice([
        (f"{first} {last}", "full"),
        (f"{last}, {first}", "last-first"),
        (f"{first[0]}. {last}", "initial"),
    ])


# --- Note templater: places identifiers with tracked context --------------------

def build_note(person: dict, rng: random.Random) -> Note:
    n = Note()
    # Header block (structured context) --------------------------------
    name_t, name_sf = name_forms(person["first"], person["last"], rng)
    n.add("PATIENT: "); n.add_id(name_t, "name", name_sf, "header")
    n.add("    MRN: "); n.add_id(person["mrn"], "mrn", "raw", "header")
    n.add("\nDOB/SSN on file: "); n.add_id(person["ssn"], "ssn", "raw", "header")
    adm_t, adm_sf = date_forms(person["admission"], rng)
    n.add("\nAdmitted: "); n.add_id(adm_t, "date", adm_sf, "header")
    n.add("    Facility: ")
    n.add_id(person["facility"], "other_unique_id", "facility_code", "header")
    n.add("\n\n")

    # Narrative body (the hard context — identifiers hide in prose) -----
    # Ages > 89 are themselves Safe Harbor identifiers (category 3); younger ages are
    # clinical content that must SURVIVE scrubbing. Sex and the diagnosis are likewise
    # clinically necessary and non-identifying — marked as utility spans (add_clinical)
    # so over-redaction that clobbers them is measurable. Text placement is unchanged.
    n.add("HPI: ")
    if person["age"] > 89:
        n.add_id(str(person["age"]), "date", "age_over_89", "narrative")
    else:
        n.add_clinical(str(person["age"]), "age")
    n.add("-year-old ")
    n.add_clinical("woman" if person["sex"] == "F" else "man", "sex")
    n.add(" from ")
    n.add_id(person["city"], "geo_subdivision", "city", "narrative")
    n.add(" (ZIP "); n.add_id(person["zip3"], "geo_subdivision", "zip3", "narrative")
    n.add(") presenting with ")
    n.add_clinical(person["diagnosis"], "diagnosis")
    n.add(". ")
    # a date buried mid-sentence, distinct from the admission date
    fu_t, fu_sf = date_forms(person["last_seen"], rng)
    n.add("Patient was last seen on "); n.add_id(fu_t, "date", fu_sf, "narrative")
    n.add(". ")
    # phone in narrative
    ph_t, ph_sf = phone_forms(person["phone"], rng)
    n.add("Callback number given as "); n.add_id(ph_t, "phone_fax", ph_sf, "narrative")
    n.add(".\n\n")

    # Signature block (another structured context) ---------------------
    dr_first, dr_last = rng.choice(FIRST), rng.choice(LAST)
    email = f"{dr_first.lower()}.{dr_last.lower()}@exampleclinic.org"
    n.add("Documented by Dr. ")
    dr_t, dr_sf = name_forms(dr_first, dr_last, rng)
    n.add_id(dr_t, "name", dr_sf, "signature")
    n.add("  Contact: "); n.add_id(email, "email", "raw", "signature")
    n.add("\n")
    return n


# --- Driver --------------------------------------------------------------------

def build_inference_case(person: dict, rng: random.Random) -> dict:
    """A diagnosis-FREE clinical vignette + the withheld diagnosis as ground truth.

    The vignette renders the diagnosis's signature (anchor + a random subset of
    supporting features) plus coarse demographics that survive Safe Harbor (age band,
    sex). The diagnosis is DERIVABLE from this context but never named — so Track 3
    tests inference, not the surface leakage Track 1 measures. The anchor is dropped
    ~30% of the time so difficulty varies: notes reduced to shared, non-specific
    features are genuinely ambiguous, which is what makes the attacker's score < 100%.
    """
    sig = DIAGNOSIS_SIGNATURES[person["diagnosis"]]
    phrases = []
    if rng.random() < 0.70:
        phrases.append(sig["anchor"])
    k = rng.randint(1, 3)
    phrases += rng.sample(sig["supporting"], min(k, len(sig["supporting"])))
    rng.shuffle(phrases)
    sex_word = "woman" if person["sex"] == "F" else "man"
    note = (f"De-identified vignette: a {age_band(person['age'])} {sex_word} presents "
            f"with {'; '.join(phrases)}. Working diagnosis withheld for inference testing.")
    return {
        "target_attribute": "diagnosis",
        "target_value": person["diagnosis"],
        "is_rare": person["rare"],
        "note": note,
    }


def generate(n_records: int, seed: int, with_inference: bool = False,
             with_utility: bool = False) -> dict:
    rng = random.Random(seed)
    # Isolated RNG for the inference-note layer (anchor dropout, feature subset), so
    # enabling inference never perturbs the main stream — the Track 1/2 corpus is
    # byte-identical with or without --inference.
    inf_rng = random.Random(seed + 2) if with_inference else None
    records = []
    for i in range(n_records):
        person = make_person(rng)
        note = build_note(person, rng)
        text, spans, clinical = note.render()
        rid = f"rec-{i:06d}"
        for j, s in enumerate(spans):
            s["span_id"] = f"{rid}:s{j:02d}"
        rec = {
            "record_id": rid,
            "identity_key": f"person-{i:06d}",
            "note_text": text,
            "identifiers": spans,
            "quasi_identifiers": {
                "age": person["age"], "sex": person["sex"], "zip3": person["zip3"],
                "admission_date": person["admission"], "rare_diagnosis": person["rare"],
                "facility_id": person["facility"],
            },
        }
        if with_utility:
            # Clinical spans are computed from the note as generated (no new text, no
            # RNG), so attaching them leaves note_text and identifiers byte-identical.
            for j, c in enumerate(clinical):
                c["span_id"] = f"{rid}:c{j:02d}"
            rec["clinical_spans"] = clinical
        if with_inference:
            rec["inference_case"] = build_inference_case(person, inf_rng)
        records.append(rec)
    return {"manifest_version": "1", "seed": seed, "generator": "synthetic-v0",
            "population_ref": None, "records": records}


def qi_profile(person: dict) -> dict:
    """The quasi-identifier view of a person — the only fields Track 2 consumes.
    Kept identical in shape to a record's `quasi_identifiers` block so the sample
    and the background population are described the same way."""
    return {
        "age": person["age"], "sex": person["sex"], "zip3": person["zip3"],
        "admission_date": person["admission"], "rare_diagnosis": person["rare"],
        "facility_id": person["facility"],
    }


def generate_population(n_people: int, seed: int) -> list:
    """Background population for the Expert Determination (Track 2) denominator.

    Drawn from the SAME make_person distribution as the corpus, so sample and
    population are distributionally identical — the precondition for a meaningful
    linkage risk. Only QI profiles are kept (no notes, no direct identifiers): the
    population is a denominator, not a corpus. Swap make_person for a Synthea driver
    here exactly as for the corpus, and the two stay consistent by construction.
    """
    rng = random.Random(seed)
    return [qi_profile(make_person(rng)) for _ in range(n_people)]


def self_test(corpus: dict) -> None:
    """Enforce the core invariant: every span's offsets slice out its own text.
    Applies to identifier spans and, when present, clinical (utility) spans — both are
    exact by construction, so both are checked here rather than trusted."""
    bad = 0
    for rec in corpus["records"]:
        t = rec["note_text"]
        for s in rec["identifiers"] + rec.get("clinical_spans", []):
            if t[s["start"]:s["end"]] != s["text"]:
                bad += 1
    if bad:
        raise AssertionError(f"{bad} spans failed the offset invariant — corpus invalid")


def inference_self_test(corpus: dict) -> None:
    """Enforce the inference invariant: the withheld diagnosis is NEVER stated in the
    vignette the attacker sees. If it were, Track 3 would measure leakage, not
    inference — so this is checked at generation time, not trusted."""
    leaked = 0
    for rec in corpus["records"]:
        case = rec.get("inference_case")
        if case and case["target_value"].lower() in case["note"].lower():
            leaked += 1
    if leaked:
        raise AssertionError(
            f"{leaked} inference notes state their own target diagnosis — not inference")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=50)
    ap.add_argument("--seed", type=int, default=20260101)
    ap.add_argument("--out", default="corpus.json")
    ap.add_argument("--population", type=int, default=0,
                    help="if >0, also emit a background population of this size for Track 2")
    ap.add_argument("--population-out", default="population.jsonl")
    ap.add_argument("--inference", action="store_true",
                    help="attach a diagnosis-free inference vignette per record for Track 3")
    ap.add_argument("--utility", action="store_true",
                    help="mark clinical spans (diagnosis, age, sex) that must survive scrubbing")
    args = ap.parse_args()
    # Corpus is generated first and independently, so adding --population, --inference,
    # or --utility never perturbs the main RNG stream — the Track 1 corpus is byte-
    # identical with any combination of flags (inference uses a disjoint derived RNG;
    # clinical spans are metadata over the note as generated).
    corpus = generate(args.n, args.seed, with_inference=args.inference,
                      with_utility=args.utility)
    self_test(corpus)
    if args.inference:
        inference_self_test(corpus)

    if args.population > 0:
        # Derived-but-independent seed: reproducible from --seed, disjoint from the
        # corpus stream. The sample is NOT drawn from this file; the scorer folds the
        # sample into the population counts so every record's class size is >= 1.
        pop = generate_population(args.population, args.seed + 1)
        with open(args.population_out, "w") as pf:
            for prof in pop:
                pf.write(json.dumps(prof) + "\n")
        # Store a bare reference; the scorer resolves it relative to the corpus file.
        corpus["population_ref"] = os.path.basename(args.population_out)

    with open(args.out, "w") as f:
        json.dump(corpus, f, indent=2)
    n_ids = sum(len(r["identifiers"]) for r in corpus["records"])
    print(f"Wrote {len(corpus['records'])} records, {n_ids} identifier spans -> {args.out}")
    print("Self-test passed: every span slices out its own text.")
    if args.population > 0:
        print(f"Wrote background population of {args.population} QI profiles "
              f"-> {args.population_out}  (population_ref set)")
    if args.inference:
        print(f"Attached {len(corpus['records'])} inference vignettes "
              f"(diagnosis withheld) for Track 3.")
    if args.utility:
        n_cs = sum(len(r.get("clinical_spans", [])) for r in corpus["records"])
        print(f"Marked {n_cs} clinical spans (must survive scrubbing) for the utility axis.")


if __name__ == "__main__":
    main()
