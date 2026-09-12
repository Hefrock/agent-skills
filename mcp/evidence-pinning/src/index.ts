#!/usr/bin/env node
import { Server } from "@modelcontextprotocol/sdk/server/index.js";
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
import {
  CallToolRequestSchema,
  ListToolsRequestSchema,
} from "@modelcontextprotocol/sdk/types.js";
import fs from "fs/promises";
import path from "path";
import crypto from "crypto";
import {
  type SourceStatus,
  checkDoiStatus,
  checkPmidStatus,
  checkUrlReachable,
} from "./decay.js";
import { claimsToFlagOnRetraction, type ClaimStatus } from "./logic.js";

const STORE_PATH: string = process.env.EVIDENCE_STORE_PATH ?? (() => {
  console.error("EVIDENCE_STORE_PATH environment variable is required");
  process.exit(1);
})();

const storeRoot = path.resolve(STORE_PATH);
await fs.mkdir(storeRoot, { recursive: true });

const SOURCES_FILE = path.join(storeRoot, "sources.json");
const CLAIMS_FILE = path.join(storeRoot, "claims.json");
const PROVENANCE_FILE = path.join(storeRoot, "provenance.jsonl");

// ── Types ─────────────────────────────────────────────────────────────────────

type SourceIdType = "doi" | "pmid" | "url";

interface Source {
  source_id: string;
  id_type: SourceIdType;
  url: string;
  title: string;
  registered_at: string;
  status: SourceStatus;
  last_checked_at: string | null;
  check_count: number;
}

interface Claim {
  claim_id: string;
  run_id: string;
  text: string;
  source_ids: string[];
  excerpt: string;
  pinned_at: string;
  status: ClaimStatus;
  flag_reason?: string;
}

interface ProvenanceEvent {
  event_id: string;
  target_type: "claim" | "source" | "run";
  target_id: string;
  stage: string;
  action: string;
  detail?: string;
  timestamp: string;
}

// ── Storage ───────────────────────────────────────────────────────────────────
// Same atomic-write shape as obsidian-vault MCP: temp file in the same dir, then
// rename, so a crash mid-write never leaves a corrupt store file. provenance.jsonl
// is append-only by design (the point of a provenance log), so it doesn't need
// the atomic-replace treatment the two JSON maps do.

async function atomicWriteJson(filePath: string, data: unknown): Promise<void> {
  const tmp = `${filePath}.tmp-${process.pid}-${Date.now()}`;
  await fs.writeFile(tmp, JSON.stringify(data, null, 2));
  await fs.rename(tmp, filePath);
}

async function loadJson<T>(filePath: string, fallback: T): Promise<T> {
  try {
    return JSON.parse(await fs.readFile(filePath, "utf-8")) as T;
  } catch (err) {
    if ((err as NodeJS.ErrnoException).code === "ENOENT") return fallback;
    throw err;
  }
}

async function loadSources(): Promise<Record<string, Source>> {
  return loadJson<Record<string, Source>>(SOURCES_FILE, {});
}
async function saveSources(sources: Record<string, Source>): Promise<void> {
  await atomicWriteJson(SOURCES_FILE, sources);
}
async function loadClaims(): Promise<Record<string, Claim>> {
  return loadJson<Record<string, Claim>>(CLAIMS_FILE, {});
}
async function saveClaims(claims: Record<string, Claim>): Promise<void> {
  await atomicWriteJson(CLAIMS_FILE, claims);
}

async function appendProvenance(event: Omit<ProvenanceEvent, "event_id" | "timestamp">): Promise<void> {
  const full: ProvenanceEvent = {
    ...event,
    event_id: crypto.randomUUID(),
    timestamp: new Date().toISOString(),
  };
  await fs.appendFile(PROVENANCE_FILE, JSON.stringify(full) + "\n");
}

async function getProvenanceFor(targetType: string, targetId: string): Promise<ProvenanceEvent[]> {
  let raw: string;
  try {
    raw = await fs.readFile(PROVENANCE_FILE, "utf-8");
  } catch (err) {
    if ((err as NodeJS.ErrnoException).code === "ENOENT") return [];
    throw err;
  }
  const events: ProvenanceEvent[] = [];
  for (const line of raw.split("\n")) {
    if (!line.trim()) continue;
    const event = JSON.parse(line) as ProvenanceEvent;
    if (event.target_type === targetType && event.target_id === targetId) events.push(event);
  }
  return events;
}

