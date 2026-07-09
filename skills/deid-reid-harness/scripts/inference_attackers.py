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
"""
from __future__ import annotations
from typing import Optional


class InferenceAttacker:
    name = "base"

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


def get_attacker(name: str) -> InferenceAttacker:
    if name not in REGISTRY:
        raise KeyError(f"unknown attacker '{name}'; have {list(REGISTRY)}")
    return REGISTRY[name]()
