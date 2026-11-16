// Source-decay checking: does a pinned source still say what it said when it was
// pinned? Split into pure classifiers (fixture-testable, no network) and thin
// network wrappers that fetch live data and hand it to the classifiers — the split
// exists so decay-classify.test.mjs can cover the parsing logic against real
// captured API responses without needing a live retracted DOI/PMID to test against.

import { lookup as dnsLookup } from "node:dns/promises";

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

// ── Outbound-address guard (plain URL sources) ──────────────────────────────
//
// A plain-URL source is fetched at whatever address it names, so without a
// guard check_source_decay could be pointed at loopback, the LAN, or a cloud
// metadata endpoint (169.254.169.254). Every hop, including each redirect, is
// refused unless it is http(s) and every address its host resolves to is a
// public one. Fails closed: anything that is not a parseable IP is blocked.
//
// Residual gap, stated plainly: the address is resolved here and again by
// fetch, so a hostile DNS server that answers differently the second time
// (DNS rebinding) is not stopped. Closing that needs a pinned-IP dispatcher,
// which needs undici as a dependency; not done here.

function ipv4ToInt(ip: string): number | null {
  const m = ip.match(/^(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})$/);
  if (!m) return null;
  const parts = m.slice(1).map(Number);
  if (parts.some((p) => p > 255)) return null;
  return ((parts[0] << 24) >>> 0) + (parts[1] << 16) + (parts[2] << 8) + parts[3];
}

const BLOCKED_V4: Array<[string, number]> = [
  ["0.0.0.0", 8],        // "this network"
  ["10.0.0.0", 8],       // private
  ["100.64.0.0", 10],    // carrier-grade NAT
  ["127.0.0.0", 8],      // loopback
  ["169.254.0.0", 16],   // link-local, incl. cloud metadata
  ["172.16.0.0", 12],    // private
  ["192.0.0.0", 24],     // IETF protocol assignments
  ["192.168.0.0", 16],   // private
  ["198.18.0.0", 15],    // benchmarking
  ["224.0.0.0", 4],      // multicast
  ["240.0.0.0", 4],      // reserved, incl. 255.255.255.255
];

function v4Blocked(n: number): boolean {
  return BLOCKED_V4.some(([base, bits]) => {
    const mask = bits === 0 ? 0 : (~0 << (32 - bits)) >>> 0;
    return ((n & mask) >>> 0) === ((ipv4ToInt(base)! & mask) >>> 0);
  });
}

function expandIPv6(addr: string): number[] | null {
  let s = addr;
  let v4tail: number[] = [];
  const dotted = s.match(/^(.*:)(\d{1,3}(?:\.\d{1,3}){3})$/);
  if (dotted) {
    const n = ipv4ToInt(dotted[2]);
    if (n === null) return null;
    v4tail = [n >>> 16, n & 0xffff];
    s = dotted[1].endsWith("::") ? dotted[1] : dotted[1].slice(0, -1);
  }
  const halves = s.split("::");
  if (halves.length > 2) return null;
  const parse = (part: string) => (part === "" ? [] : part.split(":"));
  const head = parse(halves[0]);
  const tail = halves.length === 2 ? parse(halves[1]) : [];
  const groups = [...head, ...tail];
  if (groups.some((g) => !/^[0-9a-f]{1,4}$/.test(g))) return null;
  const known = groups.length + v4tail.length;
  if (halves.length === 1 && known !== 8) return null;
  if (halves.length === 2 && known > 7) return null;
  const zeros = halves.length === 2 ? new Array(8 - known).fill(0) : [];
  return [...head.map((g) => parseInt(g, 16)), ...zeros, ...tail.map((g) => parseInt(g, 16)), ...v4tail];
}

