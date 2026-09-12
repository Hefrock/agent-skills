# Worked example — six seeded findings from two staged files

[`sample_diff.txt`](./sample_diff.txt) is what `git diff --cached -U0` would show for
two staged files: `intake_notes.txt` (six lines, five seeded PII patterns plus one
deliberate near-miss) and `photos/receipt.jpg` (a new binary image file).

**No Python interpreter was available to actually execute this** when the example was
built — the numbers below are hand-traced against `scan_diff.py`'s documented regex and
Luhn logic, the same honesty standard used for `agent-eval`'s trajectory-eval worked
example. Worth a real run (`python scripts/scan_diff.py --file examples/... ` against a
staged copy) before fully trusting the exact wording.

## What's seeded, line by line

| Line | Content | Expected result |
|---|---|---|
| 1 | `jane.doe@example.com` | email, medium |
| 2 | `(555) 123-4567` | phone, medium |
| 3 | `123-45-6789` | ssn, high |
| 4 | `4111 1111 1111 1111` | credit_card, high — Luhn-valid (verified by hand: doubled-digit sum is a multiple of 10) |
| 5 | `10.0.0.42` | ip_address, low |
| 6 | `1234567890123456` | **no finding** — 16 digits, but fails the Luhn check (sum ≡ 4 mod 10), so it's a deliberate near-miss, not a bug |
| `photos/receipt.jpg` (staged file) | — | metadata, medium — `.jpg` is in the metadata-extension set |

## Expected report

```
privacy-linter: 6 finding(s)
  [severity: high] [class: direct_pii] [US Social Security Number format (###-##-####): ssn] Matched ssn pattern in staged addition (intake_notes.txt:3)
  [severity: high] [class: direct_pii] [Luhn-valid card number: credit_card] Matched credit_card pattern in staged addition (intake_notes.txt:4)
  [severity: medium] [class: direct_pii] [Email address: email] Matched email pattern in staged addition (intake_notes.txt:1)
  [severity: medium] [class: direct_pii] [Phone number: phone] Matched phone pattern in staged addition (intake_notes.txt:2)
  [severity: medium] [class: metadata] [File type commonly carries embedded metadata (.jpg)] Verify EXIF/document properties are stripped before committing (Pillow not installed — EXIF not inspected) (photos/receipt.jpg)
  [severity: low] [class: direct_pii] [IPv4 address: ip_address] Matched ip_address pattern in staged addition (intake_notes.txt:5)

(Pillow not installed — EXIF GPS confirmation skipped; image/doc files still flagged by file type. `pip install pillow` to enable the stronger check.)
```

Exit code **0** — advisory by default, even with two `high` findings. Add
`--block-on high` to turn this into a gate; against this exact set it would exit 1.

## What this demonstrates

1. **Ordering is severity-first, stable within a tier** — both `high` findings surface
   before any `medium`, matching the "lead with the worst finding" discipline
   `repo-pincer` and `agent-eval` both use.
2. **Luhn validation is load-bearing, not decorative** — line 6 is the same length as a
   real card number and would false-positive on a naive "13-19 digits" regex; the Luhn
   check is what keeps it out of the report.
3. **The Pillow-unavailable case is reported, not hidden** — the `.jpg` finding says
   explicitly that only the weaker file-type heuristic ran, not the confirmed EXIF GPS
   check. If Pillow were installed and this were a real file with GPS EXIF data, this
   line would instead read `high` / "Embedded GPS location (EXIF)".

## Running it for real

```bash
cd skills/privacy-linter
git init -q /tmp/privacy-linter-demo && cd /tmp/privacy-linter-demo
printf 'patient contact: jane.doe@example.com\nbackup phone: (555) 123-4567\nssn on file: 123-45-6789\ncard used: 4111 1111 1111 1111\nserver ip for staging: 10.0.0.42\norder reference: 1234567890123456\n' > intake_notes.txt
git add intake_notes.txt
python /path/to/agent-skills/skills/privacy-linter/scripts/scan_diff.py
```
