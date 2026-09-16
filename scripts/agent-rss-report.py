#!/usr/bin/env python3
"""Render the agent-RSS campaign records into report tables.

Reads every records/*.jsonl and emits GitHub-flavoured
markdown tables on stdout, so the report carries tables that are derived from the
records rather than transcribed by hand.

    scripts/agent-rss-report.py                 # all records
    scripts/agent-rss-report.py --arm default   # one arm

Two derived quantities matter and are computed here rather than eyeballed:

  growth_over_floor   (tree RSS on task - tree RSS on noop) / tree RSS on noop.
                      This is the harness-relative cost of the task, which is
                      what makes classes comparable across harnesses that have
                      very different absolute floors.

  aux_share           share of the peak tree held by anything that is not the
                      harness's own process. Makes the mind-daemon/daemon
                      attribution explicit instead of buried in a total.
"""
import argparse
import glob
import json
import math
import os
import statistics
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
RECORDS = os.path.join(ROOT, "records")

TASK_ORDER = ["noop", "wide_read", "large_read", "pressure_read", "deep_read",
              "aggregate_scan", "noisy_tool", "edit_tool", "build_pkg",
              "long_session"]
# Processes that are auxiliary services rather than the harness itself.
AUX_NAMES = {"mind-daemon", "anvd", "anv-repomap", "__repomap-build", "repomap"}
HARNESS_MAIN = {"anv": "anv", "jcode": "jcode", "codex": "codex",
                "opencode": "opencode", "claude": "claude", "aider": "aider"}


def load(arm=None, source_prefix=None):
    recs = []
    for path in sorted(glob.glob(os.path.join(RECORDS, "*.jsonl"))):
        name = os.path.basename(path)
        if source_prefix and not name.startswith(source_prefix):
            continue
        # Superseded failed attempts are kept as evidence but must not be
        # averaged back into the results they were replaced by.
        if "failed-attempts" in name:
            continue
        for line in open(path):
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
            except json.JSONDecodeError:
                continue
            if d.get("schema") != "agent-rss-run/1":
                continue
            if arm and d.get("arm") != arm:
                continue
            d["_source"] = os.path.basename(path)
            recs.append(d)
    return recs


def r0(v):
    """Round half away from zero: 42.5 -> 43, -4.5 -> -5.

    Python's `round()` and `f"{v:.0f}"` both use banker's rounding (42.5 -> 42),
    which disagrees with what a reader expects and with the tables in
    FINDINGS.md. Every displayed integer goes through here so the generated
    tables and the written report cannot drift apart on a half-tie.
    """
    if v is None:
        return None
    return math.floor(v + 0.5) if v >= 0 else math.ceil(v - 0.5)


def group(recs, keys):
    out = {}
    for r in recs:
        out.setdefault(tuple(r.get(k) for k in keys), []).append(r)
    return out


def nonempty(recs, key):
    vals = [r[key] for r in recs if r.get(key) is not None]
    return vals


def is_main_name(harness, proc):
    """Whether a breakdown process name is the harness's own binary.

    anv is matched by prefix because a pinned benchmark build carries its own
    name (`anv-nomind-bench`), while known aux services (`anvd`,
    `anv-repomap`, ...) stay excluded by name.

    Colon labels are split the same way the runner's `is_main_proc` does it:
    aider's process is `Python:aider`, so an exact `== "aider"` comparison
    silently listed the harness itself as its own largest auxiliary process.
    """
    if harness == "anv":
        return proc.startswith("anv") and proc not in AUX_NAMES
    target = HARNESS_MAIN.get(harness)
    if not target:
        return False
    parts = proc.strip().strip("()").split(":")
    return target in parts


def aux_share(rec):
    """Share of the peak tree held by processes that are not the harness."""
    bd = rec.get("peak_breakdown") or []
    if not bd:
        return None
    total = rec.get("peak_tree_rss_mb") or 0
    if total <= 0:
        return None
    aux = sum(b["rss_mb"] for b in bd
              if not is_main_name(rec["harness"], b["proc"]))
    return round(100.0 * aux / total, 1)


def fmt_series(rec, limit=6):
    s = rec.get("tree_rss_series") or []
    if not s:
        return "—"
    pts = s[:limit]
    return " ".join(str(r0(v)) for _t, v in pts)


# Every table groups by arm as well as harness/model. Arms are different
# experimental conditions (e.g. anv with vs without its mind-daemon), so merging
# them averages a condition against its own control.
GROUP_KEYS = ["harness", "model", "arm"]

# How many distinct arms are in the record set. With one arm the column is noise
# and is dropped, so the common case stays readable.
def arms_in(recs):
    return sorted({r.get("arm") or "default" for r in recs})


