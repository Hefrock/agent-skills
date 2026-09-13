#!/usr/bin/env python3
"""
scan_diff.py — deterministic pre-disclosure privacy scanner (Direct PII, Secrets, Metadata).

Scans staged git changes (or an arbitrary file/stdin) for three mechanically-detectable
leak classes from the Pre-Disclosure Privacy Linter design: Direct PII (email, phone,
SSN, Luhn-valid credit card, IPv4), Secrets (AWS/GitHub/Slack/Stripe/Google/Anthropic
tokens, private key blocks, a conservative quoted-assignment catch-all), and Metadata
(file types that commonly carry EXIF/document properties, plus a confirmed EXIF GPS
check when Pillow is installed).

Inference cues and stylometric fingerprinting are NOT implemented here — see
references/leak-taxonomy.md. Both need actual judgment (a model), which conflicts with
this project's own "must run locally, never send content to an external API" constraint
until a local-model path is wired in. Shipping the deterministic slice first, end to
end, is the same order deid-reid-harness used for its own tracks.

Usage:
    scan_diff.py                          # git pre-commit mode: staged diff + staged files
    scan_diff.py --file path/to/thing.txt # scan one file's full current content
    scan_diff.py --commit-msg FILE        # scan a commit message file (commit-msg hook)
    scan_diff.py --text -                 # read text to scan from stdin

Advisory by default (always exits 0). Add a CI/hook gate:
    scan_diff.py --block-on high          # exit 1 if any 'high' (or above) finding exists

Metadata is detected, not fixed, everywhere above — this scanner never rewrites your
staged changes. The one exception is a separate, explicit mode that replaces scanning
entirely for that invocation:
    scan_diff.py --strip-metadata photo.jpg               # -> photo.stripped.jpg, EXIF removed
    scan_diff.py --strip-metadata photo.jpg --out clean.jpg
    scan_diff.py --strip-metadata photo.jpg --in-place     # overwrites photo.jpg itself

Stdlib only for the core scan. Pillow is optional for scanning — EXIF GPS confirmation
degrades gracefully to a file-type heuristic when it isn't installed (reported, not
silent) — but required for --strip-metadata, which has no non-Pillow fallback.
"""

import argparse
import fnmatch
import json
import os
import subprocess
import sys
from dataclasses import dataclass, asdict

try:
    from PIL import Image
    from PIL.ExifTags import TAGS, GPSTAGS
    HAS_PIL = True
except ImportError:
    HAS_PIL = False

import re

SEVERITY_ORDER = {"low": 0, "medium": 1, "high": 2}

SUPPRESS_MARKER = "privacy-linter: ignore"
IGNORE_FILE = ".privacy-linter-ignore"

METADATA_EXTENSIONS = {
    ".jpg": "medium", ".jpeg": "medium", ".png": "medium", ".heic": "medium",
    ".tiff": "medium", ".tif": "medium", ".bmp": "medium", ".gif": "medium",
    ".pdf": "medium", ".docx": "medium", ".xlsx": "medium", ".pptx": "medium",
}
EXIF_CAPABLE_EXTENSIONS = {".jpg", ".jpeg", ".tiff", ".tif"}


# --- Direct PII patterns -----------------------------------------------------------

EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
PHONE_RE = re.compile(
    r"(?<!\d)(?:\+?1[-.\s]?)?\(?\d{3}\)?[-.\s]\d{3}[-.\s]\d{4}(?!\d)"
)
SSN_RE = re.compile(r"(?<!\d)\d{3}-\d{2}-\d{4}(?!\d)")
CREDIT_CARD_RE = re.compile(r"(?<!\d)(?:\d[ -]?){13,19}(?!\d)")
IPV4_RE = re.compile(r"(?<!\d)(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})(?!\d)")


def _luhn_valid(digits):
    total = 0
    parity = len(digits) % 2
    for i, ch in enumerate(digits):
        d = int(ch)
        if i % 2 == parity:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


def _valid_ipv4(match):
    return all(0 <= int(g) <= 255 for g in match.groups())


PII_SCANNERS = [
    ("email", EMAIL_RE, "medium", "Email address", lambda m: True),
    ("phone", PHONE_RE, "medium", "Phone number", lambda m: True),
    ("ssn", SSN_RE, "high", "US Social Security Number format (###-##-####)", lambda m: True),
    ("credit_card", CREDIT_CARD_RE, "high", "Luhn-valid card number",
     lambda m: _luhn_valid(re.sub(r"[ -]", "", m.group(0)))),
    ("ip_address", IPV4_RE, "low", "IPv4 address", _valid_ipv4),
]


