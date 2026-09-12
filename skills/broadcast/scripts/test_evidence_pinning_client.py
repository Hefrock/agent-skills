#!/usr/bin/env python3
"""
End-to-end tests for evidence_pinning_client.py — drives the REAL compiled
evidence-pinning-mcp server (mcp/evidence-pinning/dist/index.js) over its
actual stdio JSON-RPC transport. No mocks, mirroring what
mcp/evidence-pinning/test/server.test.mjs already does from the
TypeScript side — this is the same server, driven from Python instead.

Requires the server to already be built: `npm run build` in
mcp/evidence-pinning. Skipped (not failed) if dist/index.js doesn't
exist, so this suite doesn't break local test runs for anyone who hasn't
built the TS server — see .github/workflows/ci.yml's python-skills job
for where CI builds it before running this file.

check_source_decay is deliberately NOT covered here — it makes a real
network call (Crossref/PubMed/plain URL reachability), which this
sandbox's default-deny egress policy blocks, same as every other
live-network call in this pipeline. server.test.mjs already covers that
tool's real behavior from an environment with real egress; duplicating
it here would just be a second, weaker copy of the same test.

Run: python test_evidence_pinning_client.py"""

import importlib.util
import os
import shutil
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, "evidence_pinning_client.py")
SERVER_PATH = os.path.join(HERE, "..", "..", "..", "mcp", "evidence-pinning", "dist", "index.js")

spec = importlib.util.spec_from_file_location("evidence_pinning_client", SCRIPT)
evidence_pinning_client = importlib.util.module_from_spec(spec)
spec.loader.exec_module(evidence_pinning_client)