def _grid(recs, value_fn, label, note, task_order=None):
    order = task_order or TASK_ORDER
    arms = arms_in(recs)
    show_arm = len(arms) > 1
    g = group(recs, GROUP_KEYS + ["task_id"])
    combos = sorted({(r["harness"], r["model"], r.get("arm") or "default")
                     for r in recs}, key=lambda k: (k[0], k[1], k[2]))
    print(f"### {label}\n")
    if note:
        print(note + "\n")
    cols = "| harness | model |" + (" arm |" if show_arm else "")
    print(cols + " " + " | ".join(order) + " |")
    print("|---|---|" + ("---|" if show_arm else "") + "---|" * len(order))
    for h, m, a in combos:
        cells = []
        for t in order:
            rs = g.get((h, m, a, t))
            cells.append(value_fn(rs) if rs else "—")
        am = f" `{a}` |" if show_arm else ""
        print(f"| `{h}` | `{m}` |{am} " + " | ".join(cells) + " |")
    print()


def table_main(recs):
    def value(rs):
        v = max(r["peak_main_rss_mb"] for r in rs if r.get("peak_main_rss_mb") is not None)
        bad = "" if all(r.get("verified") for r in rs) else " ⚠"
        return f"{r0(v)}{bad}"

    _grid(recs, value, "Headline: harness's own process, peak RSS (MB)",
          "The CLI's own cost. For jcode/codex/opencode this is effectively the "
          "whole tree; for anv it excludes `mind-daemon`.\n\n"
          "⚠ = at least one run on that cell did not verify.")


def table_tree(recs):
    def value(rs):
        return str(r0(max(r['peak_tree_rss_mb'] for r in rs)))

    _grid(recs, value, "Total: whole process tree, peak RSS (MB)",
          "What running the harness actually costs, including every helper it "
          "spawns. The user's real bill; not the same question as the headline.")


def table_footprint(recs):
    print("### Peak footprint by harness, model and task (MB)\n")
    print("Leader process only — see the granularity caveat in README §4.\n")
    g = group(recs, ["harness", "model", "task_id"])
    combos = sorted({(r["harness"], r["model"]) for r in recs},
                    key=lambda k: (k[0], k[1]))
    print("| harness | model | " + " | ".join(TASK_ORDER) + " |")
    print("|---|---|" + "---|" * len(TASK_ORDER))
    for h, m in combos:
        cells = []
        for t in TASK_ORDER:
            rs = g.get((h, m, t))
            vals = [r["peak_footprint_mb"] for r in (rs or [])
                    if r.get("peak_footprint_mb") is not None]
            cells.append(str(r0(max(vals))) if vals else "—")
        print(f"| `{h}` | `{m}` | " + " | ".join(cells) + " |")
    print()


def table_growth(recs):
    print("### Growth over the noop floor (headline RSS, %)\n")
    print("How much *more* the harness's own process needs to do real work,")
    print("relative to its own idle-turn cost. Makes classes comparable across")
    print("harnesses whose absolute floors differ by 10x.\n")
    arms = arms_in(recs)
    show_arm = len(arms) > 1
    g = group(recs, GROUP_KEYS + ["task_id"])
    combos = sorted({(r["harness"], r["model"], r.get("arm") or "default")
                     for r in recs}, key=lambda k: (k[0], k[1], k[2]))
    cls = [t for t in TASK_ORDER if t != "noop"]
    print("| harness | model |" + (" arm |" if show_arm else "") + " "
          + " | ".join(cls) + " |")
    print("|---|---|" + ("---|" if show_arm else "") + "---|" * len(cls))
    for h, m, a in combos:
        base = g.get((h, m, a, "noop"))
        base_v = None
        if base:
            vals = [r["peak_main_rss_mb"] for r in base if r.get("peak_main_rss_mb") is not None]
            base_v = max(vals) if vals else None
        cells = []
        for t in cls:
            rs = g.get((h, m, a, t))
            vals = [r["peak_main_rss_mb"] for r in (rs or [])
                    if r.get("peak_main_rss_mb") is not None]
            if not vals or not base_v:
                cells.append("—")
                continue
            pct = 100.0 * (max(vals) - base_v) / base_v
            cells.append(f"{r0(pct):+d}%")
        am = f" `{a}` |" if show_arm else ""
        print(f"| `{h}` | `{m}` |{am} " + " | ".join(cells) + " |")
    print()


def table_aux(recs):
    def value(rs):
        v = max((r.get("peak_aux_rss_mb") or 0) for r in rs)
        return str(r0(v)) if v else "0"

    _grid(recs, value, "Auxiliary cost: non-harness processes at peak (MB)",
          "Everything in the tree that is not the harness's own process: "
          "auxiliary services, tool children, MCP servers. Reported separately "
          "so a platform daemon is visible without being charged to the CLI.")

    print("### Largest auxiliary process per harness/task\n")
    print("| harness | arm | task | aux MB | share of tree | largest non-harness process |")
    print("|---|---|---|---|---|---|")
    rows = []
    for r in recs:
        bd = r.get("peak_main_breakdown") or []
        aux = [(b["proc"], b["rss_mb"]) for b in bd
               if not is_main_name(r["harness"], b["proc"])]
        if not aux:
            continue
        total = r.get("peak_tree_rss_mb") or 0
        s = round(100.0 * (r.get("peak_aux_rss_mb") or 0) / total, 1) if total else 0
        rows.append((r["harness"], r.get("arm"), r["task_id"],
                     r.get("peak_aux_rss_mb") or 0, s, aux))
    for h, arm, t, aux_mb, s, aux in sorted(rows):
        top = max(aux, key=lambda kv: kv[1])
        print(f"| `{h}` | {arm} | {t} | {r0(aux_mb)} | {s}% | "
              f"`{top[0]}` {r0(top[1])} MB |")
    print()


