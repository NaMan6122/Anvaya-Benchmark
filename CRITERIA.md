# Agent Harness Benchmark — Criteria and Method

**Public methodology for the agent-RSS v3 benchmark.**
This document exists so the results can be audited, replicated, or disputed on
method rather than on vibes. Raw records, corpus generator, and measurement
scripts are in this repository.

---

## 1. What the benchmark claims to answer

Three questions, measured independently and never blended into one score:

| # | Question | Metric |
|---|---|---|
| 1 | What does the CLI itself cost in memory while doing real work? | **peak own-process RSS** (headline), plus whole-tree RSS and kernel footprint |
| 2 | How fast does it finish the same work on the same model? | **wall time** and **time to first output** |
| 3 | Does it actually complete the task, not just talk about it? | **execution rate** = objectively verified runs / runs |

A harness that is small but cannot do the work, or fast but incorrect, should
lose on the other columns — which is why one score is never computed.

## 2. Fairness principles

These are constraints we imposed on ourselves before the campaign:

1. **Same model, same gateway, every harness.** No vendor defaults, no
   "best model for me" lanes. All six harnesses are pointed at the *same*
   operator gateways (`siemens:qwen-3.8-27b`, `opencode:deepseek-v4.1-flash`,
   `opencode:glm-5.3-flash`) through each tool's supported provider mechanism.
2. **Same prompt, same workspace.** Every harness receives the identical,
   harness-neutral prompt (it says read/write/run — never a tool name), seeded
   from the same deterministic fixture in a fresh temp directory.
3. **No human in the loop.** Headless mode, auto-approval, MCP off, telemetry
   off, no hints, no retries toward a win.
4. **Objective completion only.** A run counts as verified only if an external
   verifier passes *and* the harness's own output shows it responded. Model
   self-reports are never accepted.
5. **Everything is published.** Corpus generator, campaign scripts, raw JSONL
   records, build fingerprints, and these criteria ship with the results.
6. **Our own product is disclosed.** One of the harnesses (anv) is ours. We
   state that up front and we pre-commit to the claim policy in §8.

## 3. Measured quantities — exact definitions

Per run, sampled from a process the benchmark owns:

| Metric | Definition |
|---|---|
| `peak_main_rss_mb` | max over samples (250 ms) of `ps` RSS summed over processes whose executable is the harness itself (colon labels like `Python:aider` and pinned binary names are matched) |
| `peak_aux_rss_mb` | max over samples of (whole-tree RSS − own-process RSS) — services and tool children, reported, never folded into the headline |
| `peak_tree_rss_mb` | max over samples of RSS summed over every *ever-discovered descendant* of the run leader, which keeps detached daemons in the total while excluding unrelated pre-existing processes |
| `peak_footprint_mb` | kernel `phys_footprint` (`footprint -p`) on the leader process, sampled every 4th tick — reported because macOS `ps` RSS includes shared pages and excludes compressed pages |
| `wall_s` | start of process to exit, summed over continuation turns |
| `first_output_s` | time to the first byte on stdout |
| `verified` | verifier return code 0 **and** every `expect_stdout_contains` present **and** the harness's own stdout contains the declared evidence token |
| `clean_exit` | process returned 0 with no timeout or idle-kill on any turn |

Tree membership rule: a process counts once **seen as a descendant** of the run
leader. This is what makes a daemon comparable across architectures — an anv
daemon, a jcode daemon, and a codex `git` child are all counted while they are
in the tree, and none survive the run.

## 4. Corpus design

**Deterministic** (seed `20260915`), **harness-neutral**, and **objectively
verifiable**. Corpus v3: 483 files / ~2.9 MB, schema `agent-rss-corpus/3`,
hash `205507698927dd67`.

| Class | Pressure it creates | Verification |
|---|---|---|
| `noop` | agent loop + model-client floor | output contains `READY` |
| `wide_read` | traversal + tool-result churn over ~200 modules | unique needle among 41 decoys |
| `large_read` | large tool-result buffering, 3 × ~200 KB | tail facts combined |
| `pressure_read` | 4 × ~350 KB briefs, compaction pressure | fragment from each tail, joined |
| `deep_read` | big resident context, 8 × ~25 KB volumes | decision stated in volume 1 |
| `aggregate_scan` | 250 files, exact arithmetic | sum matches, whole line |
| `noisy_tool` | ~2 MB from one command | tail token extracted |
| `edit_tool` | fix/create/test pipeline | test suite green in fresh workspace |
| `build_pkg` | 3 stub modules + spec + tests | `unittest` green |
| `long_session` | 6 continuation turns | turn-1 codename + 3 facts survive to turn 6 |

Every verifier was validated in both directions: **fails on the pristine seed,
passes on a known-good solution**. This is what prevents "did nothing" from
scoring as success.

## 5. Execution protocol