# --- Secret/credential patterns -----------------------------------------------------
# A distinct leak class from Direct PII — these are secrets, not personal data — but
# mechanically detectable the same way: pattern-match added lines, no model needed.
# Prefixed-token formats (AWS/GitHub/Slack/Stripe/Google/Anthropic, private key
# headers) are high-confidence by construction: the prefix itself is only ever a real
# credential, never ordinary prose. The generic assignment pattern is the deliberately
# narrower catch-all for a secret in an unrecognized format — it only fires on a
# QUOTED string literal assigned to a secret-sounding name, never a bare/unquoted
# value (a bare .env-style KEY=value or a function-call right-hand side would swamp
# this in false positives — a real, documented gap, not an oversight).

AWS_ACCESS_KEY_RE = re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")
GITHUB_TOKEN_RE = re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,}\b")
SLACK_TOKEN_RE = re.compile(r"\bxox[baprs]-[0-9A-Za-z-]{10,48}\b")
STRIPE_KEY_RE = re.compile(r"\bsk_live_[0-9a-zA-Z]{24,}\b")
GOOGLE_API_KEY_RE = re.compile(r"\bAIza[0-9A-Za-z\-_]{35}\b")
ANTHROPIC_API_KEY_RE = re.compile(r"\bsk-ant-[A-Za-z0-9\-_]{20,}\b")
PRIVATE_KEY_BLOCK_RE = re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA |PGP )?PRIVATE KEY-----")
GENERIC_SECRET_ASSIGNMENT_RE = re.compile(
    r"(?i)[\w-]*(?:api[_-]?key|secret|token|password|passwd)[\w-]*\s*[:=]\s*[\"']([^\"']{8,})[\"']"
)

# Values that are obviously placeholders, not real secrets — checked against the
# generic assignment scanner's captured value only; the prefixed-token scanners above
# never need this, since a real AWS/GitHub/etc. prefix on a placeholder string would
# be a strange thing for anyone to type by hand.
_PLACEHOLDER_MARKERS = (
    "xxx", "your_", "your-", "example", "changeme", "change_me", "replace",
    "placeholder", "dummy", "<", ">", "***", "todo", "fake", "insert_", "redacted",
)


def _looks_like_placeholder(value: str) -> bool:
    lowered = value.lower()
    return any(marker in lowered for marker in _PLACEHOLDER_MARKERS)


SECRET_SCANNERS = [
    ("aws_access_key", AWS_ACCESS_KEY_RE, "high", "AWS access key ID", lambda m: True),
    ("github_token", GITHUB_TOKEN_RE, "high", "GitHub token", lambda m: True),
    ("slack_token", SLACK_TOKEN_RE, "high", "Slack token", lambda m: True),
    ("stripe_key", STRIPE_KEY_RE, "high", "Stripe live secret key", lambda m: True),
    ("google_api_key", GOOGLE_API_KEY_RE, "high", "Google API key", lambda m: True),
    ("anthropic_api_key", ANTHROPIC_API_KEY_RE, "high", "Anthropic API key", lambda m: True),
    ("private_key_block", PRIVATE_KEY_BLOCK_RE, "high", "Private key block", lambda m: True),
    ("generic_secret_assignment", GENERIC_SECRET_ASSIGNMENT_RE, "medium", "Possible hardcoded credential",
     lambda m: not _looks_like_placeholder(m.group(1))),
]


@dataclass
class Finding:
    severity: str
    leak_class: str
    finding: str
    reason: str
    location: str

    def format(self):
        return f"[severity: {self.severity}] [class: {self.leak_class}] [{self.finding}] {self.reason} ({self.location})"


# --- Suppression ---------------------------------------------------------------------

def load_ignore_patterns(repo_root):
    path = os.path.join(repo_root, IGNORE_FILE)
    patterns = []
    if os.path.isfile(path):
        with open(path) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#"):
                    patterns.append(line)
    return patterns


def is_path_ignored(path, patterns):
    return any(fnmatch.fnmatch(path, pat) for pat in patterns)


# --- Direct PII / secret scanning over text --------------------------------------------

