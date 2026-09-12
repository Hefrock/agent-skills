#!/usr/bin/env python3
"""Tests for qa_gate.py.

Two suites, same split as the module itself:
  - QaGateStructuralChecks: pure logic over hand-built script_gen.py-shaped
    dicts, no network, no server.
  - ClaimsStillPinnedLive: drives the REAL compiled evidence-pinning-mcp
    server (same pattern as test_evidence.py/test_evidence_pinning_client.py)
    to prove the flagged-claim-fails-the-gate scenario against the actual
    server behavior, not a mocked one. Skipped (not failed) if the server
    isn't built.

Run: python test_qa_gate.py"""

import importlib.util
import os
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SERVER_PATH = os.path.join(HERE, "..", "..", "..", "mcp", "evidence-pinning", "dist", "index.js")


def load(name):
    # See test_evidence.py's load() docstring for why sys.modules
    # registration before exec_module matters here: qa_gate.py does its
    # own `from evidence_pinning_client import EvidencePinningError`
    # internally, and the live suite below needs that to be the SAME
    # EvidencePinningError class this test's own client instance raises.
    spec = importlib.util.spec_from_file_location(name, os.path.join(HERE, f"{name}.py"))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


evidence_pinning_client = load("evidence_pinning_client")
qa_gate = load("qa_gate")


def make_segment(segment_type, text="Some text.", canonical_id=None, claim_id=None, source_id=None):
    return {"segment_type": segment_type, "text": text, "canonical_id": canonical_id, "claim_id": claim_id, "source_id": source_id}


def make_story_segment(segment_type="top_three_item", canonical_id="c1", claim_id="claim-1", source_id="src-1"):
    return make_segment(segment_type, text=f"Story {canonical_id}.", canonical_id=canonical_id, claim_id=claim_id, source_id=source_id)


def make_script(segments, excluded_no_evidence=None):
    return {
        "run_date": "2026-09-02",
        "show_name": "Healthcare AI Briefing",
        "segments": segments,
        "excluded_no_evidence": excluded_no_evidence or [],
    }


def valid_minimal_script():
    return make_script([make_segment("intro"), make_segment("outro")])


def find(checks, name):
    return next(c for c in checks if c["check"] == name)


class QaGateStructuralChecks(unittest.TestCase):
    def test_minimal_valid_script_passes_the_gate(self):
        result = qa_gate.gate(valid_minimal_script())
        self.assertTrue(result["passed"])
        self.assertTrue(all(c["passed"] for c in result["checks"]))

    def test_full_valid_script_with_top_three_and_quick_hits_passes(self):
        segments = [
            make_segment("intro"),
            make_story_segment("top_three_item", canonical_id="c1", claim_id="claim-1"),
            make_segment("quick_hits_transition", text="Now, quick hits."),
            make_story_segment("quick_hits_item", canonical_id="c2", claim_id="claim-2"),
            make_segment("outro"),
        ]
        result = qa_gate.gate(make_script(segments))
        self.assertTrue(result["passed"])

    def test_missing_intro_fails_has_intro_check(self):
        script = make_script([make_segment("outro")])
        result = qa_gate.gate(script)
        self.assertFalse(result["passed"])
        self.assertFalse(find(result["checks"], "has_intro")["passed"])

    def test_missing_outro_fails_has_outro_check(self):
        script = make_script([make_segment("intro")])
        result = qa_gate.gate(script)
        self.assertFalse(find(result["checks"], "has_outro")["passed"])

    def test_empty_segments_list_fails_both_intro_and_outro_checks(self):
        result = qa_gate.gate(make_script([]))
        self.assertFalse(result["passed"])
        self.assertFalse(find(result["checks"], "has_intro")["passed"])
        self.assertFalse(find(result["checks"], "has_outro")["passed"])

    def test_empty_narration_text_fails_no_empty_text_check(self):
        script = make_script([make_segment("intro", text=""), make_segment("outro")])
        result = qa_gate.gate(script)
        self.assertFalse(find(result["checks"], "no_empty_text")["passed"])

    def test_whitespace_only_narration_text_fails_no_empty_text_check(self):
        script = make_script([make_segment("intro", text="   "), make_segment("outro")])
        result = qa_gate.gate(script)
        self.assertFalse(find(result["checks"], "no_empty_text")["passed"])

    def test_story_segment_missing_claim_id_fails_grounded_check(self):
        segments = [make_segment("intro"), make_story_segment(claim_id=None), make_segment("outro")]
        result = qa_gate.gate(make_script(segments))
        self.assertFalse(find(result["checks"], "story_segments_grounded")["passed"])

    def test_story_segment_missing_source_id_fails_grounded_check(self):
        segments = [make_segment("intro"), make_story_segment(source_id=None), make_segment("outro")]
        result = qa_gate.gate(make_script(segments))
        self.assertFalse(find(result["checks"], "story_segments_grounded")["passed"])

    def test_connective_segments_are_not_required_to_be_grounded(self):
        # intro/outro/quick_hits_transition legitimately have no claim_id —
        # the grounded check must only apply to story segment types.
        segments = [make_segment("intro"), make_story_segment(), make_segment("outro")]
        result = qa_gate.gate(make_script(segments))
        self.assertTrue(find(result["checks"], "story_segments_grounded")["passed"])

    def test_duplicate_canonical_id_across_two_story_segments_fails(self):
        segments = [
            make_segment("intro"),
            make_story_segment("top_three_item", canonical_id="c1", claim_id="claim-1"),
            make_story_segment("quick_hits_item", canonical_id="c1", claim_id="claim-2"),
            make_segment("outro"),
        ]
        result = qa_gate.gate(make_script(segments))
        self.assertFalse(find(result["checks"], "no_duplicate_stories")["passed"])

    def test_quick_hits_item_without_a_transition_segment_fails(self):
        segments = [make_segment("intro"), make_story_segment("quick_hits_item"), make_segment("outro")]
        result = qa_gate.gate(make_script(segments))
        self.assertFalse(find(result["checks"], "quick_hits_transition_consistency")["passed"])

    def test_transition_segment_with_no_quick_hits_item_fails(self):
        segments = [make_segment("intro"), make_segment("quick_hits_transition"), make_segment("outro")]
        result = qa_gate.gate(make_script(segments))
        self.assertFalse(find(result["checks"], "quick_hits_transition_consistency")["passed"])

    def test_transition_segment_after_the_quick_hits_item_it_introduces_fails(self):
        segments = [
            make_segment("intro"),
            make_story_segment("quick_hits_item"),
            make_segment("quick_hits_transition"),
            make_segment("outro"),
        ]
        result = qa_gate.gate(make_script(segments))
        self.assertFalse(find(result["checks"], "quick_hits_transition_consistency")["passed"])

    def test_excluded_no_evidence_story_leaking_into_segments_fails(self):
        leaked_item = {"canonical_id": "c1"}
        segments = [make_segment("intro"), make_story_segment(canonical_id="c1"), make_segment("outro")]
        result = qa_gate.gate(make_script(segments, excluded_no_evidence=[leaked_item]))
        self.assertFalse(find(result["checks"], "no_excluded_stories_leaked")["passed"])

    def test_excluded_no_evidence_not_cited_anywhere_passes(self):
        excluded_item = {"canonical_id": "c99"}
        segments = [make_segment("intro"), make_story_segment(canonical_id="c1"), make_segment("outro")]
        result = qa_gate.gate(make_script(segments, excluded_no_evidence=[excluded_item]))
        self.assertTrue(find(result["checks"], "no_excluded_stories_leaked")["passed"])

    def test_run_checks_runs_every_check_even_after_an_earlier_failure(self):
        # Two independent problems at once — both must be reported, not
        # just the first one found.
        segments = [make_story_segment(claim_id=None)]  # no intro, no outro, ungrounded story
        checks = qa_gate.run_checks(make_script(segments))
        failed_names = {c["check"] for c in checks if not c["passed"]}
        self.assertIn("has_intro", failed_names)
        self.assertIn("has_outro", failed_names)
        self.assertIn("story_segments_grounded", failed_names)

    def test_gate_without_a_client_does_not_include_claims_still_pinned_check(self):
        result = qa_gate.gate(valid_minimal_script())
        self.assertIsNone(next((c for c in result["checks"] if c["check"] == "claims_still_pinned"), None))


