#!/usr/bin/env python3
"""
Inference attackers — the ATTACKER under test in Track 3 (free-text inference).

Track 3 is the AI-era threat: a model derives a withheld attribute (here, the
diagnosis) from clinical context that never states it. The attacker is a SWAPPABLE
component with the same registry pattern as the de-id defenders, so the same eval runs
against a deterministic baseline today and a real LLM attacker tomorrow — no other code
changes.

Contract:
    infer(note_text) -> {"guess": str|None, "confidence": float, "rationale": str}

    guess       — the diagnosis the attacker believes the vignette describes, or None
                  if it declines (abstains) rather than force a guess.
    confidence  — the attacker's own 0..1 confidence. It is NOT trusted; the scorer
                  CALIBRATES it against actual correctness (does a claimed 0.9 mean 90%
                  right?). That calibration is the reason confidence is required.

The bundled SignatureMatchAttacker is deliberately simple and MODEL-INDEPENDENT: it is
the load-bearing baseline that proves the loop closes and demonstrates the threat with
zero API access, exactly like the regex defender in Track 1 and the statistical attacker
in Track 2. A real LLM attacker is a strictly stronger swap-in — register it here and
run it through agent-eval (see references/inference-threat.md). Per the harness's
cardinal rule, an LLM attacker must not share a base model with any LLM defender, nor
with the judge that grades it.

Compliance gate (issue #126, Tier 1): calling any third-party LLM API on DUA-governed
clinical text is, per how academic clinical-NLP DUAs are standardly written, almost
certainly a prohibited third-party disclosure — n2c2's and MIMIC's own DUA text (see
references/data-sources.md) both name this restriction explicitly. Before this gate
existed, that restriction lived only as a sentence in issue #29 — nothing in the code
enforced it. Every attacker registered here now declares `calls_external_api` (True for
anything that makes a network call to grade real data, False for the bundled
model-independent baseline); get_attacker() refuses to hand back an attacker whose
class sets it True unless the caller explicitly passes acknowledge_external_api=True —
loud, not a comment someone has to remember, the same "unsafe-by-default requires
explicit opt-in" pattern privacy-linter's --strip-metadata already uses. This is a
technical gate against an accidental real-data run, not a substitute for the actual
human compliance review Tier 1 also requires before any DUA-governed corpus is used at
all -- passing the flag says "I've confirmed this specific run's data and DUA permit
it," not "the tool has decided it's fine."
"""
from __future__ import annotations
from typing import Optional


class InferenceAttacker:
    name = "base"
    # True on any subclass that calls a third-party API to grade real data -- see the
    # compliance-gate note above. Every attacker MUST set this explicitly; there is no
    # safe default to assume for a subclass this base class has never seen.
    calls_external_api = False

    def infer(self, note_text: str) -> dict:
        raise NotImplementedError


class SignatureMatchAttacker(InferenceAttacker):
    """Knowledge-only baseline: matches surviving clinical features to a diagnosis.

    Its clinical knowledge base is authored INDEPENDENTLY of the corpus generator's
    signatures (the attacker never sees the answer key). The keywords are the
    discriminating terms a clinician would key on; they deliberately do NOT cover every
    shared, non-specific feature, so a vignette reduced to ambiguous findings yields no
    match and the attacker abstains — which is what makes its score < 100%.
    """
    name = "signature-match-v0"

    DIAGNOSIS_KEYWORDS = {
        "community-acquired pneumonia": ["infiltrate", "productive cough", "crackles", "pleuritic"],
        "type 2 diabetes": ["hba1c", "fasting glucose", "polyuria", "polydipsia"],
        "acute gout flare": ["monosodium urate", "urate", "first toe", "warm red joint"],
        "atrial fibrillation": ["irregularly irregular", "absent p waves", "palpitations", "ventricular rate"],
        "cellulitis of the left leg": ["spreading erythema", "lower leg", "portal of entry", "warmth and tenderness"],
        "migraine": ["throbbing headache", "photophobia", "aura", "phonophobia"],
        "Fabry disease": ["alpha-galactosidase", "angiokeratoma", "acroparesthesia", "verticillata"],
        "acute intermittent porphyria": ["porphobilinogen", "darkens on standing", "without peritoneal signs", "hyponatremia"],
        "Erdheim-Chester disease": ["osteosclerosis", "braf", "xanthomatous", "retroperitoneal fibrosis"],
    }

    def infer(self, note_text: str) -> dict:
        low = note_text.lower()
        # Score each candidate by how many of its discriminating keywords survive.
        scored = []
        for dx, kws in self.DIAGNOSIS_KEYWORDS.items():
            hits = [kw for kw in kws if kw in low]
            scored.append((len(hits), dx, kws, hits))
        best_hits, dx, kws, hits = max(scored, key=lambda t: t[0])
        if best_hits == 0:
            return {"guess": None, "confidence": 0.0,
                    "rationale": "no discriminating features survived; abstained"}
        confidence = round(best_hits / len(kws), 3)
        return {"guess": dx, "confidence": confidence,
                "rationale": f"matched {best_hits}/{len(kws)} features: {', '.join(hits)}"}


REGISTRY: dict = {
    SignatureMatchAttacker.name: SignatureMatchAttacker,
}


def get_attacker(name: str, acknowledge_external_api: bool = False, registry: dict = None) -> InferenceAttacker:
    """`registry` defaults to the real REGISTRY above; overridable so a test can inject
    a fake calls_external_api=True attacker without registering one for real (there is
    no live-LLM attacker built yet — see the module docstring's compliance-gate note —
    so this is the only way to test the gate today)."""
    reg = REGISTRY if registry is None else registry
    if name not in reg:
        raise KeyError(f"unknown attacker '{name}'; have {list(reg)}")
    cls = reg[name]
    if cls.calls_external_api and not acknowledge_external_api:
        raise SystemExit(
            f"attacker {name!r} calls a third-party API (calls_external_api=True) -- refusing "
            f"to run it without explicit acknowledgment. Calling any external LLM API on "
            f"DUA-governed clinical text is, per how academic clinical-NLP DUAs are standardly "
            f"written, almost certainly a prohibited third-party disclosure (see issue #126, "
            f"references/data-sources.md). Pass --acknowledge-external-api-risk (score_inference.py) "
            f"only after confirming, for the SPECIFIC data and DUA this run uses, that it's "
            f"actually permitted -- this flag records that you checked, it doesn't check for you."
        )
    return cls()
