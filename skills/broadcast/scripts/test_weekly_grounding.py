#!/usr/bin/env python3
"""Tests for weekly_grounding.py — the per-turn grounding contract for the
weekly two-host show. Pure functions, stdlib unittest, no network."""

import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import narrate  # noqa: E402
import weekly_grounding as wg  # noqa: E402


def entry(claim_id, headline, summary):
    return {"claim_id": claim_id, "headline": headline, "summary": summary}


PACK = {
    "c1": entry("c1", "FHIR advances decision support", "The trial found alerts fell by 40 percent in two hospitals."),
    "c2": entry("c2", "FDA issues draft guidance", "The agency said the framework may change after public comment."),
}


def claim_turn(claim_id="c1", text="Alerts fell by 40 percent in two hospitals.", spans=("alerts fell by 40 percent",), speaker="A"):
    return {"speaker": speaker, "kind": "claim", "text": text, "claim_id": claim_id, "supporting_spans": list(spans)}


class CheckClaimTurn(unittest.TestCase):
    def test_grounded_claim_turn_passes(self):
        self.assertEqual(wg.check_turn(claim_turn(), PACK), {"passed": True, "reasons": []})

    def test_missing_claim_id_fails(self):
        t = claim_turn()
        t["claim_id"] = None
        self.assertFalse(wg.check_turn(t, PACK)["passed"])

    def test_claim_id_not_in_the_pack_fails(self):
        self.assertFalse(wg.check_turn(claim_turn(claim_id="zzz"), PACK)["passed"])

    def test_no_supporting_spans_fails(self):
        self.assertFalse(wg.check_turn(claim_turn(spans=()), PACK)["passed"])

    def test_span_not_verbatim_in_source_fails(self):
        result = wg.check_turn(claim_turn(spans=("alerts fell by 80 percent",)), PACK)
        self.assertFalse(result["passed"])
        self.assertTrue(any("not found" in r for r in result["reasons"]))

    def test_span_from_a_different_story_fails_cross_story_leakage(self):
        # a real sentence, but from c2 — cited against c1
        result = wg.check_turn(claim_turn(claim_id="c1", spans=("the framework may change",)), PACK)
        self.assertFalse(result["passed"])

    def test_dropped_hedge_fails(self):
        t = claim_turn(claim_id="c2", text="The framework will change after comment.", spans=("the framework may change",))
        result = wg.check_turn(t, PACK)
        self.assertFalse(result["passed"])
        self.assertTrue(any("hedg" in r for r in result["reasons"]))

    def test_hedge_word_inside_another_word_does_not_mask_a_dropped_hedge(self):
        t = claim_turn(claim_id="c2", text="Mayo Clinic says the framework will change.", spans=("the framework may change",))
        self.assertFalse(wg.check_turn(t, PACK)["passed"])

    def test_preserved_hedge_passes(self):
        t = claim_turn(claim_id="c2", text="The framework may change after public comment.", spans=("the framework may change",))
        self.assertTrue(wg.check_turn(t, PACK)["passed"])

    def test_empty_text_fails(self):
        self.assertFalse(wg.check_turn(claim_turn(text="   "), PACK)["passed"])

    def test_reports_every_failed_check_not_just_the_first(self):
        t = claim_turn(claim_id="c2", text="It will change.", spans=("nowhere in source",))
        self.assertGreaterEqual(len(wg.check_turn(t, PACK)["reasons"]), 2)


class CheckFixedTurns(unittest.TestCase):
    def test_reactive_turn_from_the_pool_passes(self):
        phrase = narrate.REACTIVE_TEMPLATES["top_three_item"][0]
        t = {"speaker": "B", "kind": "reactive", "text": phrase, "claim_id": None, "supporting_spans": []}
        self.assertTrue(wg.check_turn(t, PACK)["passed"])

    def test_invented_reactive_turn_fails(self):
        t = {"speaker": "B", "kind": "reactive", "text": "That drug is a miracle.", "claim_id": None, "supporting_spans": []}
        self.assertFalse(wg.check_turn(t, PACK)["passed"])

    def test_link_turn_from_the_fixed_set_passes(self):
        t = {"speaker": "A", "kind": "link", "text": wg.LINK_TURNS[0], "claim_id": None, "supporting_spans": []}
        self.assertTrue(wg.check_turn(t, PACK)["passed"])

    def test_invented_link_turn_fails_so_cross_story_comparisons_cannot_carry_facts(self):
        t = {"speaker": "A", "kind": "link", "text": "Unlike the FHIR trial, this rule bans AI outright.", "claim_id": None, "supporting_spans": []}
        self.assertFalse(wg.check_turn(t, PACK)["passed"])

    def test_non_claim_turn_carrying_a_claim_id_fails(self):
        t = {"speaker": "B", "kind": "link", "text": wg.LINK_TURNS[0], "claim_id": "c1", "supporting_spans": []}
        self.assertFalse(wg.check_turn(t, PACK)["passed"])

    def test_unknown_kind_fails(self):
        t = {"speaker": "A", "kind": "opinion", "text": "I think so.", "claim_id": None, "supporting_spans": []}
        self.assertFalse(wg.check_turn(t, PACK)["passed"])

    def test_unknown_speaker_fails(self):
        self.assertFalse(wg.check_turn(claim_turn(speaker="C"), PACK)["passed"])


class CheckTurns(unittest.TestCase):
    def test_all_grounded_passes_and_lists_each_turn(self):
        turns = [claim_turn(), {"speaker": "B", "kind": "link", "text": wg.LINK_TURNS[0], "claim_id": None, "supporting_spans": []}]
        result = wg.check_turns(turns, PACK)
        self.assertTrue(result["passed"])
        self.assertEqual(len(result["results"]), 2)

    def test_one_bad_turn_fails_the_whole_script_and_is_identified(self):
        turns = [claim_turn(), claim_turn(spans=("fabricated",))]
        result = wg.check_turns(turns, PACK)
        self.assertFalse(result["passed"])
        self.assertEqual([r["index"] for r in result["results"] if not r["passed"]], [1])

    def test_empty_script_does_not_pass(self):
        self.assertFalse(wg.check_turns([], PACK)["passed"])

    def test_script_with_no_claim_turns_does_not_pass(self):
        turns = [{"speaker": "A", "kind": "link", "text": wg.LINK_TURNS[0], "claim_id": None, "supporting_spans": []}]
        self.assertFalse(wg.check_turns(turns, PACK)["passed"])


if __name__ == "__main__":
    unittest.main()
