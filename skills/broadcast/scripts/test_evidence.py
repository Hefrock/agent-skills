#!/usr/bin/env python3
"""
Tests for evidence.py's pin_evidence_for_stories() — drives the REAL
compiled evidence-pinning-mcp server, same as
test_evidence_pinning_client.py (no mocks; register_source/pin_claim
don't touch the network — only check_source_decay does — so this is a
genuine end-to-end test of the orchestration logic, not a compromise for
testability).

Skipped (not failed) if the server isn't built — see
test_evidence_pinning_client.py's docstring for why.

Run: python test_evidence.py"""

import importlib.util
import os
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SERVER_PATH = os.path.join(HERE, "..", "..", "..", "mcp", "evidence-pinning", "dist", "index.js")


def load(name):
    # Registers into sys.modules BEFORE exec_module — required here because
    # evidence.py does its own plain `import evidence_pinning_client`
    # internally. Without this, that import wouldn't find this
    # already-loaded copy in sys.modules and would load a second,
    # independent copy from disk instead — same module, but a DIFFERENT
    # EvidencePinningError class object, so evidence.py's own
    # `except EvidencePinningError` would silently fail to match an
    # exception raised by a client built from *this* module and every
    # error would propagate uncaught instead of landing in "failed".
    # Confirmed live: this exact failure happened before sys.modules
    # registration was added here.
    spec = importlib.util.spec_from_file_location(name, os.path.join(HERE, f"{name}.py"))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


evidence_pinning_client = load("evidence_pinning_client")
evidence = load("evidence")


def make_item(title="A Title", url="https://example.com/x", id_hint=None, summary="A summary."):
    return {
        "source_key": "stat_news",
        "title": title,
        "url": url,
        "id_hint": id_hint,
        "published_date": "2026-09-01",
        "summary": summary,
    }


@unittest.skipUnless(
    os.path.exists(SERVER_PATH),
    f"evidence-pinning-mcp not built — run `npm run build` in mcp/evidence-pinning ({SERVER_PATH} not found)",
)
class PinEvidenceForStories(unittest.TestCase):
    def setUp(self):
        self.store_dir = tempfile.mkdtemp(prefix="evidence-test-")
        self.client = evidence_pinning_client.EvidencePinningClient(store_path=self.store_dir, server_path=SERVER_PATH)
        self.client.start()

    def tearDown(self):
        self.client.close()
        shutil.rmtree(self.store_dir, ignore_errors=True)

    def test_pins_a_claim_per_item(self):
        items = [make_item(title="Story A", url="https://a.com/1"), make_item(title="Story B", url="https://b.com/1")]
        result = evidence.pin_evidence_for_stories(self.client, items, run_id="2026-09-01")
        self.assertEqual(len(result["pinned"]), 2)
        self.assertEqual(result["skipped_no_summary"], [])
        self.assertEqual(result["failed"], [])

    def test_pinned_entries_carry_source_id_and_claim_id(self):
        items = [make_item(title="Story A", url="https://doi.org/10.1000/xyz1")]
        result = evidence.pin_evidence_for_stories(self.client, items, run_id="2026-09-01")
        self.assertEqual(result["pinned"][0]["source_id"], "doi:10.1000/xyz1")
        self.assertTrue(result["pinned"][0]["claim_id"])

    def test_claim_text_is_the_item_title_and_excerpt_is_the_summary(self):
        items = [make_item(title="Exact Title Text", url="https://a.com/1", summary="Exact summary text.")]
        result = evidence.pin_evidence_for_stories(self.client, items, run_id="2026-09-01")
        claim = self.client.verify_claim(result["pinned"][0]["claim_id"])
        self.assertEqual(claim["text"], "Exact Title Text")
        self.assertEqual(claim["excerpt"], "Exact summary text.")

    def test_items_with_no_summary_are_skipped_not_failed(self):
        items = [make_item(title="No summary", url="https://a.com/1", summary="")]
        result = evidence.pin_evidence_for_stories(self.client, items, run_id="2026-09-01")
        self.assertEqual(len(result["skipped_no_summary"]), 1)
        self.assertEqual(result["pinned"], [])
        self.assertEqual(result["failed"], [])

    def test_id_hint_is_passed_through_to_register_source(self):
        items = [make_item(title="Story A", url="https://example.com/report", id_hint="doi:10.2000/forced")]
        result = evidence.pin_evidence_for_stories(self.client, items, run_id="2026-09-01")
        self.assertEqual(result["pinned"][0]["source_id"], "doi:10.2000/forced")

    def test_two_items_sharing_a_url_share_one_source_but_get_separate_claims(self):
        items = [
            make_item(title="Headline from outlet A", url="https://doi.org/10.1000/xyz1"),
            make_item(title="Headline from outlet B", url="https://doi.org/10.1000/xyz1"),
        ]
        result = evidence.pin_evidence_for_stories(self.client, items, run_id="2026-09-01")
        self.assertEqual(len(result["pinned"]), 2)
        self.assertEqual(result["pinned"][0]["source_id"], result["pinned"][1]["source_id"])
        self.assertNotEqual(result["pinned"][0]["claim_id"], result["pinned"][1]["claim_id"])

    def test_repinning_the_same_run_and_items_is_idempotent(self):
        items = [make_item(title="Story A", url="https://a.com/1")]
        first = evidence.pin_evidence_for_stories(self.client, items, run_id="2026-09-01")
        second = evidence.pin_evidence_for_stories(self.client, items, run_id="2026-09-01")
        self.assertEqual(first["pinned"][0]["claim_id"], second["pinned"][0]["claim_id"])

    def test_pinned_claims_are_retrievable_via_get_claims_for_the_run(self):
        items = [make_item(title="Story A", url="https://a.com/1"), make_item(title="Story B", url="https://b.com/1")]
        evidence.pin_evidence_for_stories(self.client, items, run_id="2026-09-01")
        claims = self.client.get_claims("2026-09-01")
        self.assertEqual(claims["total"], 2)

    def test_empty_items_list_returns_empty_result(self):
        result = evidence.pin_evidence_for_stories(self.client, [], run_id="2026-09-01")
        self.assertEqual(result, {"pinned": [], "skipped_no_summary": [], "failed": []})

    def test_a_failing_item_is_recorded_not_aborting_the_rest_of_the_batch(self):
        # An empty title makes register_source's own required-string check
        # reject it server-side — confirms the try/except in
        # pin_evidence_for_stories catches a real server-side rejection
        # (not just a hypothetical one) and keeps processing the rest of
        # the batch rather than raising out of the whole function.
        items = [
            make_item(title="", url="https://a.com/1", summary="Has a summary, so it isn't skipped for that reason."),
            make_item(title="Story B", url="https://b.com/1"),
        ]
        result = evidence.pin_evidence_for_stories(self.client, items, run_id="2026-09-01")
        self.assertEqual(len(result["failed"]), 1)
        self.assertEqual(result["failed"][0]["item"]["url"], "https://a.com/1")
        self.assertEqual(len(result["pinned"]), 1)
        self.assertEqual(result["pinned"][0]["item"]["title"], "Story B")


if __name__ == "__main__":
    unittest.main()
