// Tests for the outbound-address guard on plain-URL decay checks (decay.ts):
// check_source_decay must never contact loopback, private, link-local or
// reserved addresses, or non-http(s) schemes, and must re-check every redirect
// hop. Mostly pure (injected lookup/fetch); the "no request was made" cases use
// a real HTTP server on 127.0.0.1 that counts hits, so they exercise the real
// system DNS and fetch paths, not fakes. Stdlib only, no network beyond loopback.

import http from "node:http";
import { isBlockedAddress, urlRefusalReason, checkUrlReachable, MAX_REDIRECTS } from "../dist/decay.js";

let passed = 0, failed = 0;
function check(label, condition, detail = "") {
  if (condition) { passed++; console.log(`  PASS  ${label}`); }
  else { failed++; console.log(`  FAIL  ${label}${detail ? ` — ${detail}` : ""}`); }
}

console.log("── isBlockedAddress ──────────────────────────────────────────");
const blocked = [
  "127.0.0.1", "127.255.255.254", "10.0.0.1", "172.16.0.1", "172.31.255.255", "192.168.1.1",
  "169.254.169.254", "100.64.0.1", "0.0.0.0", "224.0.0.1", "255.255.255.255", "198.18.0.1", "192.0.0.1",
  "::1", "::", "[::1]", "fe80::1", "fe80::1%eth0", "fc00::1", "fd12:3456::1", "fec0::1", "ff02::1",
  "::ffff:127.0.0.1", "::ffff:7f00:1", "::ffff:169.254.169.254", "64:ff9b::a9fe:a9fe", "2002:7f00:1::",
  "::127.0.0.1", "not-an-ip", "", "1.2.3", "999.1.1.1",
];
for (const a of blocked) check(`blocked: ${JSON.stringify(a)}`, isBlockedAddress(a) === true);
const allowed = ["93.184.216.34", "8.8.8.8", "172.32.0.1", "192.0.2.1", "2606:4700:4700::1111", "::ffff:8.8.8.8", "2002:0808:0808::"];
for (const a of allowed) check(`allowed: ${a}`, isBlockedAddress(a) === false);

console.log("── urlRefusalReason ──────────────────────────────────────────");
const publicLookup = async () => ["93.184.216.34"];
check("https to a public host is allowed", (await urlRefusalReason("https://example.org/a", publicLookup)) === null);
for (const u of ["file:///etc/passwd", "ftp://example.org/x", "gopher://example.org/", "data:text/plain,hi", "javascript:alert(1)"]) {
  const r = await urlRefusalReason(u, publicLookup);
  check(`scheme refused: ${u}`, r !== null && /scheme/.test(r), String(r));
}
check("unparseable URL refused", (await urlRefusalReason("not a url", publicLookup)) !== null);
check("literal loopback refused without a DNS lookup", (await urlRefusalReason("http://127.0.0.1:8080/", async () => { throw new Error("lookup called"); }))?.includes("127.0.0.1"));
check("literal IPv6 loopback refused", (await urlRefusalReason("http://[::1]/", publicLookup)) !== null);
check("hostname resolving to metadata address refused", (await urlRefusalReason("http://metadata.example/", async () => ["169.254.169.254"]))?.includes("169.254.169.254"));
check("refused if ANY resolved address is internal", (await urlRefusalReason("http://mixed.example/", async () => ["93.184.216.34", "10.0.0.5"])) !== null);
check("hostname resolving to nothing refused", (await urlRefusalReason("http://empty.example/", async () => [])) !== null);
check("decimal-integer IP host (http://2130706433/) refused", (await urlRefusalReason("http://2130706433/", publicLookup)) !== null);
check("allowPrivate opts back in for internal hosts", (await urlRefusalReason("http://10.0.0.1/", publicLookup, true)) === null);
check("allowPrivate does not open other schemes", (await urlRefusalReason("file:///etc/passwd", publicLookup, true)) !== null);

