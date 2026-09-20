#!/usr/bin/env python3
"""
Regression test suite for the de-id/re-id harness. Stdlib only (unittest) — run:

    python test_harness.py

It locks the load-bearing invariants and the verified headline numbers so later work
(an LLM attacker, a Synthea driver, a new defender) cannot silently regress them. Two
layers:
  * pure-logic invariants, tested by importing the modules directly;
  * end-to-end headline numbers, tested by running the real CLIs in a temp dir.

If a number here changes on purpose, update the expected value in the same commit — a
failure means "a result moved," which should always be a conscious decision.
"""
import contextlib, io, json, os, subprocess, sys, tempfile, shutil, unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import generate_corpus as gc
from deid_pipelines import get_pipeline, REGISTRY, REDACTION
from person_sources import get_source, PERSON_KEYS
import inference_attackers as ia
import score_utility
from bootstrap import bootstrap_ratio_ci, paired_bootstrap_diff
import score_stats
from score_reid import resolve_population_path

SEED, N, POP = 20260101, 50, 100000


def run(script, *args):
    return subprocess.run([sys.executable, script, *args], cwd=HERE,
                          capture_output=True, text=True)


def load(path):
    with open(path) as f:
        return json.load(f)


class CorpusInvariants(unittest.TestCase):
    def test_offset_invariant_identifiers_and_clinical(self):
        c = gc.generate(N, SEED, with_inference=True, with_utility=True)
        gc.self_test(c)  # raises if any identifier OR clinical span offset is wrong
        for rec in c["records"]:
            t = rec["note_text"]
            for s in rec["clinical_spans"]:
                self.assertEqual(t[s["start"]:s["end"]], s["text"])

    def test_inference_invariant_target_absent_from_vignette(self):
        c = gc.generate(N, SEED, with_inference=True)
        gc.inference_self_test(c)  # raises if a target diagnosis appears in its vignette
        for rec in c["records"]:
            case = rec["inference_case"]
            self.assertNotIn(case["target_value"].lower(), case["note"].lower())

    def test_byte_identical_across_flags(self):
        plain = gc.generate(N, SEED)["records"]
        allf = gc.generate(N, SEED, with_inference=True, with_utility=True)["records"]
        extra = {"clinical_spans", "inference_case"}
        strip = lambda recs: [{k: v for k, v in r.items() if k not in extra} for r in recs]
        self.assertEqual(strip(plain), strip(allf),
                         "enabling --inference/--utility perturbed the Track 1/2 fields")

    def test_determinism(self):
        a = gc.generate(N, SEED, with_inference=True, with_utility=True)
        b = gc.generate(N, SEED, with_inference=True, with_utility=True)
        self.assertEqual(json.dumps(a, sort_keys=True), json.dumps(b, sort_keys=True))

    def test_clinical_spans_disjoint_from_identifiers(self):
        c = gc.generate(N, SEED, with_utility=True)
        ov = lambda a, b: a["start"] < b["end"] and b["start"] < a["end"]
        for rec in c["records"]:
            for cs in rec["clinical_spans"]:
                for idsp in rec["identifiers"]:
                    self.assertFalse(ov(cs, idsp), "clinical span overlaps an identifier")

    def test_age_over_89_is_identifier_not_clinical(self):
        c = gc.generate(2000, 7, with_utility=True)
        seen = 0
        for rec in c["records"]:
            if rec["quasi_identifiers"]["age"] > 89:
                seen += 1
                cats = [cs["clinical_category"] for cs in rec["clinical_spans"]]
                self.assertNotIn("age", cats)  # >89 is PHI, marked as an identifier instead
        self.assertGreater(seen, 0, "sanity: some ages should exceed 89 at n=2000")


