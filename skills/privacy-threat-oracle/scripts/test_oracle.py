import json
import subprocess
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import oracle  # noqa: E402

SCRIPT = str(Path(__file__).resolve().parent / "oracle.py")


class RuleTable(unittest.TestCase):
    def test_no_violation_no_sensitivity_proceeds(self):
        r = oracle.evaluate("personal", "personal", "public_internet", ["none"])
        self.assertEqual(r["recommendation"], "proceed")
        self.assertEqual(r["residual_risk"], "low")
        self.assertFalse(r["compartment_violation"])

    def test_compartment_violation_with_high_sensitivity_declines(self):
        r = oracle.evaluate("sensitive_research", "public_professional", "public_internet", ["direct_pii"])
        self.assertTrue(r["compartment_violation"])
        self.assertEqual(r["sensitivity_tier"], "high")
        self.assertEqual(r["recommendation"], "decline")
        self.assertEqual(r["residual_risk"], "high")

    def test_compartment_violation_alone_is_modification_not_decline(self):
        r = oracle.evaluate("sensitive_research", "personal", "close_group", ["none"])
        self.assertTrue(r["compartment_violation"])
        self.assertEqual(r["recommendation"], "proceed_with_modification")
        self.assertEqual(r["residual_risk"], "medium")

    def test_high_sensitivity_no_violation_high_cost_adversary_declines(self):
        # specific_person exposure -> stalker only, which is a high-cost adversary
        r = oracle.evaluate("personal", "personal", "specific_person", ["direct_pii"])
        self.assertFalse(r["compartment_violation"])
        self.assertEqual(r["adversary_cost_tier"], "high")
        self.assertEqual(r["recommendation"], "decline")

    def test_high_sensitivity_no_high_cost_adversary_is_modification(self):
        # employer_visible -> employer (medium) + civil_discovery (medium): no high-cost
        # adversary in this exposure, unlike public_internet (state_actor) or
        # specific_person/close_group (stalker).
        r = oracle.evaluate("personal", "personal", "employer_visible", ["secret"])
        self.assertEqual(r["adversary_cost_tier"], "medium")
        self.assertEqual(r["recommendation"], "proceed_with_modification")
        self.assertEqual(r["residual_risk"], "medium")

    def test_medium_sensitivity_no_high_cost_adversary_proceeds_via_named_exposure(self):
        r = oracle.evaluate("public_professional", "public_professional", "employer_visible", ["metadata"])
        self.assertEqual(r["adversary_cost_tier"], "medium")
        self.assertEqual(r["recommendation"], "proceed")
        self.assertEqual(r["residual_risk"], "low")

    def test_medium_sensitivity_no_high_cost_adversary_proceeds(self):
        tm = oracle.load_threat_model()
        # Build a synthetic exposure with only low-cost adversaries to isolate this branch.
        tm["target_exposure_map"]["low_cost_only"] = ["data_brokers", "corporations"]
        r = oracle.evaluate("personal", "personal", "low_cost_only", ["metadata"], threat_model=tm)
        self.assertEqual(r["recommendation"], "proceed")
        self.assertEqual(r["residual_risk"], "low")

    def test_medium_sensitivity_high_cost_adversary_is_modification(self):
        r = oracle.evaluate("personal", "personal", "specific_person", ["metadata"])
        self.assertEqual(r["adversary_cost_tier"], "high")
        self.assertEqual(r["recommendation"], "proceed_with_modification")
        self.assertEqual(r["residual_risk"], "medium")

    def test_none_content_class_treated_as_no_sensitivity(self):
        r = oracle.evaluate("personal", "personal", "public_internet", [])
        self.assertEqual(r["sensitivity_tier"], "none")

    def test_multiple_content_classes_takes_highest_tier(self):
        r = oracle.evaluate("personal", "personal", "public_internet", ["metadata", "direct_pii"])
        self.assertEqual(r["sensitivity_tier"], "high")

    def test_unknown_target_exposure_raises(self):
        with self.assertRaises(ValueError):
            oracle.evaluate("personal", "personal", "not_a_real_exposure", ["none"])

    def test_unknown_compartment_raises(self):
        with self.assertRaises(ValueError):
            oracle.evaluate("not_a_real_compartment", "personal", "public_internet", ["none"])