console.log("── checkUrlReachable with injected fetch ─────────────────────");
function fakeFetch(routes) {
  const calls = [];
  const fn = async (url, init) => {
    calls.push({ url, method: init?.method, redirect: init?.redirect });
    const r = routes[url];
    if (!r) throw new Error(`unexpected fetch ${url}`);
    return new Response(null, { status: r.status, headers: r.location ? { location: r.location } : {} });
  };
  fn.calls = calls;
  return fn;
}
let f = fakeFetch({ "https://pub.example/a": { status: 200 } });
let res = await checkUrlReachable("https://pub.example/a", { lookup: publicLookup, fetch: f });
check("public 200 -> active", res.status === "active", JSON.stringify(res));
check("fetch is called with redirect: manual", f.calls[0]?.redirect === "manual");

f = fakeFetch({ "https://pub.example/r": { status: 302, location: "http://169.254.169.254/latest/meta-data/" } });
res = await checkUrlReachable("https://pub.example/r", { lookup: publicLookup, fetch: f });
check("redirect to metadata address is refused", res.status === "unreachable" && /not checked/.test(res.detail) && /redirect hop 1/.test(res.detail), JSON.stringify(res));
check("the internal redirect target is never fetched", f.calls.length === 1, JSON.stringify(f.calls));

f = fakeFetch({ "https://pub.example/r": { status: 301, location: "/final" }, "https://pub.example/final": { status: 200 } });
res = await checkUrlReachable("https://pub.example/r", { lookup: publicLookup, fetch: f });
check("relative redirect to a public page is followed -> active", res.status === "active" && f.calls.length === 2, JSON.stringify(res));

f = fakeFetch({ "https://pub.example/h": { status: 405 } });
f = (() => { const calls = []; const fn = async (url, init) => { calls.push(init?.method); return new Response(null, { status: init?.method === "HEAD" ? 405 : 200 }); }; fn.calls = calls; return fn; })();
res = await checkUrlReachable("https://pub.example/h", { lookup: publicLookup, fetch: f });
check("HEAD 405 falls back to GET (behaviour kept)", res.status === "active" && f.calls.join() === "HEAD,GET", JSON.stringify([res, f.calls]));

const loop = {}; for (let i = 0; i <= MAX_REDIRECTS + 1; i++) loop[`https://pub.example/${i}`] = { status: 302, location: `/${i + 1}` };
f = fakeFetch(loop);
res = await checkUrlReachable("https://pub.example/0", { lookup: publicLookup, fetch: f });
check(`more than ${MAX_REDIRECTS} redirects -> unreachable`, res.status === "unreachable" && /redirects/.test(res.detail), JSON.stringify(res));

f = fakeFetch({ "https://pub.example/x": { status: 302 } });
res = await checkUrlReachable("https://pub.example/x", { lookup: publicLookup, fetch: f });
check("3xx with no Location -> unreachable", res.status === "unreachable", JSON.stringify(res));

f = fakeFetch({ "https://pub.example/404": { status: 404 } });
res = await checkUrlReachable("https://pub.example/404", { lookup: publicLookup, fetch: f });
check("public 404 -> unreachable (behaviour kept)", res.status === "unreachable" && res.detail === "HTTP 404", JSON.stringify(res));

console.log("── real loopback server: guard must stop the request ─────────");
let hits = 0;
const srv = http.createServer((req, res2) => { hits++; res2.writeHead(200); res2.end("ok"); });
await new Promise((r) => srv.listen(0, "127.0.0.1", r));
const port = srv.address().port;
delete process.env.EVIDENCE_ALLOW_PRIVATE_URLS;
for (const u of [`http://127.0.0.1:${port}/`, `http://localhost:${port}/`, `http://[::ffff:127.0.0.1]:${port}/`]) {
  res = await checkUrlReachable(u);
  check(`real path: ${u} refused`, res.status === "unreachable" && /not checked/.test(res.detail), JSON.stringify(res));
}
check("real path: the loopback server received zero requests", hits === 0, `hits=${hits}`);
res = await checkUrlReachable(`http://127.0.0.1:${port}/`, { allowPrivate: true });
check("real path: EVIDENCE_ALLOW_PRIVATE_URLS-style opt-in does reach it -> active", res.status === "active" && hits > 0, JSON.stringify([res, hits]));
srv.close();

console.log(`\n${"─".repeat(62)}`);
console.log(`  ${passed + failed} tests  —  ${passed} passed  —  ${failed} failed`);
process.exit(failed > 0 ? 1 : 0);
