#!/usr/bin/env python3
"""
Unit and end-to-end tests for human_score.py: blind, resumable human scoring.

Covers ordering (near-threshold first, balanced interleave), blinding (the
queue holds only the shown fields, ids reveal nothing), the refuse-to-
reshuffle guard, interactive scoring through real subprocess stdin
(including quit-and-resume and 'b' re-score), latest-wins export, and the
real contract: the exported file is read by calibrate_judge.py.

Stdlib only (unittest). Run: python test_human_score.py"""

import importlib.util
import json
import os
import random
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, "human_score.py")
CALIBRATE = os.path.join(HERE, "calibrate_judge.py")

spec = importlib.util.spec_from_file_location("human_score", SCRIPT)
hs = importlib.util.module_from_spec(spec)
spec.loader.exec_module(hs)


def write_jsonl(path, rows):
    with open(path, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")


def read_jsonl(path):
    with open(path, encoding="utf-8") as f:
        return [json.loads(l) for l in f if l.strip()]


def read_text(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def run(*args, stdin=""):
    return subprocess.run([sys.executable, SCRIPT, *args], input=stdin, capture_output=True, text=True)


class Fixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = self.tmp.name
        self.cases = os.path.join(self.dir, "cases.jsonl")
        self.judge = os.path.join(self.dir, "judge.jsonl")
        self.work = os.path.join(self.dir, "work")
        cats = ["accuracy", "format"]
        cases, judge = [], []
        scores = [0.95, 0.71, 0.69, 0.2, 0.9, 0.5, 0.75, 0.3]
        for i, s in enumerate(scores):
            cid = f"case_{i}"
            cases.append({"id": cid, "input": f"in {i}", "output": f"out {i}", "version": "SECRET_V2"})
            judge.append({"id": cid, "score": s, "category": cats[i % 2], "rationale": "SECRET_RATIONALE"})
        write_jsonl(self.cases, cases)
        write_jsonl(self.judge, judge)
        self.n = len(cases)

    def tearDown(self):
        self.tmp.cleanup()

    def prepare(self, *extra):
        return run("prepare", self.cases, self.judge, "--workdir", self.work, "--seed", "1", "--priority-n", "3", *extra)


class TestOrdering(unittest.TestCase):
    def test_near_threshold_first(self):
        rows = [{"id": "a", "score": 0.99, "category": "x"}, {"id": "b", "score": 0.70, "category": "x"},
                {"id": "c", "score": 0.72, "category": "x"}, {"id": "d", "score": 0.05, "category": "x"}]
        order = hs.order_ids(rows, 0.7, 2, random.Random(0))
        self.assertEqual(set(order[:2]), {"b", "c"})
        self.assertEqual(len(order), 4)

    def test_rest_interleaved_across_categories(self):
        rows = [{"id": f"a{i}", "score": 0.0, "category": "A"} for i in range(4)] + \
               [{"id": f"b{i}", "score": 0.0, "category": "B"} for i in range(4)]
        order = hs.order_ids(rows, 0.7, 0, random.Random(0))
        cats = [o[0] for o in order]
        self.assertTrue(all(cats[i] != cats[i + 1] for i in range(len(cats) - 1)), cats)

    def test_blind_ids_unique_and_opaque(self):
        ids = hs.new_blind_ids(50, random.Random(0))
        self.assertEqual(len(set(ids)), 50)
        self.assertNotEqual(ids, sorted(ids))


class TestParse(unittest.TestCase):
    def test_valid_and_invalid(self):
        self.assertEqual(hs.parse_score("0.8"), (0.8, ""))
        self.assertEqual(hs.parse_score("0.3 wrong fix"), (0.3, "wrong fix"))
        self.assertEqual(hs.parse_score("1"), (1.0, ""))
        for bad in ["", "abc", "1.5", "-0.1", "nan"]:
            self.assertIsNone(hs.parse_score(bad), bad)


class TestPrepare(Fixture):
    def test_queue_blinded(self):
        r = self.prepare()
        self.assertEqual(r.returncode, 0, r.stderr)
        raw = read_text(os.path.join(self.work, "queue.jsonl"))
        for leak in ["SECRET_V2", "SECRET_RATIONALE", "case_", "accuracy", "format", "score"]:
            self.assertNotIn(leak, raw, leak)
        queue = read_jsonl(os.path.join(self.work, "queue.jsonl"))
        self.assertEqual(len(queue), self.n)
        self.assertEqual(set(queue[0]), {"blind_id", "input", "output"})

    def test_refuses_overwrite_without_force(self):
        self.assertEqual(self.prepare().returncode, 0)
        before = read_text(os.path.join(self.work, "queue.jsonl"))
        r = self.prepare()
        self.assertEqual(r.returncode, 2)
        self.assertEqual(before, read_text(os.path.join(self.work, "queue.jsonl")))

    def test_unmatched_ids_warn_not_crash(self):
        write_jsonl(self.judge, read_jsonl(self.judge) + [{"id": "ghost", "score": 0.7, "category": "x"}])
        r = self.prepare()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("ghost", r.stderr)

    def test_no_matching_show_fields_refuses(self):
        r = self.prepare("--show-fields", "nonexistent")
        self.assertEqual(r.returncode, 2)


class TestScoreAndResume(Fixture):
    def test_quit_then_resume_and_export_with_calibrate(self):
        self.assertEqual(self.prepare().returncode, 0)
        # session 1: score 3 items, then quit
        r1 = run("score", "--workdir", self.work, stdin="0.9\n0.4 meh\n0.8\nq\n")
        self.assertEqual(r1.returncode, 0, r1.stderr)
        self.assertIn("3 of 8", r1.stdout)
        st = run("status", "--workdir", self.work)
        self.assertIn("3 of 8 scored", st.stdout)
        # session 2 resumes at item 4 and does not re-show scored items
        r2 = run("score", "--workdir", self.work, stdin="0.1\n0.2\n0.3\n0.4\n0.5\n")
        self.assertIn("Item 4 of 8", r2.stdout)
        self.assertIn("All 8 items scored", r2.stdout)
        scores = read_jsonl(os.path.join(self.work, "scores.jsonl"))
        self.assertEqual(len(scores), 8)
        self.assertEqual(len({s["blind_id"] for s in scores}), 8)
        # export + the real downstream consumer
        out = os.path.join(self.dir, "human.jsonl")
        ex = run("export", "--workdir", self.work, "--out", out)
        self.assertEqual(ex.returncode, 0, ex.stderr)
        rows = read_jsonl(out)
        self.assertEqual({r["id"] for r in rows}, {f"case_{i}" for i in range(8)})
        self.assertTrue(all(set(r) == {"id", "score", "category", "rationale"} for r in rows))
        cal = subprocess.run([sys.executable, CALIBRATE, self.judge, out], capture_output=True, text=True)
        self.assertIn(cal.returncode, (0, 1), cal.stderr)
        self.assertNotIn("Traceback", cal.stderr)
        self.assertIn("8", cal.stdout)

    def test_invalid_input_reprompts_and_writes_nothing(self):
        self.assertEqual(self.prepare().returncode, 0)
        r = run("score", "--workdir", self.work, stdin="abc\n2\n\nq\n")
        self.assertEqual(r.returncode, 0)
        self.assertFalse(os.path.exists(os.path.join(self.work, "scores.jsonl")))

    def test_back_rescore_latest_wins(self):
        self.assertEqual(self.prepare().returncode, 0)
        run("score", "--workdir", self.work, stdin="0.2\nb\n0.9 changed my mind\nq\n")
        scores = read_jsonl(os.path.join(self.work, "scores.jsonl"))
        self.assertEqual([s["score"] for s in scores], [0.2, 0.9])
        out = os.path.join(self.dir, "human.jsonl")
        run("export", "--workdir", self.work, "--out", out)
        rows = read_jsonl(out)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["score"], 0.9)
        self.assertEqual(rows[0]["rationale"], "changed my mind")

    def test_eof_mid_session_loses_nothing(self):
        self.assertEqual(self.prepare().returncode, 0)
        r = run("score", "--workdir", self.work, stdin="0.6\n0.7\n")
        self.assertEqual(r.returncode, 0)
        self.assertEqual(len(read_jsonl(os.path.join(self.work, "scores.jsonl"))), 2)

    def test_status_never_prints_scores_or_key(self):
        self.assertEqual(self.prepare().returncode, 0)
        run("score", "--workdir", self.work, stdin="0.55\nq\n")
        st = run("status", "--workdir", self.work)
        for leak in ["0.55", "case_", "accuracy"]:
            self.assertNotIn(leak, st.stdout)

    def test_limit_reached_logic(self):
        self.assertFalse(hs.limit_reached(None, 10**9))   # --minutes 0 = no limit
        self.assertFalse(hs.limit_reached(100.0, 99.0))
        self.assertTrue(hs.limit_reached(100.0, 101.0))

    def test_minutes_zero_means_no_limit(self):
        self.assertEqual(self.prepare().returncode, 0)
        r = run("score", "--workdir", self.work, "--minutes", "0", stdin="0.5\nq\n")
        self.assertEqual(r.returncode, 0)
        self.assertEqual(len(read_jsonl(os.path.join(self.work, "scores.jsonl"))), 1)


class TestExtremeGuard(Fixture):
    def scores(self):
        path = os.path.join(self.work, "scores.jsonl")
        return [s["score"] for s in read_jsonl(path)] if os.path.exists(path) else []

    def test_enter_keeps_extreme(self):
        self.assertEqual(self.prepare().returncode, 0)
        r = run("score", "--workdir", self.work, stdin="0.0\n\n1.0 great\ny\nq\n")
        self.assertIn("extreme score", r.stdout)
        self.assertEqual(self.scores(), [0.0, 1.0])

    def test_replacement_typed_at_confirm_wins(self):
        self.assertEqual(self.prepare().returncode, 0)
        run("score", "--workdir", self.work, stdin="0.0\n0.9\n0.5\nq\n")
        self.assertEqual(self.scores(), [0.9, 0.5])

    def test_n_discards_and_reprompts_same_item(self):
        self.assertEqual(self.prepare().returncode, 0)
        r = run("score", "--workdir", self.work, stdin="1.0\nn\n0.7\nq\n")
        self.assertIn("Not saved", r.stdout)
        self.assertIn("Item 1 of", r.stdout)
        self.assertEqual(self.scores(), [0.7])

    def test_eof_at_confirm_saves_nothing(self):
        self.assertEqual(self.prepare().returncode, 0)
        r = run("score", "--workdir", self.work, stdin="0.0\n")
        self.assertEqual(r.returncode, 0)
        self.assertEqual(self.scores(), [])

    def test_invalid_reply_at_confirm_reprompts(self):
        self.assertEqual(self.prepare().returncode, 0)
        run("score", "--workdir", self.work, stdin="0.0\nabc\n7\n\nq\n")
        self.assertEqual(self.scores(), [0.0])

    def test_non_extreme_scores_are_not_prompted(self):
        self.assertEqual(self.prepare().returncode, 0)
        r = run("score", "--workdir", self.work, stdin="0.1\n0.99\nq\n")
        self.assertNotIn("extreme score", r.stdout)
        self.assertEqual(self.scores(), [0.1, 0.99])

    def test_no_confirm_flag_skips_guard(self):
        self.assertEqual(self.prepare().returncode, 0)
        r = run("score", "--workdir", self.work, "--no-confirm-extremes", stdin="0.0\n1.0\nq\n")
        self.assertNotIn("extreme score", r.stdout)
        self.assertEqual(self.scores(), [0.0, 1.0])

    def test_back_rescore_extreme_is_confirmed_too(self):
        self.assertEqual(self.prepare().returncode, 0)
        run("score", "--workdir", self.work, stdin="0.4\nb\n0.0\nn\nq\n")
        self.assertEqual(self.scores(), [0.4])
        run("score", "--workdir", self.work, stdin="b\n0.0\n\nq\n")
        self.assertEqual(self.scores(), [0.4, 0.0])

    def test_rescore_extreme_is_confirmed_and_flagged_revised(self):
        self.assertEqual(self.prepare().returncode, 0)
        run("score", "--workdir", self.work, stdin="0.3\n0.4\nq\n")
        bid = read_jsonl(os.path.join(self.work, "queue.jsonl"))[0]["blind_id"]
        r = run("rescore", "--workdir", self.work, "--ids", bid, stdin="0.0\nn\n\n")
        self.assertIn("Not saved", r.stdout)
        self.assertEqual(self.scores(), [0.3, 0.4])
        run("rescore", "--workdir", self.work, "--ids", bid, stdin="1.0\ny\n")
        rows = read_jsonl(os.path.join(self.work, "scores.jsonl"))
        self.assertEqual(rows[-1]["score"], 1.0)
        self.assertTrue(rows[-1]["revised"])

    def test_confirm_extreme_unit(self):
        answers = iter(["", "n", "0.4 fixed"])
        self.assertEqual(hs.confirm_extreme((0.0, "n1"), ask=lambda p: next(answers)), (0.0, "n1"))
        self.assertIsNone(hs.confirm_extreme((1.0, ""), ask=lambda p: next(answers)))
        self.assertEqual(hs.confirm_extreme((0.0, ""), ask=lambda p: next(answers)), (0.4, "fixed"))
        self.assertEqual(hs.confirm_extreme((0.5, "x"), ask=lambda p: self.fail("asked")), (0.5, "x"))


class TestRescore(Fixture):
    def scored_workdir(self):
        self.assertEqual(self.prepare().returncode, 0)
        run("score", "--workdir", self.work, stdin="0.1 first\n0.2\n0.3\nq\n")
        return [q["blind_id"] for q in read_jsonl(os.path.join(self.work, "queue.jsonl"))][:3]

    def test_rescore_appends_revised_and_keeps_original(self):
        ids = self.scored_workdir()
        r = run("rescore", "--workdir", self.work, "--ids", ids[0], stdin="0.9 changed\n")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("Your previous score: 0.1", r.stdout)
        rows = read_jsonl(os.path.join(self.work, "scores.jsonl"))
        self.assertEqual(len(rows), 4)
        self.assertEqual(rows[-1]["blind_id"], ids[0])
        self.assertTrue(rows[-1]["revised"])
        self.assertNotIn("revised", rows[0])

    def test_export_latest_vs_first(self):
        ids = self.scored_workdir()
        run("rescore", "--workdir", self.work, "--ids", ids[0], stdin="0.9\n")
        latest = os.path.join(self.dir, "latest.jsonl")
        first = os.path.join(self.dir, "first.jsonl")
        self.assertEqual(run("export", "--workdir", self.work, "--out", latest).returncode, 0)
        self.assertEqual(run("export", "--workdir", self.work, "--out", first, "--which", "first").returncode, 0)
        with open(os.path.join(self.work, "key.json"), encoding="utf-8") as f:
            key = json.load(f)
        real = key[ids[0]]["id"]
        self.assertEqual({r["id"]: r["score"] for r in read_jsonl(latest)}[real], 0.9)
        self.assertEqual({r["id"]: r["score"] for r in read_jsonl(first)}[real], 0.1)

    def test_enter_keeps_and_writes_nothing(self):
        ids = self.scored_workdir()
        run("rescore", "--workdir", self.work, "--ids", ids[0], stdin="\n")
        self.assertEqual(len(read_jsonl(os.path.join(self.work, "scores.jsonl"))), 3)

    def test_invalid_then_valid_input(self):
        ids = self.scored_workdir()
        run("rescore", "--workdir", self.work, "--ids", ids[0], stdin="nope\n7\n0.5\n")
        rows = read_jsonl(os.path.join(self.work, "scores.jsonl"))
        self.assertEqual(rows[-1]["score"], 0.5)
        self.assertEqual(len(rows), 4)

    def test_refuses_unscored_or_unknown_ids(self):
        ids = self.scored_workdir()
        queue_ids = [q["blind_id"] for q in read_jsonl(os.path.join(self.work, "queue.jsonl"))]
        unscored = queue_ids[5]
        for bad in (unscored, "b000000"):
            r = run("rescore", "--workdir", self.work, "--ids", bad, stdin="0.5\n")
            self.assertEqual(r.returncode, 2)
        self.assertEqual(len(read_jsonl(os.path.join(self.work, "scores.jsonl"))), 3)

    def test_never_prints_judge_data(self):
        ids = self.scored_workdir()
        r = run("rescore", "--workdir", self.work, "--ids", ids[0], stdin="\n")
        for leak in ["SECRET_RATIONALE", "judge", "case_", "accuracy"]:
            self.assertNotIn(leak, r.stdout)


class TestExportGuards(Fixture):
    def test_export_without_scores_errors(self):
        self.assertEqual(self.prepare().returncode, 0)
        r = run("export", "--workdir", self.work, "--out", os.path.join(self.dir, "h.jsonl"))
        self.assertEqual(r.returncode, 2)

    def test_export_partial_ok(self):
        self.assertEqual(self.prepare().returncode, 0)
        run("score", "--workdir", self.work, stdin="0.5\n0.6\nq\n")
        out = os.path.join(self.dir, "h.jsonl")
        r = run("export", "--workdir", self.work, "--out", out)
        self.assertEqual(r.returncode, 0)
        self.assertEqual(len(read_jsonl(out)), 2)
        self.assertIn("Partial export", r.stdout)


if __name__ == "__main__":
    unittest.main()