class DefenderInvariants(unittest.TestCase):
    def setUp(self):
        self.corpus = gc.generate(N, SEED, with_utility=True)

    def test_redacted_spans_well_formed_and_reconstruct(self):
        for name in REGISTRY:
            pipe = get_pipeline(name)
            for rec in self.corpus["records"]:
                t = rec["note_text"]
                scrubbed, red = pipe.scrub(t)
                prev = -1
                for r in red:
                    self.assertTrue(0 <= r["start"] < r["end"] <= len(t), name)
                    self.assertGreaterEqual(r["start"], prev, f"{name}: unsorted/overlap")
                    self.assertEqual(t[r["start"]:r["end"]], r["text"], name)
                    prev = r["end"]
                out = t
                for r in sorted(red, key=lambda x: -x["start"]):
                    out = out[:r["start"]] + REDACTION + out[r["end"]:]
                self.assertEqual(out, scrubbed, f"{name}: scrubbed text does not reconstruct")


class UtilityOverlapLogic(unittest.TestCase):
    def test_overlap_semantics(self):
        cs = [{"span_id": "d", "start": 10, "end": 23,
               "text": "Fabry disease", "clinical_category": "diagnosis"}]
        pres = lambda red: score_utility.score_record(cs, "x", red)[0]["preserved"]
        self.assertFalse(pres([{"start": 10, "end": 15, "text": "Fabry"}]), "partial -> destroyed")
        self.assertFalse(pres([{"start": 10, "end": 23, "text": "Fabry disease"}]), "full -> destroyed")
        self.assertTrue(pres([{"start": 30, "end": 35, "text": "x"}]), "no overlap -> preserved")
        self.assertTrue(pres([{"start": 23, "end": 28, "text": "x"}]), "adjacent -> preserved")