@unittest.skipUnless(
    os.path.exists(SERVER_PATH),
    f"evidence-pinning-mcp not built — run `npm run build` in mcp/evidence-pinning ({SERVER_PATH} not found)",
)
class EvidencePinningClientLive(unittest.TestCase):
    def setUp(self):
        self.store_dir = tempfile.mkdtemp(prefix="evidence-pinning-client-test-")
        self.client = evidence_pinning_client.EvidencePinningClient(store_path=self.store_dir, server_path=SERVER_PATH)
        self.client.start()

    def tearDown(self):
        self.client.close()
        shutil.rmtree(self.store_dir, ignore_errors=True)

    def test_register_source_extracts_doi_from_url(self):
        result = self.client.register_source("https://doi.org/10.1000/xyz1", "A Paper")
        self.assertEqual(result["source_id"], "doi:10.1000/xyz1")
        self.assertEqual(result["id_type"], "doi")
        self.assertTrue(result["is_new"])

    def test_register_source_is_idempotent(self):
        first = self.client.register_source("https://doi.org/10.1000/xyz1", "A Paper")
        second = self.client.register_source("https://doi.org/10.1000/xyz1", "A Paper (again)")
        self.assertFalse(second["is_new"])
        self.assertEqual(second["source_id"], first["source_id"])

    def test_register_source_extracts_pmid_from_url(self):
        result = self.client.register_source("https://pubmed.ncbi.nlm.nih.gov/12345678", "A PubMed Paper")
        self.assertEqual(result["source_id"], "pmid:12345678")

    def test_register_source_plain_url_falls_back_to_hash(self):
        result = self.client.register_source("https://statnews.com/some-article", "A Press Article")
        self.assertEqual(result["id_type"], "url")
        self.assertTrue(result["source_id"].startswith("url:"))

    def test_register_source_id_hint_forces_canonical_id(self):
        result = self.client.register_source("https://example.com/report", "Forced DOI", id_hint="doi:10.2000/forced")
        self.assertEqual(result["source_id"], "doi:10.2000/forced")

    def test_pin_claim_against_unregistered_source_raises(self):
        with self.assertRaises(evidence_pinning_client.EvidencePinningError):
            self.client.pin_claim("run1", "X reduces Y", ["doi:nonexistent"], "...")

    def test_pin_claim_with_zero_sources_raises(self):
        with self.assertRaises(evidence_pinning_client.EvidencePinningError):
            self.client.pin_claim("run1", "X reduces Y", [], "...")

    def test_pin_claim_succeeds_against_a_registered_source(self):
        source = self.client.register_source("https://doi.org/10.1000/xyz1", "A Paper")
        claim = self.client.pin_claim("run1", "X reduces Y by 40%", [source["source_id"]], "Study found a 40% reduction.")
        self.assertTrue(claim["is_new"])
        self.assertEqual(claim["status"], "pinned")

    def test_pin_claim_is_idempotent_per_run_and_text(self):
        source = self.client.register_source("https://doi.org/10.1000/xyz1", "A Paper")
        first = self.client.pin_claim("run1", "X reduces Y by 40%", [source["source_id"]], "excerpt")
        second = self.client.pin_claim("run1", "X reduces Y by 40%", [source["source_id"]], "excerpt")
        self.assertFalse(second["is_new"])
        self.assertEqual(second["claim_id"], first["claim_id"])

    def test_pin_claim_can_cite_multiple_sources(self):
        s1 = self.client.register_source("https://doi.org/10.1000/xyz1", "Paper 1")
        s2 = self.client.register_source("https://pubmed.ncbi.nlm.nih.gov/12345678", "Paper 2")
        claim = self.client.pin_claim("run1", "Z improves W", [s1["source_id"], s2["source_id"]], "...")
        self.assertTrue(claim["is_new"])

    def test_get_claims_returns_all_claims_for_a_run(self):
        source = self.client.register_source("https://doi.org/10.1000/xyz1", "A Paper")
        self.client.pin_claim("run1", "Claim A", [source["source_id"]], "excerpt A")
        self.client.pin_claim("run1", "Claim B", [source["source_id"]], "excerpt B")
        result = self.client.get_claims("run1")
        self.assertEqual(result["total"], 2)

    def test_get_claims_inlines_source_records(self):
        source = self.client.register_source("https://doi.org/10.1000/xyz1", "A Paper")
        self.client.pin_claim("run1", "Claim A", [source["source_id"]], "excerpt A")
        result = self.client.get_claims("run1")
        self.assertEqual(result["claims"][0]["sources"][0]["source_id"], source["source_id"])

    def test_get_claims_for_unknown_run_returns_empty_not_an_error(self):
        result = self.client.get_claims("run-with-nothing-pinned")
        self.assertEqual(result["total"], 0)

    def test_verify_claim_inlines_backing_sources(self):
        s1 = self.client.register_source("https://doi.org/10.1000/xyz1", "Paper 1")
        s2 = self.client.register_source("https://pubmed.ncbi.nlm.nih.gov/12345678", "Paper 2")
        claim = self.client.pin_claim("run1", "Z improves W", [s1["source_id"], s2["source_id"]], "...")
        result = self.client.verify_claim(claim["claim_id"])
        self.assertEqual(len(result["sources"]), 2)

    def test_verify_claim_on_unknown_id_raises(self):
        with self.assertRaises(evidence_pinning_client.EvidencePinningError):
            self.client.verify_claim("nonexistent")

    def test_flag_claim_marks_it_flagged(self):
        source = self.client.register_source("https://doi.org/10.1000/xyz1", "A Paper")
        claim = self.client.pin_claim("run1", "Claim A", [source["source_id"]], "excerpt A")
        result = self.client.flag_claim(claim["claim_id"], "Excerpt does not actually support this claim")
        self.assertEqual(result["status"], "flagged")

    def test_flagged_status_persists_on_subsequent_lookup(self):
        source = self.client.register_source("https://doi.org/10.1000/xyz1", "A Paper")
        claim = self.client.pin_claim("run1", "Claim A", [source["source_id"]], "excerpt A")
        self.client.flag_claim(claim["claim_id"], "bad excerpt")
        result = self.client.verify_claim(claim["claim_id"])
        self.assertEqual(result["status"], "flagged")
        self.assertIn("bad excerpt", result["flag_reason"])

    def test_provenance_log_records_pin_then_flag_in_order(self):
        source = self.client.register_source("https://doi.org/10.1000/xyz1", "A Paper")
        claim = self.client.pin_claim("run1", "Claim A", [source["source_id"]], "excerpt A")
        self.client.flag_claim(claim["claim_id"], "bad excerpt")
        result = self.client.get_provenance("claim", claim["claim_id"])
        actions = [e["action"] for e in result["events"]]
        self.assertEqual(actions[0], "pinned")
        self.assertIn("flagged", actions)

    def test_provenance_log_records_source_registration(self):
        source = self.client.register_source("https://doi.org/10.1000/xyz1", "A Paper")
        result = self.client.get_provenance("source", source["source_id"])
        self.assertTrue(any(e["action"] == "registered" for e in result["events"]))


if __name__ == "__main__":
    unittest.main()
