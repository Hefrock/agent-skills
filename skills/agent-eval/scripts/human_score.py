#!/usr/bin/env python3
"""human_score.py - Blind, resumable human scoring for agent-eval cases, in
installments. Produces the human_scores.jsonl that calibrate_judge.py reads.

Four subcommands:

  prepare  cases.jsonl judge_results.jsonl --workdir DIR [--seed N]
           Freezes a blinded queue once. Writes DIR/queue.jsonl (opaque
           blind ids plus only the fields you will see) and DIR/key.json
           (blind id -> real id + category). Do not open key.json until
           scoring is done. Refuses to overwrite an existing workdir
           unless --force, because re-shuffling would break resumability.
  score    --workdir DIR [--rubric FILE] [--minutes N]
           Interactive loop. Shows the next unscored item and appends every
           score to DIR/scores.jsonl immediately. Quit any time (q or
           Ctrl-C); the next run resumes at the first unscored item.
  rescore  --workdir DIR --ids b123,b456 [--rubric FILE]
           Re-shows chosen already-scored items (your previous score and
           note only, never the judge's) and appends a revised score. The
           original stays in the log; `export --which first` recovers it.
  status   --workdir DIR
           Counts only; never prints scores or the key.
  export   --workdir DIR --out human_scores.jsonl
           Joins the latest score per blind id to the key and writes rows in
           the score_eval.py schema ({id, score, category, rationale}).

Ordering: the judge's near-threshold cases come first (they carry the most
information about whether the judge is right), then everything else is
interleaved across categories. The human never sees the judge's scores,
the category, the real id or the order logic.

Blinding limit: only the fields named by --show-fields are copied into the
queue, but the text of an output can itself reveal which version produced
it. That cannot be stripped automatically; check a few items yourself.

Stdlib only. Run: python human_score.py prepare ... """

import argparse
import json
import os
import random
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import jsonl_io  # noqa: E402

DEFAULT_SHOW_FIELDS = ("input", "output")
# Case fields that would hand the scorer a real id, a category or a score (or
# overwrite the blind id), so they can never be shown.
RESERVED_FIELDS = ("blind_id", "id", "category", "score")
DEFAULT_THRESHOLD = 0.7
DEFAULT_PRIORITY_N = 15
DEFAULT_MINUTES = 35

QUEUE = "queue.jsonl"
KEY = "key.json"
SCORES = "scores.jsonl"


# ---------- prepare ----------

def order_ids(judge_rows: list[dict], threshold: float, priority_n: int, rng: random.Random) -> list[str]:
    """Priority block: the priority_n ids whose judge score is closest to
    threshold, shuffled among themselves so closeness isn't recoverable
    from position. Remainder: shuffled per category, then interleaved
    round-robin so any prefix is a balanced sample across categories."""
    ranked = sorted(judge_rows, key=lambda r: abs(float(r["score"]) - threshold))
    priority = [r["id"] for r in ranked[:priority_n]]
    rng.shuffle(priority)
    pset = set(priority)
    by_cat = defaultdict(list)
    for r in judge_rows:
        if r["id"] not in pset:
            by_cat[r.get("category", "")].append(r["id"])
    cats = sorted(by_cat)
    for c in cats:
        rng.shuffle(by_cat[c])
    rest = []
    while any(by_cat[c] for c in cats):
        for c in cats:
            if by_cat[c]:
                rest.append(by_cat[c].pop())
    return priority + rest


def new_blind_ids(n: int, rng: random.Random) -> list[str]:
    """Opaque, unique, order-independent ids (not a counter, so position in
    the queue isn't encoded in the id)."""
    pool = rng.sample(range(10**5, 10**6), n)
    return [f"b{p}" for p in pool]


def build_queue(cases: list[dict], judge_rows: list[dict], show_fields, threshold, priority_n, seed):
    rng = random.Random(seed)
    case_by_id = {c["id"]: c for c in cases}
    judged = [r for r in judge_rows if r["id"] in case_by_id]
    missing = [r["id"] for r in judge_rows if r["id"] not in case_by_id]
    ordered = order_ids(judged, threshold, priority_n, rng)
    blind = new_blind_ids(len(ordered), rng)
    cat_by_id = {r["id"]: r.get("category", "") for r in judged}
    queue, key = [], {}
    for bid, real in zip(blind, ordered):
        case = case_by_id[real]
        item = {"blind_id": bid}
        for f in show_fields:
            if f in case:
                item[f] = case[f]
        queue.append(item)
        key[bid] = {"id": real, "category": cat_by_id[real]}
    return queue, key, missing