class HeadlineNumbers(unittest.TestCase):
    """End-to-end via the real CLIs. Locks the verified numbers for the default corpus."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="deidtest_")
        cls.corpus = os.path.join(cls.tmp, "c.json")
        r = run("generate_corpus.py", "--n", str(N), "--seed", str(SEED),
                "--population", str(POP), "--inference", "--utility",
                "--out", cls.corpus, "--population-out", os.path.join(cls.tmp, "population.jsonl"))
        assert r.returncode == 0, r.stderr

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def _out(self, name):
        return os.path.join(self.tmp, name)

    def test_track1_baseline_coverage_0449(self):
        runs, rep = self._out("runs.jsonl"), self._out("leak.json")
        run("run_track1.py", "--corpus", self.corpus, "--pipeline", "regex-baseline-v0", "--out", runs)
        run("score_leakage.py", "--runs", runs, "--out", rep)
        self.assertAlmostEqual(load(rep)["overall"]["coverage"], 0.449, places=3)

    def test_frontier_two_corners(self):
        rep = self._out("frontier.json")
        run("score_frontier.py", "--corpus", self.corpus, "--out", rep)
        pts = {p["pipeline"]: p for p in load(rep)["points"]}
        self.assertAlmostEqual(pts["regex-baseline-v0"]["privacy"], 0.449, places=3)
        self.assertAlmostEqual(pts["regex-baseline-v0"]["utility"], 1.0, places=3)
        self.assertAlmostEqual(pts["over-redact-v0"]["privacy"], 0.900, places=3)
        self.assertAlmostEqual(pts["over-redact-v0"]["utility"], 0.600, places=3)

    def test_track3_recovery_094(self):
        rep = self._out("inf.json")
        run("score_inference.py", "--corpus", self.corpus, "--out", rep, "--eval-out", self._out("e.jsonl"))
        self.assertAlmostEqual(load(rep)["overall_recovery"], 0.94, places=3)

    def test_crosstrack_headline_consistency_and_reuse(self):
        xt = self._out("xt.json")
        run("score_crosstrack.py", "--corpus", self.corpus, "--out", xt)
        r = load(xt)
        self.assertEqual(r["vulnerable_on_either_axis"]["count"], 49)
        e = r["vulnerable_on_either_axis"]["count"]
        re_ = r["reidentifiable_despite_safe_harbor"]["count"]
        inf = r["inferable_despite_safe_harbor"]["count"]
        both = r["vulnerable_on_both_axes"]["count"]
        self.assertEqual(e, re_ + inf - both, "inclusion-exclusion violated")
        # cross-track reuses Track 2/3 logic -> per-record outputs must match the standalone scorers
        reid, inf_r = self._out("reid.json"), self._out("inf2.json")
        run("score_reid.py", "--corpus", self.corpus, "--out", reid)
        run("score_inference.py", "--corpus", self.corpus, "--out", inf_r, "--eval-out", self._out("e2.jsonl"))
        xtr = {x["record_id"]: x for x in r["per_record"]}
        for rr in load(reid)["per_record"]:
            self.assertEqual(xtr[rr["record_id"]]["k_population"], rr["k_population"])
        for ir in load(inf_r)["per_record"]:
            self.assertEqual(xtr[ir["record_id"]]["inferable"], ir["correct"])

    def test_crosstrack_guard_requires_both_flags(self):
        plain = self._out("plain.json")
        run("generate_corpus.py", "--n", "5", "--seed", "1", "--out", plain)
        r = run("score_crosstrack.py", "--corpus", plain, "--out", self._out("z.json"))
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("--population AND --inference", r.stderr)


class FhirPersonSource(unittest.TestCase):
    """The Synthea/FHIR person source: real demographics flow through unchanged machinery."""
    FIX = os.path.join(HERE, "fixtures", "fhir")

    def test_parses_all_patients_with_required_keys(self):
        src = get_source("fhir-synthea", fhir_dir=self.FIX)
        self.assertEqual(len(src), 3)
        for i in range(len(src)):
            p = src.person(i, None)
            for k in PERSON_KEYS:
                self.assertIn(k, p, f"FHIR person missing {k}")

    def test_field_mapping(self):
        src = get_source("fhir-synthea", fhir_dir=self.FIX)
        alice = next(p for p in src.persons if p["last"] == "Okafor")
        self.assertEqual(alice["first"], "Adaeze")          # Synthea digit-suffix stripped
        self.assertEqual(alice["sex"], "F")                 # gender female -> F
        self.assertEqual(alice["zip3"], "021")              # from postalCode 02127
        self.assertEqual(alice["age"], 2026 - 1979)         # from birthDate
        self.assertEqual(alice["diagnosis"], "Fabry disease")  # mapped from SNOMED condition
        self.assertTrue(alice["rare"])
        self.assertEqual(alice["facility"], "Riverton General Hospital")

    def test_full_pipeline_on_fhir_corpus(self):
        src = get_source("fhir-synthea", fhir_dir=self.FIX)
        c = gc.generate(3, 20260101, with_inference=True, with_utility=True, source=src)
        gc.self_test(c)             # offset invariant holds on FHIR-sourced notes
        gc.inference_self_test(c)   # withheld diagnosis absent from the vignette
        self.assertEqual(len(c["records"]), 3)
        self.assertEqual(c["generator"], "fhir-synthea")

    def test_unknown_source_raises(self):
        with self.assertRaises(KeyError):
            get_source("bogus")

    def test_missing_fhir_dir_raises(self):
        with self.assertRaises(SystemExit):
            get_source("fhir-synthea", fhir_dir="/no/such/dir")

    def test_malformed_file_skipped_not_fatal(self):
        tmp = tempfile.mkdtemp(prefix="fhirbad_")
        try:
            for name in os.listdir(self.FIX):
                shutil.copy(os.path.join(self.FIX, name), tmp)
            with open(os.path.join(tmp, "broken.json"), "w") as f:
                f.write("{not valid json")
            src = get_source("fhir-synthea", fhir_dir=tmp)  # must not raise
            self.assertEqual(len(src), 3)  # the 3 good patients still parsed
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_fhir_sourced_population_matches_sample_distribution(self):
        # A FHIR population must carry the SAME real QI values the sample does (not
        # synthetic ones), so Track 2's denominator is distributionally consistent.
        src = get_source("fhir-synthea", fhir_dir=self.FIX)
        pop = gc.generate_population(100000, 999, source=src)  # requested >> available
        self.assertEqual(len(pop), 3, "population capped at the source's supply")
        # Alice's real ZIP3 (from postalCode 02127) must appear in the population profiles.
        zips = {p["zip3"] for p in pop}
        self.assertIn("021", zips)
        for p in pop:  # QI profile shape is exactly what Track 2 consumes
            self.assertEqual(set(p), {"age", "sex", "zip3", "admission_date",
                                      "rare_diagnosis", "facility_id"})

    def test_synthetic_population_unchanged_by_source_param(self):
        # generate_population(source=None) must be byte-identical to the pre-change call.
        a = gc.generate_population(200, 7)
        b = gc.generate_population(200, 7, source=None)
        self.assertEqual(a, b)


class MaCityZip3Fallback(unittest.TestCase):
    """Regression coverage for a real, confirmed bug: Synthea v3.3.0's Massachusetts
    module emits a literal postalCode "00000" for patients in certain towns (confirmed
    on a real 300-patient pilot generation, ~24% of patients, systematic per town, not
    per-patient randomness) — the naive postalCode[:3] extraction put a real quarter of
    the population into a fake "000" zip3 bucket, which would corrupt Track 2's
    k-anonymity numbers. Fixed by falling back to a real, government-sourced MA
    city->ZIP3 table (ma_city_zip3.json) when postalCode is missing or "00000"."""

    def _bundle(self, city, postal_code):
        return {
            "resourceType": "Bundle", "type": "collection",
            "entry": [{"resource": {
                "resourceType": "Patient", "id": "test-patient-1",
                "name": [{"family": "Test", "given": ["Case"]}],
                "gender": "female", "birthDate": "1980-01-01",
                "address": [{"city": city, "state": "MA", "postalCode": postal_code}],
            }}],
        }

    def _load_one(self, tmp, city, postal_code):
        with open(os.path.join(tmp, "patient.json"), "w") as f:
            json.dump(self._bundle(city, postal_code), f)
        src = get_source("fhir-synthea", fhir_dir=tmp)
        return src.person(0, None)

    # --- _zip3_from_city() unit coverage ------------------------------------------
    def test_exact_city_match(self):
        from person_sources import _zip3_from_city
        self.assertEqual(_zip3_from_city("Boston"), "021")

    def test_directional_strip_from_input(self):
        # "West Concord" isn't in the dataset; "Concord" is.
        from person_sources import _zip3_from_city
        self.assertEqual(_zip3_from_city("West Concord"), "017")

    def test_directional_strip_onto_input(self):
        # "Dartmouth" isn't in the dataset; only "North Dartmouth"/"South Dartmouth" are.
        # The bug this specifically regression-tests: an earlier version only tried
        # stripping a prefix FROM the input, never adding one TO it, and silently missed
        # this direction on a real pilot run.
        from person_sources import _zip3_from_city
        self.assertEqual(_zip3_from_city("Dartmouth"), "027")

    def test_override_table_hit(self):
        from person_sources import _zip3_from_city
        self.assertEqual(_zip3_from_city("Cochituate"), "017")  # village of Wayland

    def test_borough_to_boro_postal_name_swap(self):
        # Confirmed on a real 22,754-patient background-population generation: Synthea
        # uses the town's full legal name, the ZIP dataset uses USPS's abbreviated
        # postal name -- "Middleborough" only exists in the dataset as "Middleboro".
        # 95 of 511 unresolved-city occurrences on that run were this single town alone.
        from person_sources import _zip3_from_city
        self.assertEqual(_zip3_from_city("Middleborough"), "023")
        self.assertEqual(_zip3_from_city("North Attleborough"), "027")
        self.assertEqual(_zip3_from_city("Tyngsborough"), "018")

    def test_village_suffix_strip(self):
        # "Wareham Center" is a village of "Wareham", which is already in the dataset.
        from person_sources import _zip3_from_city
        self.assertEqual(_zip3_from_city("Wareham Center"), "025")

    def test_two_transforms_chained(self):
        # Real regression case from the same 22,754-patient run: "Middleborough Center"
        # needs BOTH the village-suffix strip (-> "Middleborough") AND the borough->boro
        # swap (-> "Middleboro") before it's found -- neither transform alone resolves
        # it, confirmed directly before this two-transform chaining was added.
        from person_sources import _zip3_from_city
        self.assertEqual(_zip3_from_city("Middleborough Center"), "023")

    def test_generic_transforms_do_not_shadow_a_real_exact_match(self):
        # A town whose plain name IS in the dataset must resolve directly, not get
        # mangled by a transform that happens to also apply syntactically.
        from person_sources import _zip3_from_city
        self.assertEqual(_zip3_from_city("Boston"), "021")

    def test_unresolvable_city_returns_none(self):
        from person_sources import _zip3_from_city
        self.assertIsNone(_zip3_from_city("Not A Real Massachusetts Town"))

    # --- end-to-end through FhirSynthaSource ---------------------------------------
    def test_degenerate_postal_code_recovers_real_zip3(self):
        tmp = tempfile.mkdtemp(prefix="fhirzip_")
        try:
            person = self._load_one(tmp, "Dartmouth", "00000")
            self.assertEqual(person["zip3"], "027")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_missing_postal_code_recovers_real_zip3(self):
        tmp = tempfile.mkdtemp(prefix="fhirzip_")
        try:
            person = self._load_one(tmp, "Boston", "")
            self.assertEqual(person["zip3"], "021")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_valid_postal_code_still_used_directly(self):
        # A real postal code must never be overridden by the city lookup, even for a
        # city also present in ma_city_zip3.json.
        tmp = tempfile.mkdtemp(prefix="fhirzip_")
        try:
            person = self._load_one(tmp, "Boston", "02199")
            self.assertEqual(person["zip3"], "021")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_genuinely_unknown_city_falls_back_to_000_without_crashing(self):
        # The safety net for a town this table has never seen: still "000", still a
        # warning, never a crash -- the harness's existing "never raises" discipline.
        tmp = tempfile.mkdtemp(prefix="fhirzip_")
        stderr = io.StringIO()
        try:
            with contextlib.redirect_stderr(stderr):
                person = self._load_one(tmp, "Not A Real Massachusetts Town", "00000")
            self.assertEqual(person["zip3"], "000")
            self.assertIn("no ZIP3 known", stderr.getvalue())
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class InferenceAttackerComplianceGate(unittest.TestCase):
    """Issue #126, Tier 1: calling a third-party API on DUA-governed clinical text is
    almost certainly a prohibited disclosure -- get_attacker() must refuse to hand back
    an attacker that calls one unless explicitly acknowledged. No live-LLM attacker
    exists yet (see inference_attackers.py's module docstring), so a fake one injected
    via get_attacker()'s registry override is the only way to test this today."""

    class FakeExternalAttacker(ia.InferenceAttacker):
        name = "fake-external-v0"
        calls_external_api = True

        def infer(self, note_text):
            return {"guess": None, "confidence": 0.0, "rationale": ""}

    FAKE_REGISTRY = {FakeExternalAttacker.name: FakeExternalAttacker}

    def test_baseline_attacker_needs_no_acknowledgment(self):
        a = ia.get_attacker("signature-match-v0")
        self.assertFalse(a.calls_external_api)

    def test_external_api_attacker_refused_without_acknowledgment(self):
        with self.assertRaises(SystemExit) as ctx:
            ia.get_attacker("fake-external-v0", registry=self.FAKE_REGISTRY)
        self.assertIn("calls_external_api", str(ctx.exception))
        self.assertIn("--acknowledge-external-api-risk", str(ctx.exception))

    def test_external_api_attacker_allowed_with_acknowledgment(self):
        a = ia.get_attacker("fake-external-v0", acknowledge_external_api=True, registry=self.FAKE_REGISTRY)
        self.assertEqual(a.name, "fake-external-v0")

    def test_acknowledgment_flag_is_a_no_op_for_the_baseline(self):
        # Passing the flag when it isn't needed must never change behavior.
        a = ia.get_attacker("signature-match-v0", acknowledge_external_api=True)
        self.assertEqual(a.name, "signature-match-v0")

    def test_unknown_attacker_still_raises_keyerror_not_swallowed_by_the_gate(self):
        with self.assertRaises(KeyError):
            ia.get_attacker("nonexistent-attacker")

    def test_cli_refuses_without_the_flag(self):
        r = run("score_inference.py", "--corpus", self._corpus_path(),
                "--attacker", "signature-match-v0", "--out", self._out("inference_report.json"))
        self.assertEqual(r.returncode, 0, "the bundled baseline must never need the flag")

    def _out(self, name):
        fd, path = tempfile.mkstemp(prefix="gate_", suffix="_" + name)
        os.close(fd)
        return path

    def _corpus_path(self):
        path = self._out("corpus.json")
        c = gc.generate(3, 1, with_inference=True)
        with open(path, "w") as f:
            json.dump(c, f)
        return path


class BootstrapPrimitives(unittest.TestCase):
    """bootstrap.py in isolation: correctness on hand-computable cases, not the harness."""

    def test_ratio_ci_point_estimate_is_exact_ratio(self):
        pairs = [(1, 1), (0, 1), (1, 1), (1, 1)]  # 3/4 caught
        ci = bootstrap_ratio_ci(pairs, n_boot=500)
        self.assertAlmostEqual(ci["point"], 0.75)
        self.assertTrue(ci["ci_lo"] <= ci["point"] <= ci["ci_hi"])

    def test_ratio_ci_reproducible_given_seed(self):
        pairs = [(1, 2), (0, 2), (2, 2), (1, 2), (0, 2)]
        a = bootstrap_ratio_ci(pairs, n_boot=500, boot_seed=7)
        b = bootstrap_ratio_ci(pairs, n_boot=500, boot_seed=7)
        self.assertEqual(a, b)

    def test_ratio_ci_no_variance_when_all_records_identical(self):
        pairs = [(1, 1)] * 20  # every record scores 1/1 -> zero bootstrap variance
        ci = bootstrap_ratio_ci(pairs, n_boot=500)
        self.assertEqual(ci["point"], 1.0)
        self.assertEqual(ci["ci_lo"], 1.0)
        self.assertEqual(ci["ci_hi"], 1.0)

    def test_paired_diff_detects_a_real_difference(self):
        # A always scores 1/1, B always scores 0/1 on the SAME 20 records -> diff must be 1.0
        pairs_a = [(1, 1)] * 20
        pairs_b = [(0, 1)] * 20
        d = paired_bootstrap_diff(pairs_a, pairs_b, n_boot=500)
        self.assertAlmostEqual(d["diff"], 1.0)
        self.assertTrue(d["significant_at_0.05"])
        self.assertLess(d["p_value"], 0.05)

    def test_paired_diff_no_difference_when_identical(self):
        pairs = [(1, 1), (0, 1), (1, 1), (0, 1), (1, 1), (0, 1)] * 5
        d = paired_bootstrap_diff(pairs, pairs, n_boot=500)
        self.assertAlmostEqual(d["diff"], 0.0)
        self.assertFalse(d["significant_at_0.05"])

    def test_mismatched_lengths_raise(self):
        with self.assertRaises(ValueError):
            paired_bootstrap_diff([(1, 1)], [(1, 1), (0, 1)])


class StatisticalRigor(unittest.TestCase):
    """score_stats.py end-to-end: bootstrap CIs must reproduce the locked point estimates,
    correctly flag the two known frontier differences as significant, and a seed sweep
    must show the same ordering holds across independently generated corpora."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="statstest_")
        cls.corpus_path = os.path.join(cls.tmp, "c.json")
        r = run("generate_corpus.py", "--n", str(N), "--seed", str(SEED),
                "--population", str(POP), "--inference", "--utility",
                "--out", cls.corpus_path, "--population-out", os.path.join(cls.tmp, "population.jsonl"))
        assert r.returncode == 0, r.stderr
        cls.corpus = load(cls.corpus_path)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_bootstrap_point_estimates_match_locked_numbers(self):
        report = score_stats.bootstrap_report(
            self.corpus, self.corpus_path, list(REGISTRY), "signature-match-v0",
            None, n_boot=500, boot_seed=12345)
        self.assertAlmostEqual(report["privacy"]["regex-baseline-v0"]["point"], 0.449, places=3)
        self.assertAlmostEqual(report["privacy"]["over-redact-v0"]["point"], 0.900, places=3)
        self.assertAlmostEqual(report["utility"]["regex-baseline-v0"]["point"], 1.000, places=3)
        self.assertAlmostEqual(report["utility"]["over-redact-v0"]["point"], 0.600, places=3)
        self.assertAlmostEqual(report["inference_recovery"]["point"], 0.94, places=3)
        self.assertAlmostEqual(report["crosstrack_vulnerable"]["point"], 0.98, places=3)  # 49/50

    def test_frontier_differences_are_statistically_significant(self):
        report = score_stats.bootstrap_report(
            self.corpus, self.corpus_path, list(REGISTRY), "signature-match-v0",
            None, n_boot=500, boot_seed=12345)
        self.assertTrue(report["privacy_diff"]["significant_at_0.05"],
                        "over-redact's higher privacy vs baseline should be significant at n=50")
        self.assertTrue(report["utility_diff"]["significant_at_0.05"],
                        "over-redact's lower utility vs baseline should be significant at n=50")
        # direction: over-redact (pipeline B, second in REGISTRY) has HIGHER privacy,
        # so privacy_diff (A - B) must be negative; and LOWER utility, so utility_diff positive.
        self.assertLess(report["privacy_diff"]["diff"], 0)
        self.assertGreater(report["utility_diff"]["diff"], 0)

    def test_bootstrap_reproducible(self):
        r1 = score_stats.bootstrap_report(self.corpus, self.corpus_path, list(REGISTRY),
                                          "signature-match-v0", None, n_boot=300, boot_seed=1)
        r2 = score_stats.bootstrap_report(self.corpus, self.corpus_path, list(REGISTRY),
                                          "signature-match-v0", None, n_boot=300, boot_seed=1)
        self.assertEqual(r1, r2)

    def test_seed_sweep_confirms_frontier_ordering_holds_across_corpora(self):
        seeds = [SEED + i * 100000 for i in range(3)]
        report = score_stats.seed_sweep_report(
            N, seeds, list(REGISTRY), "signature-match-v0",
            with_population=20000, with_inference=True, with_utility=True)
        for row in report["per_seed"]:
            self.assertLess(row["privacy::regex-baseline-v0"], row["privacy::over-redact-v0"],
                            "baseline must be lower-privacy than over-redact on EVERY seed")
            self.assertGreater(row["utility::regex-baseline-v0"], row["utility::over-redact-v0"],
                               "baseline must be higher-utility than over-redact on EVERY seed")


class PopulationPathResolution(unittest.TestCase):
    """Regression for a real bug found during a real MIMIC-IV-Note-scale dry run
    (issue #126, Tier 5) -- not constructed. resolve_population_path used to check
    the CALLER's cwd for population_ref's bare filename before falling back to the
    corpus's own directory. population.jsonl is this tool's own default output
    filename, so a leftover from an earlier run sitting in cwd got silently
    substituted for the population that actually matches the corpus under test --
    corrupting Track 2's journalist/marketer numbers with no error or warning. This
    went undetected by the existing HeadlineNumbers/StatisticalRigor tests only
    because their fixture and the stray file happened to both use POP=100000."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="popres_")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def test_population_ref_resolves_next_to_corpus_not_cwd(self):
        corpus_dir = os.path.join(self.tmp, "corpus_dir")
        cwd_dir = os.path.join(self.tmp, "cwd_dir")
        os.makedirs(corpus_dir); os.makedirs(cwd_dir)
        real_pop = os.path.join(corpus_dir, "population.jsonl")
        with open(real_pop, "w") as f:
            f.write('{"age": 40}\n')
        # A same-named DECOY in some other directory (e.g. wherever the caller's cwd is).
        with open(os.path.join(cwd_dir, "population.jsonl"), "w") as f:
            f.write('{"age": 1}\n{"age": 2}\n{"age": 3}\n')
        corpus = {"population_ref": "population.jsonl"}
        corpus_path = os.path.join(corpus_dir, "c.json")
        old_cwd = os.getcwd()
        os.chdir(cwd_dir)
        try:
            resolved = resolve_population_path(None, corpus, corpus_path)
        finally:
            os.chdir(old_cwd)
        self.assertEqual(os.path.abspath(resolved), os.path.abspath(real_pop),
                         "population_ref must resolve next to the corpus, never via cwd")

    def test_explicit_population_flag_bypasses_population_ref(self):
        p = os.path.join(self.tmp, "explicit.jsonl")
        with open(p, "w") as f:
            f.write('{"age": 1}\n')
        resolved = resolve_population_path(p, {"population_ref": "unrelated.jsonl"}, "/nonexistent/c.json")
        self.assertEqual(resolved, p)

    def test_missing_explicit_population_flag_raises(self):
        with self.assertRaises(SystemExit):
            resolve_population_path(os.path.join(self.tmp, "nope.jsonl"), {}, "/nonexistent/c.json")

    def test_missing_population_ref_file_raises(self):
        corpus_path = os.path.join(self.tmp, "c.json")
        with self.assertRaises(SystemExit):
            resolve_population_path(None, {"population_ref": "nope.jsonl"}, corpus_path)

    def test_no_population_ref_and_no_flag_raises(self):
        with self.assertRaises(SystemExit):
            resolve_population_path(None, {}, "/nonexistent/c.json")

    def test_end_to_end_score_reid_ignores_cwd_decoy(self):
        """Full CLI reproduction: generate a corpus/population pair, then run
        score_reid.py from HERE (the scripts dir `run()` uses as cwd) -- exactly the
        directory the dry run hit this in -- and confirm the corpus's OWN population
        is scored regardless of whatever population.jsonl (if any) already sits there."""
        corpus_path = os.path.join(self.tmp, "c.json")
        r = run("generate_corpus.py", "--n", "10", "--seed", str(SEED),
                "--population", "40", "--out", corpus_path,
                "--population-out", os.path.join(self.tmp, "population.jsonl"))
        assert r.returncode == 0, r.stderr
        out = os.path.join(self.tmp, "reid.json")
        r = run("score_reid.py", "--corpus", corpus_path, "--out", out)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(load(out)["population_size"], 40)


class LargeCorpusBootstrapHint(unittest.TestCase):
    """A real, measured finding (issue #126, Tier 5): score_stats.py's default
    n_boot=2000 cost ~88 minutes at real MIMIC-IV-Note scale (331,794 records, full
    metric battery) for CIs that n_boot=200 reproduced identically to 3 decimal places
    in ~10 minutes. Too large a corpus to regression-test at real scale, so this locks
    the CLI-level hint that fires instead, using a lowered threshold to stay fast."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="hinttest_")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.corpus_path = os.path.join(self.tmp, "c.json")
        r = run("generate_corpus.py", "--n", str(N), "--seed", str(SEED), "--out", self.corpus_path)
        assert r.returncode == 0, r.stderr

    def _run_main_capture_stderr(self, extra_args):
        old_argv = sys.argv
        sys.argv = (["score_stats.py", "--corpus", self.corpus_path,
                     "--out", os.path.join(self.tmp, "out.json")] + extra_args)
        err, out = io.StringIO(), io.StringIO()
        try:
            with contextlib.redirect_stderr(err), contextlib.redirect_stdout(out):
                score_stats.main()
        finally:
            sys.argv = old_argv
        return err.getvalue()

    def test_hint_fires_at_default_n_boot_over_threshold(self):
        old = score_stats.LARGE_CORPUS_HINT_THRESHOLD
        score_stats.LARGE_CORPUS_HINT_THRESHOLD = 10  # N=50 fixture is "large" for this test
        try:
            err = self._run_main_capture_stderr(["--n-boot", str(score_stats.DEFAULT_N_BOOT)])
        finally:
            score_stats.LARGE_CORPUS_HINT_THRESHOLD = old
        self.assertIn("--n-boot 200", err)

    def test_hint_silent_below_threshold(self):
        # Default LARGE_CORPUS_HINT_THRESHOLD (50000) is far above the N=50 fixture.
        err = self._run_main_capture_stderr(["--n-boot", str(score_stats.DEFAULT_N_BOOT)])
        self.assertNotIn("--n-boot 200", err)

    def test_hint_silent_when_n_boot_already_overridden(self):
        # Above threshold but the caller already chose a non-default n_boot -- no nag.
        old = score_stats.LARGE_CORPUS_HINT_THRESHOLD
        score_stats.LARGE_CORPUS_HINT_THRESHOLD = 10
        try:
            err = self._run_main_capture_stderr(["--n-boot", "50"])
        finally:
            score_stats.LARGE_CORPUS_HINT_THRESHOLD = old
        self.assertNotIn("--n-boot 200", err)


if __name__ == "__main__":
    unittest.main(verbosity=2)