def _find_pattern_matches(content, scanners):
    """Runs each (subtype, pattern, severity, label, validate) scanner in
    `scanners` against `content`, returning (severity, subtype, label) for
    every validated match. leak_class and location vary by caller (a text
    line vs. a diff-added line; "direct_pii" vs. "secret") so those aren't
    baked in here — every call site (scan_text_for_pii, scan_text_for_
    secrets, scan_staged's two passes) wraps this into a Finding with its
    own leak_class/location instead of duplicating the per-scanner loop
    four times."""
    hits = []
    for subtype, pattern, severity, label, validate in scanners:
        for m in pattern.finditer(content):
            if validate(m):
                hits.append((severity, subtype, label))
    return hits


def _findings_from_matches(content, scanners, leak_class, location, reason_suffix=""):
    """Wraps _find_pattern_matches() into a list of Finding objects for
    one leak_class/location — the part that does differ between
    scan_text_for_pii/scan_text_for_secrets (a text line) and
    scan_staged's two passes (a diff-added line, which adds "in staged
    addition" to the reason so it's clear the match came from the diff,
    not the file's full content)."""
    findings = []
    for severity, subtype, label in _find_pattern_matches(content, scanners):
        findings.append(Finding(
            severity=severity,
            leak_class=leak_class,
            finding=f"{label}: {subtype}",
            reason=f"Matched {subtype} pattern{reason_suffix}",
            location=location,
        ))
    return findings


def scan_text_for_pii(text, location_prefix):
    findings = []
    for lineno, line in enumerate(text.splitlines(), 1):
        if SUPPRESS_MARKER in line:
            continue
        findings.extend(_findings_from_matches(line, PII_SCANNERS, "direct_pii", f"{location_prefix}:{lineno}"))
    return findings


def scan_text_for_secrets(text, location_prefix):
    findings = []
    for lineno, line in enumerate(text.splitlines(), 1):
        if SUPPRESS_MARKER in line:
            continue
        findings.extend(_findings_from_matches(line, SECRET_SCANNERS, "secret", f"{location_prefix}:{lineno}"))
    return findings


# --- Metadata scanning over a staged file list -----------------------------------------

def scan_metadata(file_paths, repo_root=None):
    findings = []
    for path in file_paths:
        ext = os.path.splitext(path)[1].lower()
        if ext not in METADATA_EXTENSIONS:
            continue

        exif_checked = False
        if ext in EXIF_CAPABLE_EXTENSIONS and HAS_PIL and repo_root:
            full_path = os.path.join(repo_root, path)
            gps = _read_exif_gps(full_path)
            exif_checked = gps is not None
            if gps:
                findings.append(Finding(
                    severity="high",
                    leak_class="metadata",
                    finding="Embedded GPS location (EXIF)",
                    reason="EXIF GPSInfo tag present and non-empty",
                    location=path,
                ))
                continue  # confirmed finding supersedes the generic heuristic below

        if not exif_checked:
            note = "" if (ext in EXIF_CAPABLE_EXTENSIONS and HAS_PIL) else (
                " (Pillow not installed — EXIF not inspected)" if ext in EXIF_CAPABLE_EXTENSIONS else ""
            )
            findings.append(Finding(
                severity=METADATA_EXTENSIONS[ext],
                leak_class="metadata",
                finding=f"File type commonly carries embedded metadata ({ext})",
                reason=f"Verify EXIF/document properties are stripped before committing{note}",
                location=path,
            ))
    return findings


def strip_exif_metadata(source_path, dest_path):
    """Re-saves source_path's image to dest_path with all EXIF metadata
    dropped, returning the sorted, deduped human-readable tag names that
    were present beforehand (empty list if there was none). Verified
    empirically before writing this, not assumed from Pillow's docs:
    Image.save() does not carry EXIF forward unless the caller explicitly
    passes exif=img.info[...] or exif=img.getexif() — simply not doing
    that is sufficient, confirmed against a real fixture with a written
    GPSInfo IFD, including the same-path (in-place) case. Only ever
    called for EXIF_CAPABLE_EXTENSIONS; the caller re-reads dest_path's
    EXIF afterward to confirm the strip actually worked rather than
    trusting this docstring's claim alone."""
    with Image.open(source_path) as img:
        exif = img.getexif()
        removed = sorted({TAGS.get(tag_id, str(tag_id)) for tag_id in exif}) if exif else []
        img.save(dest_path)
    return removed