def cmd_prepare(args) -> int:
    if os.path.exists(os.path.join(args.workdir, QUEUE)) and not args.force:
        print(f"Refusing: {args.workdir}/{QUEUE} already exists. Re-preparing would reshuffle the queue and break resumability. Use --force only to start over (existing {SCORES} is kept, but its blind ids will no longer match).", file=sys.stderr)
        return 2
    cases = jsonl_io.load_jsonl(args.cases, required_keys=("id",))
    judge_rows = jsonl_io.load_jsonl(args.judge_results, required_keys=("id", "score"))
    if not cases or not judge_rows:
        print("Refusing: no usable rows in cases or judge results.", file=sys.stderr)
        return 2
    show_fields = tuple(f.strip() for f in args.show_fields.split(",") if f.strip())
    reserved = [f for f in show_fields if f in RESERVED_FIELDS]
    if reserved:
        print(f"Refusing: --show-fields {reserved} would show the scorer a real id, category or score (or overwrite the blind id). Put what you want shown in a differently named case field.", file=sys.stderr)
        return 2
    queue, key, missing = build_queue(cases, judge_rows, show_fields, args.threshold, args.priority_n, args.seed)
    if not queue:
        print("Refusing: no judge result id matches any case id.", file=sys.stderr)
        return 2
    if missing:
        print(f"Warning: {len(missing)} judge result id(s) have no matching case and were left out: {missing[:5]}", file=sys.stderr)
    shown = [f for f in show_fields if any(f in q for q in queue)]
    if not shown:
        print(f"Refusing: none of --show-fields {show_fields} exist in the cases.", file=sys.stderr)
        return 2
    os.makedirs(args.workdir, exist_ok=True)
    with open(os.path.join(args.workdir, QUEUE), "w", encoding="utf-8") as f:
        for q in queue:
            f.write(json.dumps(q, ensure_ascii=False) + "\n")
    with open(os.path.join(args.workdir, KEY), "w", encoding="utf-8") as f:
        json.dump(key, f, indent=1, ensure_ascii=False)
    print(f"Prepared {len(queue)} items in {args.workdir} (showing fields: {', '.join(shown)}).")
    if args.priority_n >= len(queue):
        print(f"All {len(queue)} items fall in the near-threshold block (--priority-n {args.priority_n} >= item count), so the order is a plain shuffle: no near-threshold-first ordering and no category interleave. Do NOT open {KEY} until you finish scoring.")
    else:
        print(f"First {args.priority_n} are the judge's near-threshold cases. Do NOT open {KEY} until you finish scoring.")
    return 0


# ---------- shared state ----------

def load_queue(workdir: str) -> list[dict]:
    path = os.path.join(workdir, QUEUE)
    if not os.path.exists(path):
        raise SystemExit(f"No {QUEUE} in {workdir}. Run `prepare` first.")
    return jsonl_io.load_jsonl(path, required_keys=("blind_id",))


def load_latest_scores(workdir: str) -> dict:
    """{blind_id: row}, latest line wins (a re-score appends, never edits)."""
    path = os.path.join(workdir, SCORES)
    if not os.path.exists(path):
        return {}
    rows = jsonl_io.load_jsonl(path, required_keys=("blind_id", "score"))
    latest = {}
    for r in rows:
        latest[r["blind_id"]] = r
    return latest


def load_first_scores(workdir: str) -> dict:
    """{blind_id: row}, the FIRST line per id: your original, still-blind
    score, before any 'b' or rescore revision."""
    path = os.path.join(workdir, SCORES)
    if not os.path.exists(path):
        return {}
    rows = jsonl_io.load_jsonl(path, required_keys=("blind_id", "score"))
    first = {}
    for r in rows:
        first.setdefault(r["blind_id"], r)
    return first


