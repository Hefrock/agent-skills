#!/usr/bin/env python3
"""
scan_diff.py — deterministic pre-disclosure privacy scanner (v1: Direct PII + Metadata).

Scans staged git changes (or an arbitrary file/stdin) for two mechanically-detectable
leak classes from the Pre-Disclosure Privacy Linter design: Direct PII (email, phone,
SSN, Luhn-valid credit card, IPv4) and Metadata (file types that commonly carry EXIF/
document properties, plus a confirmed EXIF GPS check when Pillow is installed).

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

Stdlib only for the core scan. Pillow is optional — EXIF GPS confirmation degrades
gracefully to a file-type heuristic when it isn't installed (reported, not silent).
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


# --- Direct PII scanning over text ----------------------------------------------------

def scan_text_for_pii(text, location_prefix):
    findings = []
    for lineno, line in enumerate(text.splitlines(), 1):
        if SUPPRESS_MARKER in line:
            continue
        for leak_class, pattern, severity, label, validate in PII_SCANNERS:
            for m in pattern.finditer(line):
                if not validate(m):
                    continue
                findings.append(Finding(
                    severity=severity,
                    leak_class="direct_pii",
                    finding=f"{label}: {leak_class}",
                    reason=f"Matched {leak_class} pattern",
                    location=f"{location_prefix}:{lineno}",
                ))
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
        for leak_class, pattern, severity, label, validate in PII_SCANNERS:
            for m in pattern.finditer(content):
                if not validate(m):
                    continue
                findings.append(Finding(
                    severity=severity,
                    leak_class="direct_pii",
                    finding=f"{label}: {leak_class}",
                    reason=f"Matched {leak_class} pattern in staged addition",
                    location=f"{path}:{lineno}",
                ))
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
    args = parser.parse_args()

    findings = []

    if args.text == "-":
        text = sys.stdin.read()
        findings = scan_text_for_pii(text, location_prefix="<stdin>")
    elif args.commit_msg:
        with open(args.commit_msg) as f:
            text = f.read()
        findings = scan_text_for_pii(text, location_prefix=args.commit_msg)
    elif args.file:
        file_dir = os.path.dirname(os.path.abspath(args.file)) or "."
        repo_root = get_repo_root(cwd=file_dir) or file_dir
        rel_path = os.path.relpath(args.file, repo_root)
        ext = os.path.splitext(args.file)[1].lower()
        if ext in METADATA_EXTENSIONS:
            # Binary-ish type (image/doc) — not meaningfully readable as text;
            # scan it via the metadata path only, not the PII text scanner.
            findings = scan_metadata([rel_path], repo_root=repo_root)
        else:
            with open(args.file, encoding="utf-8", errors="replace") as f:
                text = f.read()
            findings = scan_text_for_pii(text, location_prefix=args.file)
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