1. **Waves, not one long queue.** A/A2 single-turn (parallel, jobs 6, gated on
   ≥4 GB free memory) → B/B2 six-turn continuation (jobs 3) → **C serial RSS
   anchor** (jobs 1). The parallel waves buy coverage; the serial anchor is
   what the memory ranking quotes, because macOS reclaims pages under memory
   pressure and that deflates parallel RSS.
2. **Isolation per run.** Fresh seed outside the repo; per-run config dirs so
   parallel runs cannot see each other's sessions; session continuation is
   scoped to the run.
3. **Timeouts are policy, published.** Per-run cap 1800 s; idle cap 900 s. An
   idle cap shorter than a model's first-token latency on large reads measures
   the timeout, not the harness — the 300 s attempts that were killed for this
   reason are preserved under `records/archive/` and were re-run at 900 s.
4. **Append-only evidence.** One JSONL record per run, flushed immediately.
   `--resume` only completes missing cells; when a cell had to be re-run, the
   failed attempt is archived, not overwritten.
5. **Whole-tree teardown** after every run (SIGTERM then SIGKILL, including
   detached members), so no daemon can inflate the next run.
6. **Mode conformance is measured, never assumed.** For anv's `--no-mind`
   mode, a run only counts if no daemon process carries the workspace path, no
   `.anvaya/mind/` store appears, and auxiliary RSS is 0 — because the switch
   leaves no trace in `--version`.

## 6. Environment and provenance

- Machine: Apple M5 Pro MacBook Pro, 24 GB RAM, macOS; runs serialised for the
  anchor and gated for parallel waves.
- Every record carries the harness build fingerprint (content hash, size,
  mtime) and the corpus hash. `--version` alone is not accepted as identity.
- Harnesses under test: `anv` 0.1.0 (benchmark default `--no-mind`);
  `jcode` 0.84.0; `codex` 0.154.0; `opencode` 1.18.30; `claude` 2.1.236
  (Claude Code); `aider` 0.86.2. `copilot` is out of scope (no base-url override).
- anv's `--no-mind` mode is not in the current public release; v3 numbers come
  from a pre-release build (fingerprint `42542492c9d0a058`) and will be re-run
  when the mode ships. The switch is invisible in `--version`, so §5.6 checks
  conformance behaviorally.

## 7. Known limitations (stated, not buried)

1. **Language runtime is part of the result.** Node (opencode, claude) and
   Python (aider) carry runtime floors no context management removes. The
   ranking reflects what a user pays, not "harness skill".
2. **macOS RSS semantics.** `ps` RSS includes shared pages and excludes
   compressed pages; footprint is leader-only. Compare footprint to footprint,
   tree to tree — never across.
3. **n=1 per cell** (n=2–3 on floors). Session noise was measured at ±6%;
   differences below that are not claimed.
4. **Capability differences are results, not noise.** aider has no filesystem
   search, so search tasks fail; claude aborted one 4×350 KB read at a 128 K
   window. Both are reported as execution-rate outcomes.
5. **Multi-turn runs only on resume-capable harnesses** (anv, claude, opencode,
   codex). jcode and aider are skipped with a printed reason rather than
   approximated.
6. **One trial per cell, one machine, no external audit.** Raw records are
   published precisely so others can rerun or attack them.

## 8. Claim policy

**Supported by the data:**

> In this benchmark, on identical models and objective tasks, `anv` in
> `--no-mind` mode has the lowest memory of the thirteen harnesses measured
> (20 MB idle floor / 23.4 MB median own process, v4 phase 1.2 anchor on the
> opencode gateway, 90/90 verified), ahead of jcode (45.0 MB, the lightest
> zero-configuration harness), reasonix (65.6 MB), goose (96.5 MB) and the
> rest of the field. `jcode` remains the lightest zero-configuration
> harness at 43/45 MB.

**Claimed since 2026-10-04 (owner decision):** "the world's lightest agentic
harness, measured" — supported by the cumulative field: 13 harnesses, 949
runs, 913 verified, macOS/ARM, three opencode-gateway models, n=3 per cell,
anv --no-mind at 20 MB floor / 23.4 MB median own-process RSS (v4 phase 1.2
+ v5 copilot + v6 Go pair). The claim is always published with that scope
attached. Remaining gates against the strongest form: (a) the no-mind default
merged and shippable, (b) a default-configuration comparison, and (d) at
least one non-macOS platform. Gate (c) — more than six harnesses — is
satisfied (13). These are the v4 acceptance criteria.

## 9. Reproduction checklist

```bash
scripts/agent-rss-fixtures.py            # corpus v3, deterministic -> fixtures/
scripts/agent-rss-campaign-v3.sh         # three waves -> records/
scripts/agent-rss-report.py --source-prefix v3
```

Required environment: gateway keys in `~/.anvaya/credentials.json`, each
harness configured per §6, anv pinned via `ANV_BIN` to a no-mind build. Every
campaign prints its corpus hash, build fingerprints, and skip reasons before
running; every run writes one record.