def last_scored_blind_id(workdir: str):
    """The blind id on the final line of scores.jsonl (file order, not
    timestamp order: timestamps only have one-second resolution)."""
    path = os.path.join(workdir, SCORES)
    if not os.path.exists(path):
        return None
    rows = jsonl_io.load_jsonl(path, required_keys=("blind_id", "score"))
    return rows[-1]["blind_id"] if rows else None


def append_score(workdir: str, blind_id: str, score: float, note: str, revised: bool = False) -> None:
    row = {"blind_id": blind_id, "score": score, "note": note, "ts": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    if revised:
        row["revised"] = True
    path = os.path.join(workdir, SCORES)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")
        f.flush()
        os.fsync(f.fileno())


def next_unscored(queue: list[dict], scored: dict):
    for item in queue:
        if item["blind_id"] not in scored:
            return item
    return None


# ---------- score ----------

def parse_score(text: str):
    """Returns (score, note) or None if invalid. Accepts '0.8' or '0.8 reason'."""
    parts = text.strip().split(None, 1)
    if not parts:
        return None
    try:
        value = float(parts[0])
    except ValueError:
        return None
    if not (0.0 <= value <= 1.0):
        return None
    return value, (parts[1] if len(parts) > 1 else "")


EXTREME_SCORES = (0.0, 1.0)


def confirm_extreme(parsed, ask=input):
    """Read-back for a score of exactly 0.0 or 1.0, the typo-prone ends of
    the scale (a 0.0 typed for 0.9 skewed this tool's first real run).
    Enter or 'y' keeps it; 'n' discards it so the caller re-prompts; a valid
    score typed instead replaces it (and is itself confirmed if extreme).
    Returns (score, note), or None when not confirmed, including EOF and
    Ctrl-C, so an unconfirmed extreme is never saved."""
    while parsed[0] in EXTREME_SCORES:
        try:
            reply = ask(f"{parsed[0]} is an extreme score. Enter/y to keep, n to discard, or type a replacement: ").strip()
        except (EOFError, KeyboardInterrupt):
            return None
        if reply == "" or reply.lower() == "y":
            return parsed
        if reply.lower() == "n":
            return None
        replacement = parse_score(reply)
        if replacement is None:
            print("Not a score between 0.0 and 1.0.")
            continue
        parsed = replacement
    return parsed


def render_item(item: dict, show_fields) -> str:
    chunks = []
    for f in show_fields:
        if f in item and f not in RESERVED_FIELDS:
            body = item[f]
            if not isinstance(body, str):
                body = json.dumps(body, indent=2, ensure_ascii=False)
            chunks.append(f"--- {f.upper()} ---\n{body}")
    return "\n\n".join(chunks)


def limit_reached(deadline, now: float) -> bool:
    """deadline is None when --minutes is 0 (no limit)."""
    return deadline is not None and now > deadline


def cmd_score(args) -> int:
    queue = load_queue(args.workdir)
    rubric = ""
    if args.rubric:
        with open(args.rubric, encoding="utf-8") as f:
            rubric = f.read().strip()
    show_fields = tuple(f.strip() for f in args.show_fields.split(",") if f.strip())
    deadline = time.monotonic() + args.minutes * 60 if args.minutes else None
    done_this_session = 0
    while True:
        scored = load_latest_scores(args.workdir)
        item = next_unscored(queue, scored)
        if item is None:
            print(f"All {len(queue)} items scored. Next: `export`.")
            return 0
        if limit_reached(deadline, time.monotonic()):
            print(f"Session limit of {args.minutes} min reached after {done_this_session} item(s). {len(scored)} of {len(queue)} done. Resume later with the same command.")
            return 0
        print("\n" + "=" * 70)
        print(f"Item {len(scored) + 1} of {len(queue)}")
        if rubric:
            print("\nRUBRIC:\n" + rubric + "\n")
        print(render_item(item, show_fields))
        print("\nScore 0.0-1.0 (optionally followed by a note), 'b' re-score previous, 'q' quit.")
        while True:
            try:
                text = input("score> ")
            except (EOFError, KeyboardInterrupt):
                print(f"\nStopped. {len(scored)} of {len(queue)} done. Resume with the same command.")
                return 0
            cmd = text.strip().lower()
            if cmd == "q":
                print(f"Stopped. {len(scored)} of {len(queue)} done. Resume with the same command.")
                return 0
            if cmd == "b":
                last_id = last_scored_blind_id(args.workdir)
                last = next((q for q in queue if q["blind_id"] == last_id), None)
                if last is None:
                    print("Nothing to go back to.")
                    continue
                print("\nRe-scoring previous item (the new score is appended; the latest wins):\n")
                print(render_item(last, show_fields))
                try:
                    text2 = input("score> ")
                except (EOFError, KeyboardInterrupt):
                    print(f"\nStopped. {len(scored)} of {len(queue)} done. Resume with the same command.")
                    return 0
                parsed2 = parse_score(text2)
                if parsed2 is not None and not getattr(args, "no_confirm_extremes", False):
                    parsed2 = confirm_extreme(parsed2)
                    if parsed2 is None:
                        print("Not saved; nothing changed.")
                        print("\nCurrent item again. Score 0.0-1.0, 'b', or 'q'.")
                        continue
                if parsed2 is None:
                    print("Not a score between 0.0 and 1.0; nothing changed.")
                else:
                    append_score(args.workdir, last["blind_id"], parsed2[0], parsed2[1], revised=True)
                    print("Re-score saved. Returning to the current item.")
                print("\nCurrent item again. Score 0.0-1.0, 'b', or 'q'.")
                continue
            parsed = parse_score(text)
            if parsed is None:
                print("Not a score between 0.0 and 1.0. Try again (e.g. 0.8 or '0.3 wrong fix').")
                continue
            if not getattr(args, "no_confirm_extremes", False):
                parsed = confirm_extreme(parsed)
                if parsed is None:
                    print("Not saved. Enter the score again.")
                    continue
            append_score(args.workdir, item["blind_id"], parsed[0], parsed[1])
            done_this_session += 1
            break


def cmd_rescore(args) -> int:
    """Re-show chosen, already-scored items and append a revised score.
    Shows only YOUR previous score and note, never the judge's. The
    original line stays in scores.jsonl; `export --which first` recovers
    the original, still-blind scores."""
    queue = {q["blind_id"]: q for q in load_queue(args.workdir)}
    scored = load_latest_scores(args.workdir)
    rubric = ""
    if args.rubric:
        with open(args.rubric, encoding="utf-8") as f:
            rubric = f.read().strip()
    show_fields = tuple(f.strip() for f in args.show_fields.split(",") if f.strip())
    ids = [i.strip() for i in args.ids.split(",") if i.strip()]
    bad = [i for i in ids if i not in queue or i not in scored]
    if bad:
        print(f"Refusing: not a scored item in this queue: {bad}", file=sys.stderr)
        return 2
    for n, bid in enumerate(ids, 1):
        prev = scored[bid]
        print("\n" + "=" * 70)
        print(f"Rescoring {n} of {len(ids)}")
        if rubric:
            print("\nRUBRIC:\n" + rubric + "\n")
        print(render_item(queue[bid], show_fields))
        print(f"\nYour previous score: {prev['score']}" + (f"  note: {prev['note']}" if prev.get("note") else ""))
        print("New score 0.0-1.0 (optionally with a note), Enter to keep, 'q' to stop.")
        while True:
            try:
                text = input("rescore> ")
            except (EOFError, KeyboardInterrupt):
                print("\nStopped.")
                return 0
            if text.strip().lower() == "q":
                print("Stopped.")
                return 0
            if text.strip() == "":
                print("Kept.")
                break
            parsed = parse_score(text)
            if parsed is None:
                print("Not a score between 0.0 and 1.0. Try again, or Enter to keep.")
                continue
            if not getattr(args, "no_confirm_extremes", False):
                parsed = confirm_extreme(parsed)
                if parsed is None:
                    print("Not saved. Enter the score again, or Enter to keep.")
                    continue
            append_score(args.workdir, bid, parsed[0], parsed[1], revised=True)
            print("Revised score saved (original kept in the log).")
            break
    return 0


# ---------- status / export ----------

def cmd_status(args) -> int:
    queue = load_queue(args.workdir)
    scored = load_latest_scores(args.workdir)
    n_scored = len([q for q in queue if q["blind_id"] in scored])
    print(f"{n_scored} of {len(queue)} scored; {len(queue) - n_scored} remaining.")
    return 0


def build_export_rows(key: dict, scored: dict) -> list[dict]:
    rows = []
    for bid, s in scored.items():
        if bid not in key:
            continue
        rows.append({
            "id": key[bid]["id"],
            "score": float(s["score"]),
            "category": key[bid].get("category", ""),
            "rationale": s.get("note") or "human score",
        })
    return rows


def cmd_export(args) -> int:
    key_path = os.path.join(args.workdir, KEY)
    if not os.path.exists(key_path):
        print(f"No {KEY} in {args.workdir}.", file=sys.stderr)
        return 2
    with open(key_path, encoding="utf-8") as f:
        key = json.load(f)
    queue = load_queue(args.workdir)
    scored = load_first_scores(args.workdir) if args.which == "first" else load_latest_scores(args.workdir)
    unknown = [b for b in scored if b not in key]
    if unknown:
        print(f"Warning: {len(unknown)} score(s) have a blind id not in the key and were left out (was the queue re-prepared?).", file=sys.stderr)
    rows = build_export_rows(key, scored)
    if not rows:
        print("Nothing to export: no scores match the key.", file=sys.stderr)
        return 2
    with open(args.out, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(f"Wrote {len(rows)} of {len(queue)} human scores to {args.out}.")
    first, latest = load_first_scores(args.workdir), load_latest_scores(args.workdir)
    changed = [b for b in latest if b in key and first.get(b, {}).get("score") != latest[b]["score"]]
    if changed:
        print(f"Note: {len(changed)} score(s) were changed after the first entry (by `b` or `rescore`). --which first reports the first entry, which ignores the change even if it fixed a slip; --which latest reports the revision. Say which you used.")
    if len(rows) < len(queue):
        print("Partial export is fine: calibrate_judge.py compares only ids present in both files.")
    print(f"Next: python calibrate_judge.py <judge_results.jsonl> {args.out}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("prepare")
    p.add_argument("cases")
    p.add_argument("judge_results")
    p.add_argument("--workdir", required=True)
    p.add_argument("--seed", type=int, default=None, help="Shuffle seed. Default: random. The queue is frozen on disk either way.")
    p.add_argument("--threshold", type=float, default=DEFAULT_THRESHOLD, help=f"Pass mark used to pick near-threshold cases first (default {DEFAULT_THRESHOLD}, score_eval.py's default).")
    p.add_argument("--priority-n", type=int, default=DEFAULT_PRIORITY_N)
    p.add_argument("--show-fields", default=",".join(DEFAULT_SHOW_FIELDS), help="Case fields shown to the human, comma-separated. Anything else (including version labels) is never copied into the queue.")
    p.add_argument("--force", action="store_true")
    p.set_defaults(func=cmd_prepare)

    p = sub.add_parser("score")
    p.add_argument("--workdir", required=True)
    p.add_argument("--rubric", help="Text file shown above every item.")
    p.add_argument("--minutes", type=int, default=DEFAULT_MINUTES, help=f"Stop offering new items after this many minutes (default {DEFAULT_MINUTES}; 0 = no limit).")
    p.add_argument("--show-fields", default=",".join(DEFAULT_SHOW_FIELDS))
    p.add_argument("--no-confirm-extremes", action="store_true", help="Skip the read-back on scores of exactly 0.0 or 1.0 (for scripted input).")
    p.set_defaults(func=cmd_score)

    p = sub.add_parser("status")
    p.add_argument("--workdir", required=True)
    p.set_defaults(func=cmd_status)

    p = sub.add_parser("export")
    p.add_argument("--workdir", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--which", choices=("latest", "first"), default="latest", help="latest (default): after any revisions. first: your original blind scores only.")
    p.set_defaults(func=cmd_export)

    p = sub.add_parser("rescore")
    p.add_argument("--workdir", required=True)
    p.add_argument("--ids", required=True, help="Comma-separated blind ids to re-show.")
    p.add_argument("--rubric")
    p.add_argument("--show-fields", default=",".join(DEFAULT_SHOW_FIELDS))
    p.add_argument("--no-confirm-extremes", action="store_true", help="Skip the read-back on scores of exactly 0.0 or 1.0 (for scripted input).")
    p.set_defaults(func=cmd_rescore)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
