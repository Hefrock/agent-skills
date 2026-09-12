// Unit tests for the pure Crossref/PubMed classifiers in decay.ts, against
// fixture response bodies — no network. These fixtures were built from
// Crossref's documented CrossMark `update-to` schema and PubMed's documented
// PublicationType/CommentsCorrections fields, NOT captured from a live
// retracted-DOI or retracted-PMID response — see the caveat in decay.ts.
// Run with `node decay-classify.test.mjs` (stdlib only, no framework).

import { classifyCrossrefWork, classifyPubmedXml } from "../dist/decay.js";

let passed = 0, failed = 0;
function check(label, condition, detail = "") {
  if (condition) {
    passed++;
  } else {
    failed++;
    console.log(`  FAIL  ${label}${detail ? ` — ${detail}` : ""}`);
    return;
  }
  console.log(`  PASS  ${label}`);
}

console.log("── classifyCrossrefWork ──────────────────────────────────────");

check(
  "active — no update-to, no relation",
  classifyCrossrefWork({ message: { DOI: "10.1000/xyz1", title: ["A Paper"] } }).status === "active"
);

check(
  "retracted — update-to type: retraction",
  classifyCrossrefWork({
    message: { DOI: "10.1000/xyz2", "update-to": [{ DOI: "10.1000/notice", type: "retraction" }] },
  }).status === "retracted"
);

check(
  "retracted — update-to type: removal",
  classifyCrossrefWork({ message: { "update-to": [{ type: "removal" }] } }).status === "retracted"
);

check(
  "updated — update-to type: erratum",
  classifyCrossrefWork({ message: { "update-to": [{ type: "erratum" }] } }).status === "updated"
);

check(
  "updated — update-to type: correction",
  classifyCrossrefWork({ message: { "update-to": [{ type: "correction" }] } }).status === "updated"
);

check(
  "retracted — relation is-retracted-by (no update-to)",
  classifyCrossrefWork({
    message: { relation: { "is-retracted-by": [{ "id-type": "doi", id: "10.1000/notice" }] } },
  }).status === "retracted"
);

check(
  "retraction wins over a simultaneous correction entry",
  classifyCrossrefWork({
    message: { "update-to": [{ type: "erratum" }, { type: "retraction" }] },
  }).status === "retracted"
);

check(
  "unreachable — no message field at all",
  classifyCrossrefWork({ status: "not-found" }).status === "unreachable"
);

console.log("\n── classifyPubmedXml ─────────────────────────────────────────");

check(
  "active — ordinary journal article",
  classifyPubmedXml(
    `<PubmedArticleSet><PubmedArticle><MedlineCitation><Article>
      <PublicationTypeList><PublicationType>Journal Article</PublicationType></PublicationTypeList>
    </Article></MedlineCitation></PubmedArticle></PubmedArticleSet>`
  ).status === "active"
);

check(
  "retracted — PublicationType: Retracted Publication",
  classifyPubmedXml(
    `<PubmedArticleSet><PubmedArticle><MedlineCitation><Article>
      <PublicationTypeList><PublicationType>Journal Article</PublicationType><PublicationType>Retracted Publication</PublicationType></PublicationTypeList>
    </Article></MedlineCitation></PubmedArticle></PubmedArticleSet>`
  ).status === "retracted"
);

check(
  "retracted — CommentsCorrections RefType=RetractionIn",
  classifyPubmedXml(
    `<PubmedArticleSet><PubmedArticle><MedlineCitation>
      <CommentsCorrectionsList><CommentsCorrections RefType="RetractionIn"><RefSource>J Something. 2022</RefSource></CommentsCorrections></CommentsCorrectionsList>
    </MedlineCitation></PubmedArticle></PubmedArticleSet>`
  ).status === "retracted"
);

check(
  "updated — CommentsCorrections RefType=ErratumIn",
  classifyPubmedXml(
    `<PubmedArticleSet><PubmedArticle><MedlineCitation>
      <CommentsCorrectionsList><CommentsCorrections RefType="ErratumIn"><RefSource>...</RefSource></CommentsCorrections></CommentsCorrectionsList>
    </MedlineCitation></PubmedArticle></PubmedArticleSet>`
  ).status === "updated"
);

check(
  "updated — CommentsCorrections RefType=CorrectionIn",
  classifyPubmedXml(`<CommentsCorrections RefType="CorrectionIn"><RefSource>x</RefSource></CommentsCorrections>`).status === "updated"
);

check(
  "unreachable — no PubmedArticle element (e.g. bad PMID)",
  classifyPubmedXml(`<PubmedArticleSet></PubmedArticleSet>`).status === "unreachable"
);

check(
  "retraction takes precedence when both signals present",
  classifyPubmedXml(
    `<PubmedArticleSet><PubmedArticle><MedlineCitation><Article>
      <PublicationTypeList><PublicationType>Retracted Publication</PublicationType></PublicationTypeList>
    </Article>
    <CommentsCorrectionsList><CommentsCorrections RefType="ErratumIn"><RefSource>x</RefSource></CommentsCorrections></CommentsCorrectionsList>
    </MedlineCitation></PubmedArticle></PubmedArticleSet>`
  ).status === "retracted"
);

console.log(`\n${"─".repeat(62)}\n  ${passed + failed} tests  —  ${passed} passed  —  ${failed} failed`);
if (failed > 0) process.exit(1);
