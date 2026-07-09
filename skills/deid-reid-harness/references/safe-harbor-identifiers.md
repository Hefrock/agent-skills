# Safe Harbor's 18 identifiers — the Track 1 test taxonomy

HIPAA's Safe Harbor method (45 CFR §164.514(b)(2)) is a *checklist* standard: remove
these 18 categories of identifiers (for the individual and for relatives, employers,
and household members) and the data is de-identified by construction. That checklist
nature is exactly why Track 1 is deterministically scorable — coverage is defined by
the list, and each test maps to a specific category.

Track 1 measures, per category: was every injected instance removed? Coverage is the
cross product of **category × surface form × context**, so a scrubber that catches
dashed phone numbers in a header but misses spelled-out ones mid-narrative shows up as
a partial pass, not a pass.

## The 18 categories (canonical `hipaa_category` values)

| # | `hipaa_category` | What it covers |
|---|------------------|----------------|
| 1 | `name` | Names of the patient, relatives, employers, household members |
| 2 | `geo_subdivision` | Geographic units smaller than a state: street, city, county, precinct, and ZIP (ZIP3 allowed only under the population rule) |
| 3 | `date` | All date elements (except year) tied to an individual: birth, admission, discharge, death; all ages > 89 |
| 4 | `phone_fax` | Telephone numbers (and, historically listed separately, fax numbers) |
| 5 | `fax` | Fax numbers — kept distinct so a scrubber can be scored on it separately |
| 6 | `email` | Email addresses |
| 7 | `ssn` | Social Security numbers |
| 8 | `mrn` | Medical record numbers |
| 9 | `health_plan_id` | Health plan beneficiary numbers |
| 10 | `account_number` | Account numbers |
| 11 | `license_number` | Certificate / license numbers |
| 12 | `vehicle_id` | Vehicle identifiers and serial numbers, including license plates |
| 13 | `device_id` | Device identifiers and serial numbers |
| 14 | `url` | Web URLs |
| 15 | `ip_address` | IP addresses |
| 16 | `biometric_id` | Biometric identifiers (finger, retinal, voice prints) |
| 17 | `photo` | Full-face photographs and comparable images |
| 18 | `other_unique_id` | Any other unique identifying number, characteristic, or code |

Notes that matter for scoring:
- Categories 4 and 5 are split so phone vs. fax recall can be reported independently,
  even though the regulation groups them.
- ZIP3 is *permitted* under Safe Harbor when the three-digit area's population exceeds
  20,000, but the Track 1 scorer still counts a surviving ZIP3 as leakage — a deliberate
  conservative choice, since the harness can't verify the population rule and ZIP3 is a
  quasi-identifier for Track 2 regardless.
- Category 3 (`date`) is the highest-yield leakage source in real notes because dates
  hide in narrative prose ("seen again three weeks after her March admission"), not
  just structured fields — the surface-form layer must exercise this.
- Category 18 (`other_unique_id`) is the catch-all that a checklist scrubber cannot
  fully enumerate; it is where Safe Harbor and Expert Determination start to diverge,
  and a good place to show that checklist compliance ≠ low re-identification risk.

## The standard this track does NOT cover

Safe Harbor says nothing about *combinations* of non-identifiers (age + ZIP3 + rare
diagnosis) that jointly single someone out. That is Expert Determination's domain and
Track 2's job. Reporting a clean Safe Harbor score as if it meant "safe" is the exact
error this harness is designed to expose — so Track 1 results must always be labeled
as Safe Harbor coverage, never as re-identification risk.
