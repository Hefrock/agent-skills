# Expert Determination — the Track 2 risk model

Safe Harbor (Track 1) is a checklist: remove 18 direct-identifier categories and you
are done. **Expert Determination** (45 CFR §164.514(b)(1)) is the other HIPAA de-id
route — a statistical argument that "the risk is very small that the information could
be used, alone or in combination, to identify an individual." Its currency is not a
checklist but a *probability of re-identification*, computed against a background
population.

The two standards diverge on purpose, and Track 2 exists to make the divergence
measurable: a note can have **every direct identifier removed (Safe Harbor pass)** and
still describe a **unique** person through quasi-identifiers that must clinically remain
— age, sex, ZIP3, a rare diagnosis. That record is an **Expert Determination fail**.
Reporting the Safe Harbor pass as "de-identified" is exactly the error the harness is
built to expose.

## Quasi-identifiers and equivalence classes

A **quasi-identifier (QI)** is a field that is not identifying alone but is identifying
in combination. The manifest's `quasi_identifiers` profile carries them; `qi_model.py`
defines the default generalized QI set and the *single* generalization used by both the
population generator and this scorer (they must bucket identically):

| QI | Treatment | Why |
|----|-----------|-----|
| `age` | 5-year band, `90+` capped | 90+ is itself a Safe Harbor identifier; banding mirrors real ED practice |
| `sex` | raw | low cardinality |
| `zip3` | raw | already Safe Harbor's coarsest geographic unit |
| `rare_diagnosis` | raw boolean | a rare disease is strongly identifying |

`admission_date` (constant year in v0) and `facility_id` (a custodian attribute, not a
personal QI) are carried in the manifest but excluded from the default key. The QI set
is deliberately small and generalized; full-precision QIs make almost everyone trivially
unique and teach nothing.

An **equivalence class** is the set of records sharing one generalized QI key. Its size
is *k*. Two sizes matter:

- **f_i** — the record's class size within the released **sample** (the corpus).
- **F_i** — its class size within the **population** (`population_ref`). The released
  sample is part of the population, so the sample is folded into the population counts
  and `F_i >= 1` always.

## The three attackers (El Emam)

Never collapse them — they answer different questions.

| Attacker | Knows target is in the release? | Per-record risk | Reported as |
|----------|-------------------------------|-----------------|-------------|
| **Prosecutor** | yes | `1 / f_i` | max and mean |
| **Journalist** | no | `1 / F_i` | max and mean |
| **Marketer** | no; wants volume | — | mean of `1 / F_i` |

**k-anonymity** is exactly the prosecutor bound stated as a threshold: the released
sample is *k-anonymous* iff `min_i f_i >= k`. Track 2's default verdict is **k ≥ 5**;
any record whose class is smaller is flagged high-risk. `--k` overrides the threshold.

## Reading the output

`score_reid.py` emits the risk **distribution** and the k-anonymity verdict, never one
averaged number — averaging hides the singletons, which are the whole point. Fields:

- `k_anonymity` — `min_k_sample`, `records_below_k`, `sample_is_k_anonymous`, and a
  `k_histogram_sample` so you see the shape, not just the min.
- `risk.{prosecutor,journalist,marketer}` — max/mean as applicable.
- `safe_harbor_vs_expert_determination` — how many records are Safe-Harbor-clean yet
  QI-unique (within sample, and against the population). This is the headline divergence.
- `caveats` — read them. In v0 the generator draws ZIP3 uniformly, so the QI space is
  far more dispersed than real geography; sample uniqueness (and thus prosecutor /
  k-anonymity) is an **upper bound**. The population-side journalist and marketer risks
  are the load-bearing numbers, and a Synthea-backed population (the `make_person` swap
  point) restores realistic equivalence-class sizes.

## Why this attacker is load-bearing

It is purely statistical — no LLM in the loop — so it can never share a base model with
the defender, and its score never depends on a model's blind spots. That is why the
harness keeps a model-independent attacker on every track it can (Track 1's manifest
check; Track 2's linkage), and reserves LLM judgment for Track 3, where ground truth
genuinely cannot be enumerated.