// ── Source-id canonicalization ───────────────────────────────────────────────

const DOI_RE = /10\.\d{4,9}\/[-._;()/:A-Za-z0-9]+/;
const PMID_URL_RE = /pubmed\.ncbi\.nlm\.nih\.gov\/(\d+)/i;

function canonicalizeSourceId(url: string, idHint?: string): { source_id: string; id_type: SourceIdType } {
  if (idHint) {
    const [type, value] = idHint.includes(":") ? idHint.split(/:(.+)/) : [null, null];
    if (type === "doi" && value) return { source_id: `doi:${value.toLowerCase()}`, id_type: "doi" };
    if (type === "pmid" && value) return { source_id: `pmid:${value}`, id_type: "pmid" };
  }
  const doiMatch = url.match(DOI_RE);
  if (doiMatch) return { source_id: `doi:${doiMatch[0].toLowerCase()}`, id_type: "doi" };
  const pmidMatch = url.match(PMID_URL_RE);
  if (pmidMatch) return { source_id: `pmid:${pmidMatch[1]}`, id_type: "pmid" };
  const hash = crypto.createHash("sha256").update(url).digest("hex").slice(0, 16);
  return { source_id: `url:${hash}`, id_type: "url" };
}

// ── Tool implementations ─────────────────────────────────────────────────────

async function registerSource(url: string, title: string, idHint?: string): Promise<object> {
  const { source_id, id_type } = canonicalizeSourceId(url, idHint);
  const sources = await loadSources();
  const existing = sources[source_id];
  if (existing) {
    return { source_id, id_type, is_new: false, status: existing.status };
  }
  const now = new Date().toISOString();
  sources[source_id] = {
    source_id,
    id_type,
    url,
    title,
    registered_at: now,
    status: "active",
    last_checked_at: null,
    check_count: 0,
  };
  await saveSources(sources);
  await appendProvenance({ target_type: "source", target_id: source_id, stage: "ingest", action: "registered" });
  return { source_id, id_type, is_new: true, status: "active" };
}

async function pinClaim(runId: string, text: string, sourceIds: string[], excerpt: string): Promise<object> {
  if (sourceIds.length === 0) {
    throw new Error("pin_claim requires at least one source_id — an unsourced claim is exactly what this tool exists to prevent");
  }
  const sources = await loadSources();
  const missing = sourceIds.filter((id) => !sources[id]);
  if (missing.length > 0) {
    throw new Error(`Unknown source_id(s), register_source first: ${missing.join(", ")}`);
  }

  const claimId = crypto.createHash("sha256").update(`${runId}|${text}`).digest("hex").slice(0, 16);
  const claims = await loadClaims();
  if (claims[claimId]) {
    await appendProvenance({ target_type: "claim", target_id: claimId, stage: "evidence_pin", action: "already_pinned" });
    return { claim_id: claimId, is_new: false, status: claims[claimId].status };
  }

  claims[claimId] = {
    claim_id: claimId,
    run_id: runId,
    text,
    source_ids: sourceIds,
    excerpt,
    pinned_at: new Date().toISOString(),
    status: "pinned",
  };
  await saveClaims(claims);
  await appendProvenance({ target_type: "claim", target_id: claimId, stage: "evidence_pin", action: "pinned" });
  return { claim_id: claimId, is_new: true, status: "pinned" };
}

async function getClaims(runId: string): Promise<object> {
  const [claims, sources] = await Promise.all([loadClaims(), loadSources()]);
  const forRun = Object.values(claims).filter((c) => c.run_id === runId);
  const withSources = forRun.map((claim) => ({
    ...claim,
    sources: claim.source_ids.map((id) => sources[id]).filter(Boolean),
  }));
  return { run_id: runId, claims: withSources, total: withSources.length };
}