export function isBlockedAddress(ip: string): boolean {
  let addr = ip.trim().toLowerCase();
  if (addr.startsWith("[") && addr.endsWith("]")) addr = addr.slice(1, -1);
  const zone = addr.indexOf("%");
  if (zone >= 0) addr = addr.slice(0, zone);

  const v4 = ipv4ToInt(addr);
  if (v4 !== null) return v4Blocked(v4);

  const g = expandIPv6(addr);
  if (!g) return true; // not an IP literal we understand: fail closed
  const embeddedV4 = (hi: number, lo: number) => v4Blocked(((hi << 16) >>> 0) + lo);
  if (g.every((x) => x === 0)) return true;                                  // ::
  if (g.slice(0, 7).every((x) => x === 0) && g[7] === 1) return true;        // ::1
  if (g.slice(0, 5).every((x) => x === 0) && g[5] === 0xffff) return embeddedV4(g[6], g[7]); // ::ffff:a.b.c.d
  if (g.slice(0, 6).every((x) => x === 0)) return true;                      // ::a.b.c.d (deprecated)
  if (g[0] === 0x64 && g[1] === 0xff9b && g.slice(2, 6).every((x) => x === 0)) return embeddedV4(g[6], g[7]); // NAT64
  if (g[0] === 0x2002) return embeddedV4(g[1], g[2]);                         // 6to4
  if ((g[0] & 0xfe00) === 0xfc00) return true;                               // fc00::/7 unique local
  if ((g[0] & 0xffc0) === 0xfe80) return true;                               // fe80::/10 link-local
  if ((g[0] & 0xffc0) === 0xfec0) return true;                               // fec0::/10 site-local (deprecated)
  if ((g[0] & 0xff00) === 0xff00) return true;                               // multicast
  return false;
}

export type LookupFn = (hostname: string) => Promise<string[]>;
export type FetchFn = (url: string, init?: RequestInit) => Promise<Response>;

const systemLookup: LookupFn = async (hostname) =>
  (await dnsLookup(hostname, { all: true, verbatim: true })).map((a) => a.address);

// Returns why `url` must not be fetched, or null if it may be.
export async function urlRefusalReason(url: string, lookup: LookupFn = systemLookup, allowPrivate = false): Promise<string | null> {
  let parsed: URL;
  try {
    parsed = new URL(url);
  } catch {
    return "not a valid URL";
  }
  if (parsed.protocol !== "http:" && parsed.protocol !== "https:") {
    return `scheme ${parsed.protocol} is not allowed (http and https only)`;
  }
  if (allowPrivate) return null;
  const host = parsed.hostname.replace(/^\[|\]$/g, "");
  const literal = ipv4ToInt(host) !== null || expandIPv6(host.toLowerCase().split("%")[0]) !== null;
  const addresses = literal ? [host] : await lookup(host);
  if (addresses.length === 0) return `${host} did not resolve to any address`;
  const blocked = addresses.find(isBlockedAddress);
  if (blocked) {
    return `${host} resolves to a loopback, private, link-local or reserved address (${blocked}); set EVIDENCE_ALLOW_PRIVATE_URLS=1 to allow`;
  }
  return null;
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
//
// Redirects are followed by hand (up to MAX_REDIRECTS) so that every hop goes
// through urlRefusalReason; letting fetch follow them would let a public URL
// bounce the request to an internal one. A refused hop is reported as
// "unreachable" with a "not checked:" detail and makes no request at all.
export const MAX_REDIRECTS = 5;

export interface UrlCheckDeps {
  lookup?: LookupFn;
  fetch?: FetchFn;
  allowPrivate?: boolean;
}

export async function checkUrlReachable(url: string, deps: UrlCheckDeps = {}): Promise<DecayCheckResult> {
  const lookup = deps.lookup ?? systemLookup;
  const doFetch = deps.fetch ?? fetchWithTimeout;
  const allowPrivate = deps.allowPrivate ?? process.env.EVIDENCE_ALLOW_PRIVATE_URLS === "1";
  let current = url;
  try {
    for (let hop = 0; hop <= MAX_REDIRECTS; hop++) {
      const refusal = await urlRefusalReason(current, lookup, allowPrivate);
      if (refusal) {
        return { status: "unreachable", detail: `not checked: ${refusal}${hop > 0 ? ` (redirect hop ${hop})` : ""}` };
      }
      let res = await doFetch(current, { method: "HEAD", redirect: "manual" });
      if (res.status === 405) {
        // Some servers reject HEAD; fall back to GET.
        res = await doFetch(current, { method: "GET", redirect: "manual" });
      }
      if (res.status >= 300 && res.status < 400) {
        const location = res.headers.get("location");
        if (!location) return { status: "unreachable", detail: `HTTP ${res.status} redirect with no Location header` };
        current = new URL(location, current).toString();
        continue;
      }
      if (res.ok) {
        return { status: "active", detail: `HTTP ${res.status}` };
      }
      return { status: "unreachable", detail: `HTTP ${res.status}` };
    }
    return { status: "unreachable", detail: `more than ${MAX_REDIRECTS} redirects` };
  } catch (err) {
    return { status: "unreachable", detail: err instanceof Error ? err.message : String(err) };
  }
}
