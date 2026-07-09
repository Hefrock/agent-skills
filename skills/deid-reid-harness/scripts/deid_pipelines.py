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


# Registry so the orchestrator (and later, config) can select a defender by name.
REGISTRY: dict = {
    RegexBaseline.name: RegexBaseline,
}

def get_pipeline(name: str) -> DeidPipeline:
    if name not in REGISTRY:
        raise KeyError(f"unknown pipeline '{name}'; have {list(REGISTRY)}")
    return REGISTRY[name]()