async function verifyClaim(claimId: string): Promise<object> {
  const [claims, sources] = await Promise.all([loadClaims(), loadSources()]);
  const claim = claims[claimId];
  if (!claim) throw new Error(`No claim registered with claim_id: ${claimId}`);
  return { ...claim, sources: claim.source_ids.map((id) => sources[id]).filter(Boolean) };
}

async function flagClaim(claimId: string, reason: string): Promise<object> {
  const claims = await loadClaims();
  const claim = claims[claimId];
  if (!claim) throw new Error(`No claim registered with claim_id: ${claimId}`);
  claim.status = "flagged";
  claim.flag_reason = reason;
  await saveClaims(claims);
  await appendProvenance({ target_type: "claim", target_id: claimId, stage: "qa_gate", action: "flagged", detail: reason });
  return { claim_id: claimId, status: "flagged", reason };
}

async function checkSourceDecay(sourceId: string): Promise<object> {
  const sources = await loadSources();
  const source = sources[sourceId];
  if (!source) throw new Error(`No source registered with source_id: ${sourceId}`);

  const previousStatus = source.status;
  const result =
    source.id_type === "doi" ? await checkDoiStatus(sourceId.slice("doi:".length)) :
    source.id_type === "pmid" ? await checkPmidStatus(sourceId.slice("pmid:".length)) :
    await checkUrlReachable(source.url);

  source.status = result.status;
  source.last_checked_at = new Date().toISOString();
  source.check_count += 1;
  await saveSources(sources);
  await appendProvenance({
    target_type: "source",
    target_id: sourceId,
    stage: "decay_check",
    action: "checked",
    detail: result.detail,
  });

  let flaggedClaims: string[] = [];
  if (previousStatus !== result.status) {
    await appendProvenance({
      target_type: "source",
      target_id: sourceId,
      stage: "decay_check",
      action: "status_changed",
      detail: `${previousStatus} -> ${result.status}`,
    });
    if (result.status === "retracted") {
      const claims = await loadClaims();
      const affected = claimsToFlagOnRetraction(Object.values(claims), sourceId);
      for (const claim of affected) {
        claim.status = "flagged";
        claim.flag_reason = `Backing source ${sourceId} was retracted after this claim was pinned`;
        await appendProvenance({
          target_type: "claim",
          target_id: claim.claim_id,
          stage: "decay_check",
          action: "flagged",
          detail: `source ${sourceId} retracted`,
        });
      }
      if (affected.length > 0) await saveClaims(claims);
      flaggedClaims = affected.map((c) => c.claim_id);
    }
  }

  return { source_id: sourceId, previous_status: previousStatus, status: result.status, detail: result.detail, flagged_claims: flaggedClaims };
}

async function getProvenance(targetType: string, targetId: string): Promise<object> {
  const events = await getProvenanceFor(targetType, targetId);
  return { target_type: targetType, target_id: targetId, events, total: events.length };
}

// ── Arg helpers (same shape as obsidian-vault MCP) ──────────────────────────

function requireString(args: Record<string, unknown>, key: string): string {
  const value = args[key];
  if (typeof value !== "string" || value.length === 0) {
    throw new Error(`Missing or invalid required string argument: ${key}`);
  }
  return value;
}
function requireStringArray(args: Record<string, unknown>, key: string): string[] {
  const value = args[key];
  if (!Array.isArray(value) || !value.every((v) => typeof v === "string")) {
    throw new Error(`Missing or invalid required string[] argument: ${key}`);
  }
  return value as string[];
}
function optionalString(args: Record<string, unknown>, key: string): string | undefined {
  const value = args[key];
  return typeof value === "string" ? value : undefined;
}

// ── MCP Server ────────────────────────────────────────────────────────────────

const server = new Server(
  { name: "evidence-pinning", version: "0.1.0" },
  { capabilities: { tools: {} } }
);