def table_runtime(recs):
    def value(rs):
        return f"{statistics.median([r['wall_s'] for r in rs]):.1f}"

    _grid(recs, value, "Wall time per task (s, median)",
          "Wall time is dominated by model latency, not harness overhead, and at "
          "n=1 it is high-variance. Read as indicative, not as a ranking.")


def table_integrity(recs):
    print("### Run integrity\n")
    g = group(recs, GROUP_KEYS)
    print("| harness | model | arm | runs | verified | clean exit | timeouts | idle-kills | procs seen | jobs |")
    print("|---|---|---|---|---|---|---|---|---|---|")
    for (h, m, a), rs in sorted(g.items()):
        v = sum(1 for r in rs if r.get("verified"))
        c = sum(1 for r in rs if r.get("clean_exit"))
        to = sum(1 for r in rs if r.get("timed_out"))
        ik = sum(1 for r in rs if r.get("idle_killed"))
        p = max((r.get("processes_seen") or 0) for r in rs)
        jobs = sorted({r.get("parallel_jobs") or 1 for r in rs})
        j = ",".join(str(x) for x in jobs)
        print(f"| `{h}` | `{m}` | `{a}` | {len(rs)} | {v} | {c} | {to} | {ik} | {p} | {j} |")
    print()


def table_latency(recs):
    """Time to first output, where the harness's own stream makes it visible.

    First output is a cheaper, less model-dominated signal than total wall
    time: it is how long the harness takes to say anything at all, including
    session setup and the first request. Total wall remains in the runtime
    table; the two answer different questions.
    """
    def value(rs):
        vals = [r["first_output_s"] for r in rs if r.get("first_output_s") is not None]
        return f"{statistics.median(vals):.1f}" if vals else "—"

    _grid(recs, value, "Time to first output (s, median)",
          "Harness setup + first request, before the model's answer is complete.")


def table_tokens(recs):
    """Optional token accounting, for harnesses that print it themselves.

    Only aider reports 'Tokens: N sent, M received' on its own; anv's ledger
    carries observed tokens in the trace records rather than here. A `—` means
    the harness does not report token counts, not that none were used.
    """
    print("### Token accounting where the harness reports it\n")
    print("| harness | model | arm | runs with tokens | median sent | median received | median wall s | received tok/s |")
    print("|---|---|---|---|---|---|---|---|")
    g = group(recs, GROUP_KEYS)
    for (h, m, a), rs in sorted(g.items()):
        with_tokens = [r for r in rs if r.get("tokens_received")]
        if not with_tokens:
            print(f"| `{h}` | `{m}` | `{a}` | 0 | — | — | — | — |")
            continue
        sent = statistics.median([r.get("tokens_sent") or 0 for r in with_tokens])
        recv = statistics.median([r["tokens_received"] for r in with_tokens])
        wall = statistics.median([r["wall_s"] for r in with_tokens])
        rate = (recv / wall) if wall else 0
        print(f"| `{h}` | `{m}` | `{a}` | {len(with_tokens)}/{len(rs)} | {sent:.0f} | "
              f"{recv:.0f} | {wall:.1f} | {rate:.1f} |")
    print()


def table_versions(recs):
    print("### Builds under test\n")
    print("| harness | version |")
    print("|---|---|")
    seen = {}
    for r in recs:
        seen.setdefault(r["harness"], r.get("harness_version", "unknown"))
    for h in sorted(seen):
        print(f"| `{h}` | `{seen[h]}` |")
    print()


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--arm", default=None)
    p.add_argument("--source-prefix", default=None,
                   help="only records whose file name starts with this (e.g. v3)")
    p.add_argument("--only", default=None,
                   help="emit a single section: versions|main|tree|aux|footprint|"
                        "growth|runtime|latency|tokens|integrity")
    args = p.parse_args()

    recs = load(args.arm, args.source_prefix)
    if not recs:
        print(f"no records found under {os.path.relpath(RECORDS, ROOT)}"
              + (f" for arm {args.arm!r}" if args.arm else ""))
        return 1

    sections = {
        "versions": table_versions,
        "main": table_main,
        "tree": table_tree,
        "aux": table_aux,
        "footprint": table_footprint,
        "growth": table_growth,
        "runtime": table_runtime,
        "latency": table_latency,
        "tokens": table_tokens,
        "integrity": table_integrity,
    }
    for name, fn in sections.items():
        if args.only and name != args.only:
            continue
        fn(recs)
    return 0


if __name__ == "__main__":
    sys.exit(main())
