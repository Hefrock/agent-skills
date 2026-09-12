// Source-decay checking: does a pinned source still say what it said when it was
// pinned? Split into pure classifiers (fixture-testable, no network) and thin
// network wrappers that fetch live data and hand it to the classifiers — the split
// exists so decay-classify.test.mjs can cover the parsing logic against real
// captured API responses without needing a live retracted DOI/PMID to test against.

export type SourceStatus = "active" | "retracted" | "updated" | "unreachable";

export interface DecayCheckResult {
  status: SourceStatus;
  detail: string;
}

// ── Crossref (DOI) ───────────────────────────────────────────────────────────
//
// Classifies a Crossref `GET /works/{doi}` JSON response using the CrossMark
// `update-to` field, which Crossref's documented CrossMark schema uses for
// post-publication updates (type values include "retraction", "correction",
// "erratum", "clarification", "addendum", "removal", "partial_retraction").
// NOTE: this has been implemented from Crossref's published CrossMark schema,
// not verified against a live retracted-DOI response — if it misses a real
// retraction notice, check the actual JSON shape crossref returns for
// `message.update-to` and `message.relation` before trusting this blindly.
export function classifyCrossrefWork(json: unknown): DecayCheckResult {
  const message = (json as { message?: Record<string, unknown> })?.message;
  if (!message) {
    return { status: "unreachable", detail: "Crossref response had no `message` field" };
  }

  const updates = Array.isArray(message["update-to"]) ? (message["update-to"] as Array<Record<string, unknown>>) : [];
  const retractionTypes = new Set(["retraction", "removal", "partial_retraction"]);
  const correctionTypes = new Set(["correction", "erratum", "clarification", "addendum", "new_edition"]);

  const retraction = updates.find((u) => retractionTypes.has(String(u.type)));
  if (retraction) {
    return { status: "retracted", detail: `Crossref update-to: ${String(retraction.type)}` };
  }
  const correction = updates.find((u) => correctionTypes.has(String(u.type)));
  if (correction) {
    return { status: "updated", detail: `Crossref update-to: ${String(correction.type)}` };
  }

  // Secondary signal: an explicit relation asserting this work was retracted.
  const relation = message.relation as Record<string, unknown> | undefined;
  if (relation && Array.isArray(relation["is-retracted-by"]) && (relation["is-retracted-by"] as unknown[]).length > 0) {
    return { status: "retracted", detail: "Crossref relation: is-retracted-by" };
  }

  return { status: "active", detail: "No update-to or is-retracted-by relation found" };
}

// ── PubMed (PMID) ────────────────────────────────────────────────────────────
//
// Classifies a PubMed `efetch.fcgi?db=pubmed&id={pmid}&retmode=xml` response.
// Regex-based rather than a real XML parser (no XML dependency in this repo yet)
// — fragile against nested/escaped tag content, but PubMed's efetch output for
// these two fields is consistently flat enough in practice. If PubMed ever
// returns namespaced or attribute-heavy variants of these tags, this will miss
// them silently rather than error — a real limitation, not a corner case that
// was checked and ruled out.
export function classifyPubmedXml(xml: string): DecayCheckResult {
  if (/<PublicationType[^>]*>\s*Retracted Publication\s*<\/PublicationType>/i.test(xml)) {
    return { status: "retracted", detail: "PublicationType: Retracted Publication" };
  }
  if (/<CommentsCorrections[^>]*RefType="RetractionIn"/i.test(xml)) {
    return { status: "retracted", detail: "CommentsCorrections RefType: RetractionIn" };
  }
  if (/<CommentsCorrections[^>]*RefType="(ErratumIn|CorrectionIn|UpdateIn)"/i.test(xml)) {
    return { status: "updated", detail: "CommentsCorrections RefType: correction/update" };
  }
  if (!/<PubmedArticle[\s>]/.test(xml)) {
    return { status: "unreachable", detail: "No <PubmedArticle> element in efetch response" };
  }
  return { status: "active", detail: "No retraction/correction markers found" };
}

// ── Network wrappers ─────────────────────────────────────────────────────────

const FETCH_TIMEOUT_MS = 10_000;

async function fetchWithTimeout(url: string, init?: RequestInit): Promise<Response> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), FETCH_TIMEOUT_MS);
  try {
    return await fetch(url, { ...init, signal: controller.signal });
  } finally {
    clearTimeout(timer);
  }
}

export async function checkDoiStatus(doi: string): Promise<DecayCheckResult> {
  try {
    const res = await fetchWithTimeout(`https://api.crossref.org/works/${encodeURIComponent(doi)}`);
    if (!res.ok) {
      return { status: "unreachable", detail: `Crossref returned HTTP ${res.status}` };
    }
    return classifyCrossrefWork(await res.json());
  } catch (err) {
    return { status: "unreachable", detail: err instanceof Error ? err.message : String(err) };
  }
}

export async function checkPmidStatus(pmid: string): Promise<DecayCheckResult> {
  try {
    const url = `https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi?db=pubmed&id=${encodeURIComponent(pmid)}&retmode=xml`;
    const res = await fetchWithTimeout(url);
    if (!res.ok) {
      return { status: "unreachable", detail: `PubMed efetch returned HTTP ${res.status}` };
    }
    return classifyPubmedXml(await res.text());
  } catch (err) {
    return { status: "unreachable", detail: err instanceof Error ? err.message : String(err) };
  }
}

// Plain URL sources (no DOI/PMID): decay checking is just reachability — there is
// no general-purpose "was this page retracted" signal for arbitrary web content,
// so this deliberately only ever returns "active" or "unreachable", never
// "retracted"/"updated". Reflect that honestly rather than pretending otherwise.
export async function checkUrlReachable(url: string): Promise<DecayCheckResult> {
  try {
    let res = await fetchWithTimeout(url, { method: "HEAD" });
    if (res.status === 405) {
      // Some servers reject HEAD; fall back to GET.
      res = await fetchWithTimeout(url, { method: "GET" });
    }
    if (res.ok) {
      return { status: "active", detail: `HTTP ${res.status}` };
    }
    return { status: "unreachable", detail: `HTTP ${res.status}` };
  } catch (err) {
    return { status: "unreachable", detail: err instanceof Error ? err.message : String(err) };
  }
}