server.setRequestHandler(ListToolsRequestSchema, async () => ({
  tools: [
    {
      name: "register_source",
      description: "Register a source (a URL, optionally with a DOI or PMID) so claims can be pinned against it. Idempotent — re-registering the same DOI/PMID/URL returns the existing source_id rather than creating a duplicate. DOI is extracted from the URL automatically if present (or pass id_hint like 'doi:10.1000/xyz' / 'pmid:12345' to force it).",
      inputSchema: {
        type: "object",
        properties: {
          url: { type: "string" },
          title: { type: "string" },
          id_hint: { type: "string", description: "Optional 'doi:<doi>' or 'pmid:<pmid>' to force canonical id type instead of parsing the URL." },
        },
        required: ["url", "title"],
      },
    },
    {
      name: "pin_claim",
      description: "Register a claim (an assertion a script draft makes) against one or more already-registered sources, with the excerpt that backs it. Idempotent per (run_id, text). Fails if any source_id hasn't been registered — an unsourced claim can't be pinned.",
      inputSchema: {
        type: "object",
        properties: {
          run_id: { type: "string" },
          text: { type: "string", description: "The claim/assertion text as it appears in the script" },
          source_ids: { type: "array", items: { type: "string" }, description: "One or more source_ids from register_source" },
          excerpt: { type: "string", description: "The supporting quote/snippet from the source" },
        },
        required: ["run_id", "text", "source_ids", "excerpt"],
      },
    },
    {
      name: "get_claims",
      description: "List every claim pinned for a run, each with its backing sources (including current decay status) inlined. This is what a QA gate reads to check a script draft against pinned evidence.",
      inputSchema: {
        type: "object",
        properties: { run_id: { type: "string" } },
        required: ["run_id"],
      },
    },
    {
      name: "verify_claim",
      description: "Look up a single claim by claim_id, with its backing sources inlined.",
      inputSchema: {
        type: "object",
        properties: { claim_id: { type: "string" } },
        required: ["claim_id"],
      },
    },
    {
      name: "flag_claim",
      description: "Mark a claim as flagged (e.g. because a QA gate found it isn't actually backed by its cited excerpt). Flagged claims should not proceed to audio.",
      inputSchema: {
        type: "object",
        properties: {
          claim_id: { type: "string" },
          reason: { type: "string" },
        },
        required: ["claim_id", "reason"],
      },
    },
    {
      name: "check_source_decay",
      description: "Re-check a source's live status: DOI sources are checked against Crossref for retraction/correction notices, PMID sources against PubMed for the same, and plain URL sources for reachability only (a URL has no general retraction signal, so it can only become 'unreachable', never 'retracted'/'updated'). If a source transitions to 'retracted', every currently-pinned claim that cites it is automatically flagged.",
      inputSchema: {
        type: "object",
        properties: { source_id: { type: "string" } },
        required: ["source_id"],
      },
    },
    {
      name: "get_provenance",
      description: "Get the append-only provenance log for a claim, source, or run — which stage touched it, what action, when.",
      inputSchema: {
        type: "object",
        properties: {
          target_type: { type: "string", enum: ["claim", "source", "run"] },
          target_id: { type: "string" },
        },
        required: ["target_type", "target_id"],
      },
    },
  ],
}));

server.setRequestHandler(CallToolRequestSchema, async (request) => {
  const { name, arguments: args = {} } = request.params;
  try {
    let result: object;
    switch (name) {
      case "register_source":
        result = await registerSource(requireString(args, "url"), requireString(args, "title"), optionalString(args, "id_hint"));
        break;
      case "pin_claim":
        result = await pinClaim(requireString(args, "run_id"), requireString(args, "text"), requireStringArray(args, "source_ids"), requireString(args, "excerpt"));
        break;
      case "get_claims":
        result = await getClaims(requireString(args, "run_id"));
        break;
      case "verify_claim":
        result = await verifyClaim(requireString(args, "claim_id"));
        break;
      case "flag_claim":
        result = await flagClaim(requireString(args, "claim_id"), requireString(args, "reason"));
        break;
      case "check_source_decay":
        result = await checkSourceDecay(requireString(args, "source_id"));
        break;
      case "get_provenance":
        result = await getProvenance(requireString(args, "target_type"), requireString(args, "target_id"));
        break;
      default:
        throw new Error(`Unknown tool: ${name}`);
    }
    return { content: [{ type: "text", text: JSON.stringify(result, null, 2) }] };
  } catch (err) {
    const message = err instanceof Error ? err.message : String(err);
    return { content: [{ type: "text", text: JSON.stringify({ error: message }) }], isError: true };
  }
});

const transport = new StdioServerTransport();
await server.connect(transport);
