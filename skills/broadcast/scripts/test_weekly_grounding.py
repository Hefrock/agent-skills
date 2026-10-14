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


class CheckClaimTurnBypasses(unittest.TestCase):
    """Cases found by independent review that the first version passed."""

    def test_cross_story_fact_in_text_fails_even_with_a_real_span(self):
        t = claim_turn(text="Hospital B banned it and it may cost $9M, 40%.", spans=("alerts fell by 40 percent",))
        result = wg.check_turn(t, PACK)
        self.assertFalse(result["passed"])

    def test_number_not_in_cited_story_fails(self):
        t = claim_turn(text="Alerts fell by 90 percent in two hospitals.")
        result = wg.check_turn(t, PACK)
        self.assertFalse(result["passed"])
        self.assertTrue(any("number" in r for r in result["reasons"]))

    def test_number_formatting_differences_are_not_false_failures(self):
        pack = {"c9": entry("c9", "Study", "Enrolled 1,200 patients; effect was 12.5 percent.")}
        t = claim_turn(claim_id="c9", text="It enrolled 1200 patients and the effect was 12.5 percent.", spans=("enrolled 1,200 patients",))
        self.assertTrue(wg.check_turn(t, pack)["passed"], wg.check_turn(t, pack)["reasons"])

    def test_capitalized_name_not_in_cited_story_fails(self):
        t = claim_turn(text="Alerts fell in two hospitals, Mayo Clinic said.")
        result = wg.check_turn(t, PACK)
        self.assertFalse(result["passed"])
        self.assertTrue(any("name" in r or "entit" in r for r in result["reasons"]))

    def test_single_letter_capital_cannot_smuggle_in_a_different_hospital(self):
        pack = {"c5": entry("c5", "Hospital A deploys AI triage", "The pilot reduced waits.")}
        t = claim_turn(claim_id="c5", text="Hospital B deploys AI triage.", spans=("hospital a deploys ai triage",))
        self.assertFalse(wg.check_turn(t, pack)["passed"])

    def test_empty_and_trivial_spans_fail(self):
        for span in ("", " ", ".", "a", "alerts fell"):
            self.assertFalse(wg.check_turn(claim_turn(spans=(span,)), PACK)["passed"], repr(span))

    def test_spans_must_be_a_list_of_strings(self):
        for bad in ("alerts fell by 40 percent", None, 5, [None], [["alerts fell by 40 percent"]]):
            t = claim_turn()
            t["supporting_spans"] = bad
            result = wg.check_turn(t, PACK)
            self.assertFalse(result["passed"], repr(bad))

    def test_malformed_claim_id_fails_instead_of_crashing(self):
        for bad in (["c1"], {"a": 1}, 7):
            t = claim_turn()
            t["claim_id"] = bad
            self.assertFalse(wg.check_turn(t, PACK)["passed"], repr(bad))

    def test_malformed_turn_fields_fail_instead_of_crashing(self):
        for t in ({}, {"kind": "claim"}, {"kind": "claim", "text": None, "speaker": "A"}, {"kind": ["claim"], "speaker": "A", "text": "x"}):
            self.assertFalse(wg.check_turn(t, PACK)["passed"], repr(t))

    def test_negation_not_in_cited_story_fails(self):
        t = claim_turn(text="Alerts did not fall by 40 percent in two hospitals.")
        self.assertFalse(wg.check_turn(t, PACK)["passed"])

    def test_dropped_negation_fails(self):
        pack = {"c7": entry("c7", "Trial result", "The drug did not reduce mortality in the trial.")}
        t = claim_turn(claim_id="c7", text="The drug reduced mortality in the trial.", spans=("did not reduce mortality",))
        self.assertFalse(wg.check_turn(t, pack)["passed"])

    def test_preserved_negation_passes(self):
        pack = {"c7": entry("c7", "Trial result", "The drug did not reduce mortality in the trial.")}
        t = claim_turn(claim_id="c7", text="The drug did not reduce mortality in the trial.", spans=("did not reduce mortality",))
        self.assertTrue(wg.check_turn(t, pack)["passed"], wg.check_turn(t, pack)["reasons"])


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