class CompartmentIsolationIsFullyMutual(unittest.TestCase):
    """Confirmed 2026-09-15: all three compartments are isolated from each other, no
    exceptions -- personal explicitly includes family content, which is reason enough
    on its own to keep it walled off from public_professional too, not just from
    sensitive_research. The vault's own design doc briefly read ambiguously enough to
    suggest personal and public_professional might be allowed to mix; they aren't. This
    locks in the confirmed policy so a future edit to threat-model.json's may_reference
    lists can't silently reopen that ambiguity. See decision-rubric.md's Step 1."""

    def test_every_ordered_pair_of_distinct_compartments_is_a_violation(self):
        compartments = ["public_professional", "personal", "sensitive_research"]
        for source in compartments:
            for target in compartments:
                if source == target:
                    continue
                r = oracle.evaluate(source, target, "close_group", ["none"])
                self.assertTrue(
                    r["compartment_violation"],
                    f"{source} -> {target} should be a compartment violation",
                )

    def test_personal_to_public_professional_is_a_violation(self):
        """The specific pairing the ambiguous vault wording could have been misread to
        permit -- confirmed it does not, since personal includes family content."""
        r = oracle.evaluate("personal", "public_professional", "close_group", ["none"])
        self.assertTrue(r["compartment_violation"])


class OutOfScopeAdversaries(unittest.TestCase):
    """state_actor is the only adversary currently flagged out_of_scope in
    threat-model.json, and public_internet is the only exposure that reaches it.
    Surfacing it is purely informational -- it must never change what the rule
    table itself decides, only add a note the caller can see or ignore."""

    def test_public_internet_surfaces_state_actor_as_out_of_scope(self):
        r = oracle.evaluate("personal", "personal", "public_internet", ["direct_pii"])
        self.assertEqual(r["out_of_scope_adversaries_exposed"], ["state_actor"])
        self.assertIn("State actor", r["reason"])
        self.assertIn("out-of-scope", r["reason"])

    def test_exposure_without_state_actor_has_no_out_of_scope_note(self):
        r = oracle.evaluate("personal", "personal", "specific_person", ["direct_pii"])
        self.assertEqual(r["out_of_scope_adversaries_exposed"], [])
        self.assertNotIn("out-of-scope", r["reason"])

    def test_out_of_scope_surfacing_does_not_change_the_recommendation(self):
        """public_internet + no sensitivity still proceeds -- state_actor's
        cost_to_defend still feeds adversary_cost_tier (it's modeled, not excluded),
        but this must stay a rule-table decision, not something the note overrides."""
        r = oracle.evaluate("personal", "personal", "public_internet", ["none"])
        self.assertEqual(r["out_of_scope_adversaries_exposed"], ["state_actor"])
        self.assertEqual(r["recommendation"], "proceed")


class AutonomousAiAgentAdversary(unittest.TestCase):
    """autonomous_ai_agent (public_internet only): unlike state_actor, this one is
    NOT flagged out_of_scope -- the whole point of adding it is that, unlike
    state-actor-level defense (real opsec, out of reach for a personal tool),
    defending against automated cross-compartment correlation is exactly what
    compartmentalization discipline is for. It should count toward cost tier and
    the recommendation like any ordinary adversary, with no out-of-scope note."""

    def test_public_internet_exposes_it_as_high_cost_and_in_scope(self):
        r = oracle.evaluate("personal", "personal", "public_internet", ["direct_pii"])
        self.assertIn("autonomous_ai_agent", r["exposed_adversaries"])
        self.assertEqual(r["adversary_cost_tier"], "high")
        self.assertNotIn("autonomous_ai_agent", r["out_of_scope_adversaries_exposed"])

    def test_not_exposed_at_other_exposure_levels(self):
        for exposure in ("specific_person", "close_group", "employer_visible"):
            r = oracle.evaluate("personal", "personal", exposure, ["direct_pii"])
            self.assertNotIn("autonomous_ai_agent", r["exposed_adversaries"], exposure)


class ReversibilityDowngrade(unittest.TestCase):
    def test_decline_downgrades_to_modification(self):
        r = oracle.evaluate("sensitive_research", "public_professional", "public_internet", ["direct_pii"], reversible=True)
        self.assertEqual(r["recommendation"], "proceed_with_modification")
        self.assertTrue(r["reversibility_applied"])

    def test_modification_downgrades_to_proceed(self):
        r = oracle.evaluate("sensitive_research", "personal", "close_group", ["none"], reversible=True)
        self.assertEqual(r["recommendation"], "proceed")
        self.assertTrue(r["reversibility_applied"])

    def test_proceed_stays_proceed_and_not_marked_applied(self):
        r = oracle.evaluate("personal", "personal", "public_internet", ["none"], reversible=True)
        self.assertEqual(r["recommendation"], "proceed")
        self.assertFalse(r["reversibility_applied"])

    def test_reversible_never_fully_clears_a_compartment_violation_plus_high_sensitivity(self):
        r = oracle.evaluate("sensitive_research", "public_professional", "public_internet", ["direct_pii"], reversible=True)
        self.assertNotEqual(r["recommendation"], "proceed")


