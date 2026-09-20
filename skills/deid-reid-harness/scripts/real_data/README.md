# real_data/ — where real, DUA-governed corpora go, and only here

If you're working with n2c2, MIMIC-IV, or any other real, DUA-governed clinical
corpus, put it under this directory. Everything inside `real_data/` is gitignored
except this README, regardless of filename or format — that's the point: this repo
is public, and a real corpus should never be one `git add .` away from being
committed to it.

## Why this exists

Before this directory existed, `scripts/.gitignore` protected only the harness's own
default *output* filenames (`corpus.json`, `population.jsonl`, etc.) — a real corpus
saved under any other name, anywhere else in `scripts/`, had zero automatic
protection. See [issue #126](https://github.com/Hefrock/agent-skills/issues/126),
Tier 1, and [`references/data-sources.md`](../references/data-sources.md) for the
full context.

## Rules

1. **Nothing under here is ever committed.** If you need to check that, run
   `git status --ignored` from the repo root — every file under `real_data/` other
   than this README should show up as ignored, not untracked.
2. **This protects against an accidental commit, not against the harness itself
   mishandling the data once it's loaded.** It doesn't replace the human compliance
   review Tier 1 also requires, and it doesn't say anything about whether Track 3's
   inference attacker is safe to point at this data — see `inference_attackers.py`'s
   `calls_external_api` gate for that separate, code-enforced restriction.
3. **This directory existing is not permission to put real data here.** Check the
   specific DUA's terms first — see `references/data-sources.md`'s "Before use"
   notes for n2c2 and MIMIC-IV.
