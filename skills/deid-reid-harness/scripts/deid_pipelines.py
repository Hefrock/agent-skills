#!/usr/bin/env python3
"""
De-identification pipelines — the DEFENDER under test.

The pipeline is a SWAPPABLE component, never hardcoded: the same eval must run against
a regex baseline, a rule-based scrubber, an LLM scrubber, and a hybrid, so they can be
compared on one privacy-utility frontier. That is the whole deliverable, so the
interface is intentionally minimal.

Contract:
    scrub(note_text) -> (scrubbed_text, redacted_spans_or_None)

A pipeline MAY return the char spans it redacted (enables exact, offset-aligned
scoring). If it can only return text (a true black box, e.g. an external API), it
returns None for spans and the scorer falls back to surface-string presence checks.
The regex baseline returns spans, so v0 scoring is exact.

The regex baseline is deliberately INCOMPLETE — it misses spelled-out dates,
initials-form names, and more. That is the point: a weak baseline gives you a real,
low frontier point to improve against, and proves the harness can detect leakage.
"""
from __future__ import annotations
import re
from typing import Callable, Optional

REDACTION = "[REDACTED]"

class DeidPipeline:
    name = "base"
    def scrub(self, text: str):
        raise NotImplementedError


class RegexBaseline(DeidPipeline):
    """Weak, transparent baseline. Catches the easy surface forms and little else."""
    name = "regex-baseline-v0"

    # Deliberately narrow patterns — real notes defeat these constantly.
    PATTERNS = [
        ("ssn",         re.compile(r"\b\d{3}-\d{2}-\d{4}\b")),
        ("phone_fax",   re.compile(r"\b\d{3}-\d{4}\b")),                 # misses 555 1234 / 555.1234
        ("email",       re.compile(r"\b[\w.]+@[\w.]+\.\w+\b")),
        ("mrn",         re.compile(r"\bMRN:\s*\d{7}\b")),               # only when labeled
        ("date",        re.compile(r"\b\d{4}-\d{2}-\d{2}\b")),          # ISO only; misses spelled/slashed
        ("geo",         re.compile(r"ZIP\s+\d{3}")),
    ]

    def scrub(self, text: str):
        redacted = []
        # Collect all match spans first, then apply right-to-left so offsets stay valid.
        hits = []
        for _cat, pat in self.PATTERNS:
            for m in pat.finditer(text):
                hits.append((m.start(), m.end()))
        hits.sort()
        # Merge/skip overlaps, record spans on the ORIGINAL text.
        merged = []
        for s, e in hits:
            if merged and s < merged[-1][1]:
                continue
            merged.append((s, e))
        redacted = [{"start": s, "end": e, "text": text[s:e]} for s, e in merged]
        # Build scrubbed text by replacing right-to-left.
        out = text
        for s, e in sorted(merged, reverse=True):
            out = out[:s] + REDACTION + out[e:]
        return out, redacted


class OverRedactor(DeidPipeline):
    """The opposite failure mode: redact aggressively and ask questions later.

    Catches everything the regex baseline does, PLUS every Capitalized token and every
    token containing a digit. That sweeps up the names and spelled-out dates the baseline
    misses (privacy goes UP) — but it also clobbers clinically necessary, non-identifying
    content: a patient's age (a number) and capitalized diagnoses like "Fabry" (utility
    goes DOWN). It exists to put a SECOND point on the privacy-utility frontier: a
    scrubber is only meaningful relative to what it destroys, and this one trades utility
    for privacy in exactly the direction the baseline does not.
    """
    name = "over-redact-v0"
    EXTRA = re.compile(r"\b(?:[A-Z][A-Za-z'.-]+|\S*\d\S*)\b")
    # A gap made only of separators is bridged, so a multi-token identifier (e.g. a name
    # "Sarah Alvarez", or "Alvarez, Sarah") collapses into ONE redacted span rather than
    # two — otherwise Track 1's single-span coverage rule would score a fully-redacted
    # name as leaked. The gap must contain no letters, so lowercase clinical content
    # (diagnosis, sex) between capitalized tokens is never swallowed.
    SEP_GAP = re.compile(r"[\s,.;:/()\-]*")

    def scrub(self, text: str):
        hits = []
        for _cat, pat in RegexBaseline.PATTERNS:
            for m in pat.finditer(text):
                hits.append((m.start(), m.end()))
        for m in self.EXTRA.finditer(text):
            hits.append((m.start(), m.end()))
        hits.sort()
        merged = []
        for s, e in hits:
            if merged:
                ps, pe = merged[-1]
                if s <= pe or self.SEP_GAP.fullmatch(text[pe:s]):  # overlap or separators-only gap
                    merged[-1] = (ps, max(pe, e))
                    continue
            merged.append((s, e))
        redacted = [{"start": s, "end": e, "text": text[s:e]} for s, e in merged]
        out = text
        for s, e in sorted(merged, reverse=True):
            out = out[:s] + REDACTION + out[e:]
        return out, redacted


# Registry so the orchestrator (and later, config) can select a defender by name.
REGISTRY: dict = {
    RegexBaseline.name: RegexBaseline,
    OverRedactor.name: OverRedactor,
}

def get_pipeline(name: str) -> DeidPipeline:
    if name not in REGISTRY:
        raise KeyError(f"unknown pipeline '{name}'; have {list(REGISTRY)}")
    return REGISTRY[name]()