def run_strip_metadata(source_path, out_path, in_place):
    """CLI-level wiring for --strip-metadata: resolves the destination
    path, does the strip, then re-verifies the *output* file has no GPS
    EXIF left (via the same _read_exif_gps() the detection path already
    trusts) instead of declaring success just because strip_exif_
    metadata() didn't raise. Returns a process exit code."""
    ext = os.path.splitext(source_path)[1].lower()
    if ext not in EXIF_CAPABLE_EXTENSIONS:
        print(
            f"privacy-linter: --strip-metadata isn't implemented for {ext or '(no extension)'} yet "
            f"— only {', '.join(sorted(EXIF_CAPABLE_EXTENSIONS))} (see references/leak-taxonomy.md).",
            file=sys.stderr,
        )
        return 2
    if not HAS_PIL:
        print("privacy-linter: --strip-metadata requires Pillow (`pip install pillow`).", file=sys.stderr)
        return 2
    if not os.path.isfile(source_path):
        print(f"privacy-linter: {source_path} not found.", file=sys.stderr)
        return 2

    if in_place:
        dest_path = source_path
    elif out_path:
        dest_path = out_path
    else:
        root, ext_ = os.path.splitext(source_path)
        dest_path = f"{root}.stripped{ext_}"
        if os.path.exists(dest_path):
            print(
                f"privacy-linter: {dest_path} already exists — pass --out to choose a different path, "
                f"or --in-place to overwrite {source_path} itself.",
                file=sys.stderr,
            )
            return 2

    removed = strip_exif_metadata(source_path, dest_path)
    if _read_exif_gps(dest_path):
        print(f"privacy-linter: WARNING — {dest_path} still has GPS EXIF after stripping; do not share it.", file=sys.stderr)
        return 1
    if removed:
        print(f"privacy-linter: stripped EXIF from {source_path} -> {dest_path} (removed: {', '.join(removed)})")
    else:
        print(f"privacy-linter: {source_path} had no EXIF metadata to strip -> wrote a clean copy to {dest_path}")
    return 0


def _read_exif_gps(full_path):
    """Return the raw GPSInfo dict if present and non-empty, else None. Never raises."""
    if not HAS_PIL or not os.path.isfile(full_path):
        return None
    try:
        with Image.open(full_path) as img:
            exif = img.getexif()
            if not exif:
                return None
            gps_ifd = exif.get_ifd(0x8825)  # GPSInfo tag
            return dict(gps_ifd) if gps_ifd else None
    except Exception:
        return None


# --- Git integration -------------------------------------------------------------------

def _run(cmd, cwd=None):
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, check=False)


def get_repo_root(cwd=None):
    """Resolve the git repo root for `cwd` (default: the process's own cwd).

    Must take an explicit cwd for --file mode: the target file may live in a
    different repo than wherever the script happens to be invoked from, and
    silently resolving against the wrong repo would misconstruct every path
    scan_metadata builds from it (a silent-degradation bug, not just a wrong path).
    """
    proc = _run(["git", "rev-parse", "--show-toplevel"], cwd=cwd)
    if proc.returncode != 0:
        return None
    return proc.stdout.strip()


def get_staged_diff(repo_root):
    proc = _run(["git", "diff", "--cached", "-U0"], cwd=repo_root)
    return proc.stdout if proc.returncode == 0 else ""


def get_staged_files(repo_root):
    proc = _run(["git", "diff", "--cached", "--name-only"], cwd=repo_root)
    if proc.returncode != 0:
        return []
    return [line for line in proc.stdout.splitlines() if line]


def added_lines_from_diff(diff_text):
    """Yield (file_path, line_no_in_new_file, line_text) for each added line."""
    current_file = None
    new_lineno = None
    for line in diff_text.splitlines():
        if line.startswith("+++ "):
            path = line[4:]
            current_file = path[2:] if path.startswith("b/") else path
            continue
        if line.startswith("@@"):
            m = re.search(r"\+(\d+)", line)
            new_lineno = int(m.group(1)) if m else 1
            continue
        if line.startswith("+") and not line.startswith("+++"):
            yield current_file or "?", new_lineno, line[1:]
            if new_lineno is not None:
                new_lineno += 1
            continue
        if line.startswith("-") and not line.startswith("---"):
            continue