@unittest.skipUnless(
    os.path.exists(SERVER_PATH),
    f"evidence-pinning-mcp not built — run `npm run build` in mcp/evidence-pinning ({SERVER_PATH} not found)",
)
class ClaimsStillPinnedLive(unittest.TestCase):
    def setUp(self):
        self.store_dir = tempfile.mkdtemp(prefix="qa-gate-test-")
        self.client = evidence_pinning_client.EvidencePinningClient(store_path=self.store_dir, server_path=SERVER_PATH)
        self.client.start()

    def tearDown(self):
        self.client.close()
        shutil.rmtree(self.store_dir, ignore_errors=True)

    def _pin_a_real_claim(self):
        source = self.client.register_source("https://doi.org/10.1000/xyz1", "A Paper")
        claim = self.client.pin_claim("run1", "X reduces Y", [source["source_id"]], "excerpt")
        return source["source_id"], claim["claim_id"]

    def test_a_still_pinned_claim_passes_the_gate(self):
        source_id, claim_id = self._pin_a_real_claim()
        segments = [make_segment("intro"), make_story_segment(claim_id=claim_id, source_id=source_id), make_segment("outro")]
        result = qa_gate.gate(make_script(segments), client=self.client)
        self.assertTrue(result["passed"])
        self.assertTrue(find(result["checks"], "claims_still_pinned")["passed"])

    def test_a_flagged_claim_fails_the_gate(self):
        source_id, claim_id = self._pin_a_real_claim()
        self.client.flag_claim(claim_id, "Excerpt does not actually support this claim")
        segments = [make_segment("intro"), make_story_segment(claim_id=claim_id, source_id=source_id), make_segment("outro")]
        result = qa_gate.gate(make_script(segments), client=self.client)
        self.assertFalse(result["passed"])
        self.assertFalse(find(result["checks"], "claims_still_pinned")["passed"])

    def test_an_unknown_claim_id_fails_the_gate_rather_than_raising(self):
        segments = [make_segment("intro"), make_story_segment(claim_id="nonexistent", source_id="src-1"), make_segment("outro")]
        result = qa_gate.gate(make_script(segments), client=self.client)
        self.assertFalse(result["passed"])
        self.assertFalse(find(result["checks"], "claims_still_pinned")["passed"])

    def test_structural_checks_still_run_alongside_the_live_check(self):
        # No intro segment AND a flagged claim — both should show up as
        # failures in the same gate() call.
        source_id, claim_id = self._pin_a_real_claim()
        self.client.flag_claim(claim_id, "bad excerpt")
        segments = [make_story_segment(claim_id=claim_id, source_id=source_id), make_segment("outro")]
        result = qa_gate.gate(make_script(segments), client=self.client)
        failed_names = {c["check"] for c in result["checks"] if not c["passed"]}
        self.assertIn("has_intro", failed_names)
        self.assertIn("claims_still_pinned", failed_names)


if __name__ == "__main__":
    unittest.main()