class LinterJsonBridge(unittest.TestCase):
    def test_extracts_deduped_classes(self):
        payload = json.dumps([
            {"leak_class": "direct_pii", "severity": "high"},
            {"leak_class": "direct_pii", "severity": "high"},
            {"leak_class": "secret", "severity": "high"},
        ])
        self.assertEqual(oracle.content_classes_from_linter_json(payload), ["direct_pii", "secret"])

    def test_malformed_json_returns_empty_list(self):
        self.assertEqual(oracle.content_classes_from_linter_json("not json"), [])

    def test_non_list_json_returns_empty_list(self):
        self.assertEqual(oracle.content_classes_from_linter_json(json.dumps({"leak_class": "direct_pii"})), [])

    def test_findings_missing_leak_class_field_are_skipped(self):
        payload = json.dumps([{"severity": "high"}, {"leak_class": "metadata"}])
        self.assertEqual(oracle.content_classes_from_linter_json(payload), ["metadata"])

    def test_real_scan_diff_output_round_trips(self):
        """Regression guard for the bug this replaced: the fixtures above hand-build
        {"leak_class": ...} payloads, which would keep passing even if scan_diff.py's
        actual Finding schema drifted again (that's exactly how the original {"class":
        ...} mismatch slipped through). This runs the real scan_diff.py --json and
        feeds its literal output through the bridge, so a future schema change here
        or there breaks this test instead of silently degrading to zero content classes."""
        scan_diff = str(Path(__file__).resolve().parents[2] / "privacy-linter" / "scripts" / "scan_diff.py")
        proc = subprocess.run(
            [sys.executable, scan_diff, "--text", "-", "--json"],
            capture_output=True, text=True, input="AWS key: AKIAABCDEFGHIJKLMNOP\n",
        )
        self.assertEqual(proc.returncode, 0)
        classes = oracle.content_classes_from_linter_json(proc.stdout)
        self.assertEqual(classes, ["secret"])


class Cli(unittest.TestCase):
    def _run(self, *args, input_text=None):
        return subprocess.run(
            [sys.executable, SCRIPT, *args],
            capture_output=True, text=True, input=input_text,
        )

    def test_human_output_reports_recommendation(self):
        proc = self._run("--source-compartment", "personal", "--target-compartment", "personal",
                          "--target-exposure", "public_internet", "--content-class", "none")
        self.assertEqual(proc.returncode, 0)
        self.assertIn("recommendation: proceed", proc.stdout)

    def test_json_output_is_valid(self):
        proc = self._run("--source-compartment", "personal", "--target-compartment", "personal",
                          "--target-exposure", "public_internet", "--content-class", "none", "--json")
        self.assertEqual(proc.returncode, 0)
        data = json.loads(proc.stdout)
        self.assertEqual(data["recommendation"], "proceed")

    def test_invalid_choice_errors_via_argparse(self):
        proc = self._run("--source-compartment", "not_real", "--target-compartment", "personal",
                          "--target-exposure", "public_internet")
        self.assertNotEqual(proc.returncode, 0)

    def test_from_linter_json_stdin_feeds_content_classes(self):
        payload = json.dumps([{"leak_class": "secret", "severity": "high"}])
        proc = self._run("--source-compartment", "personal", "--target-compartment", "personal",
                          "--target-exposure", "specific_person", "--from-linter-json", "-",
                          "--json", input_text=payload)
        self.assertEqual(proc.returncode, 0)
        data = json.loads(proc.stdout)
        self.assertEqual(data["sensitivity_tier"], "high")
        self.assertEqual(data["recommendation"], "decline")

    def test_repeated_content_class_flags_accumulate(self):
        proc = self._run("--source-compartment", "personal", "--target-compartment", "personal",
                          "--target-exposure", "public_internet",
                          "--content-class", "metadata", "--content-class", "direct_pii", "--json")
        data = json.loads(proc.stdout)
        self.assertEqual(data["sensitivity_tier"], "high")


if __name__ == "__main__":
    unittest.main()