def scan_staged(repo_root):
    diff_text = get_staged_diff(repo_root)
    staged_files = get_staged_files(repo_root)
    ignore_patterns = load_ignore_patterns(repo_root)

    staged_files = [p for p in staged_files if not is_path_ignored(p, ignore_patterns)]

    findings = []
    for path, lineno, content in added_lines_from_diff(diff_text):
        if is_path_ignored(path, ignore_patterns):
            continue
        if SUPPRESS_MARKER in content:
            continue
        location = f"{path}:{lineno}"
        findings.extend(_findings_from_matches(content, PII_SCANNERS, "direct_pii", location, " in staged addition"))
        findings.extend(_findings_from_matches(content, SECRET_SCANNERS, "secret", location, " in staged addition"))
    findings.extend(scan_metadata(staged_files, repo_root=repo_root))
    return findings


# --- Reporting ---------------------------------------------------------------------

def print_report(findings, json_out=False):
    if json_out:
        print(json.dumps([asdict(f) for f in findings], indent=2))
        return
    if not findings:
        print("privacy-linter: no findings.")
        return
    print(f"privacy-linter: {len(findings)} finding(s)")
    for f in sorted(findings, key=lambda f: -SEVERITY_ORDER[f.severity]):
        print(f"  {f.format()}")
    if not HAS_PIL:
        print("\n(Pillow not installed — EXIF GPS confirmation skipped; image/doc files "
              "still flagged by file type. `pip install pillow` to enable the stronger check.)")


def check_gate(findings, block_on):
    if block_on is None:
        return False
    threshold = SEVERITY_ORDER[block_on]
    return any(SEVERITY_ORDER[f.severity] >= threshold for f in findings)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--file", help="Scan one file's full current content instead of the staged diff")
    parser.add_argument("--commit-msg", help="Scan a commit message file (for use as a commit-msg hook)")
    parser.add_argument("--text", help="Read text to scan from stdin (pass '-')")
    parser.add_argument("--json", action="store_true", help="Emit findings as JSON")
    parser.add_argument("--block-on", choices=["low", "medium", "high"], default=None,
                        help="Exit non-zero if any finding at or above this severity exists (default: advisory only, always exits 0)")
    parser.add_argument("--strip-metadata", metavar="FILE",
                        help="Write a copy of FILE with EXIF metadata removed (jpg/jpeg/tiff/tif only; requires Pillow) instead of scanning")
    parser.add_argument("--out", metavar="PATH", help="Output path for --strip-metadata (default: FILE with '.stripped' inserted before the extension)")
    parser.add_argument("--in-place", action="store_true",
                        help="With --strip-metadata, overwrite FILE itself instead of writing a separate output — irreversible, the original EXIF is gone")
    args = parser.parse_args()

    if args.strip_metadata:
        sys.exit(run_strip_metadata(args.strip_metadata, args.out, args.in_place))

    findings = []

    if args.text == "-":
        text = sys.stdin.read()
        findings = scan_text_for_pii(text, location_prefix="<stdin>") + scan_text_for_secrets(text, location_prefix="<stdin>")
    elif args.commit_msg:
        with open(args.commit_msg) as f:
            text = f.read()
        findings = scan_text_for_pii(text, location_prefix=args.commit_msg) + scan_text_for_secrets(text, location_prefix=args.commit_msg)
    elif args.file:
        file_dir = os.path.dirname(os.path.abspath(args.file)) or "."
        repo_root = get_repo_root(cwd=file_dir) or file_dir
        rel_path = os.path.relpath(args.file, repo_root)
        ext = os.path.splitext(args.file)[1].lower()
        if ext in METADATA_EXTENSIONS:
            # Binary-ish type (image/doc) — not meaningfully readable as text;
            # scan it via the metadata path only, not the PII/secret text scanners.
            findings = scan_metadata([rel_path], repo_root=repo_root)
        else:
            with open(args.file, encoding="utf-8", errors="replace") as f:
                text = f.read()
            findings = scan_text_for_pii(text, location_prefix=args.file) + scan_text_for_secrets(text, location_prefix=args.file)
    else:
        repo_root = get_repo_root()
        if repo_root is None:
            print("privacy-linter: not inside a git repository (use --file or --text instead)", file=sys.stderr)
            sys.exit(2)
        findings = scan_staged(repo_root)

    print_report(findings, json_out=args.json)

    if check_gate(findings, args.block_on):
        if not args.json:
            print(f"\nBLOCKED: a finding at or above '{args.block_on}' severity was found.", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
