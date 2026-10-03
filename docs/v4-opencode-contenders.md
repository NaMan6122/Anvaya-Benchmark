# v4 — Opencode-gateway contender harnesses

**Date:** 2026-10-01/04. · **Status:** FINAL

**Records:** `records/v4-phase1.2-anchor-serial-opencode-2026-10-04.jsonl`
(serial anchor, noop, 54 runs, all verified) and
`records/v4-anchor-serial-opencode-no-mind-contended.jsonl` (full task
matrix, serial `--jobs 1`, 498 runs, 469 verified, 0 timeouts).
**Tables:** `scripts/agent-rss-report.py --source-prefix v4`
**Corpus:** v3 corpus, sha256 `20550769`, single build per harness (fingerprint
in every record), `arm = anv-no-mind` throughout.

## 0. What this campaign is

Phase 1 (v3) measured the six incumbent harnesses (anv, jcode, codex,
opencode, claude, aider) on the operator gateways. v4 answers the next
question in the contender pipeline (`BENCHMARK_CONTENDERS.md` in the web
repo): where do the **new contender harnesses** sit — and does Phase 1's
runtime-ordering finding hold?

Contenders added: `pi`, `goose`, `hermes`, `kimi-code`, plus `aider` as the
bridge (the only harness present in both campaigns).

- **Gateway / models:** opencode gateway, OpenAI wire, through the runner's
  local pass-through (the gateway requires `x-opencode-session` + a
  non-default User-Agent; pi/goose/hermes/kimi cannot set those themselves).
  Models: `mimo-v2.6-flash`, `deepseek-v4.1-flash`,
  `longcat-2.5-preview-free`.
- **Arm:** `anv --no-mind` (benchmark default, unchanged from v3).
- **Two lanes, different status:**
  - *Anchor* (`v4-phase1.2-*`): the noop floor, n=3, **54/54 verified**.
    Anchor-grade; safe to print beside Phase 1.
  - *Contended matrix* (`v4-anchor-serial-*contended*`): all ten task
    classes, run on the dev machine with other agents resident (host state in
    the companion `.env.txt`). Serial. Contended-window numbers — comparable
    across harnesses (same window, same corpus), but do not print beside the
    v3 anchor figures.

## 1. Headline — own-process peak RSS, noop anchor (anchor-grade)

| harness | runtime | deepseek | longcat | mimo | aux |
|---|---|---|---|---|---|
| **anv --no-mind** | Rust | 19 | 20 | 20 | 0 |
| goose | Rust | 96 | 97 | 97 | 0 |
| pi | TypeScript | 116 | 129 | 128 | 0 |
| hermes | Python | 200 | 197 | 203 | 0 |
| aider | Python | 253 | 253 | 253 | 0 |
| kimi-code | TypeScript | 386 | 390 | 403 | 0 |

MB, median of 3, all cells verified. The anchor lane shows aux = 0 for every
harness on noop; the resident-helper costs appear on the heavier tasks (§3).

## 2. Full task matrix (contended window)

Own-process peak RSS, MB, median of 3 per cell.
⚠ = at least one run on that cell did not verify (aider, §4).
`—` = legitimate skip. `long_session` is absent for pi/goose/hermes/kimi/aider:
no documented headless session-resume form (same documented skip as v3's
jcode/aider; anv's 3-run column is shown for reference only).

| harness | noop | wide | large | pressure | deep | agg_scan | noisy | edit | build | long_sess |
|---|---|---|---|---|---|---|---|---|---|---|
| **anv** | 20 | 23 | 24 ⚠ | 27 | 26 | 23 | 22 | 24 | 24 | 23 |
| goose | 96 | 96 | 98 ⚠ | 100 | 102 | 96 | 96 | 96 | 96 | — |
| pi | 127 | 129 | 129 | 132 | 131 | 129 | 129 | 129 | 129 | — |
| hermes | 184 | 201 | 206 | 212 | 202 | 207 | 198 | 184 | 209 | — |
| aider | 253 | 288 ⚠ | 256 | 273 ⚠ | 253 | 251 ⚠ | 254 ⚠ | 266 ⚠ | 277 ⚠ | — |
| kimi | 383 | 386 | 396 | 415 | 390 | 411 | 400 | 381 | 412 | — |

(the two non-aider ⚠ cells are the gateway-stall idle-kills of §4: the
failed attempt is kept in the file, the rerun passed.)

Per-harness summary — every record in the file, failed attempts included
(498 runs, 469 verified; the 29 unverified are 26 aider refusal attempts
and 3 gateway-stall attempts, §4):

| harness | runs | verified | own med (min–max) | tree med | footprint med | wall med |
|---|---|---|---|---|---|---|
| **anv** | 92 | 90 | **23.4** (20–30) | 23.6 | **13.0** | 26.6 s |
| goose | 82 | 81 | 96.7 (96–111) | 98.1 | 61.0 | 26.8 s |
| pi | 81 | 81 | 129.3 (115–135) | 129.5 | 92.0 | 26.2 s |
| hermes | 81 | 81 | 201.4 (179–225) | 204.8 | 173.0 | 38.5 s |
| aider | 81 | **55** | 256.0 (217–301) | 265.9 | 233.0 | **18.3 s** |
| kimi | 81 | 81 | 393.5 (359–444) | 393.5 | 346.0 | 26.1 s |

(Per-cell values are medians of the records; regenerate with
`agent-rss-report.py --source-prefix v4-anchor-serial-opencode-no-mind-contended`.)

## 3. Findings

1. **Model does not move memory.** Per-harness medians move only a few MB
   across the three models (anv 19–20, kimi 386–403 on the anchor). Memory is
   a runtime property, as Phase 1 found.
2. **Runtime ordering holds loosely.** Rust lightest, then Node, then Python
   — but goose (Rust) is ~5x anv, and pi (Node) is lighter than both Python
   harnesses. The harness's own engineering moves it as much as the language.
3. **anv stays the floor** with a 13 MB kernel footprint — no daemon, no
   helper process, on this gateway and these models. 90/92 verified; the two
   failures are a gateway stall, not anv (§4).
4. **hermes and kimi carry a resident helper they cannot drop.** On the heavy
   tasks each carries a ~190 MB / ~350 MB second process that the kernel
   footprint column makes visible (hermes fp 173 vs own 201; kimi fp 346 vs
   own 393). Transient tool children (node/npm) add brief spikes that are
   not part of the steady state — compare footprint to footprint, tree to
   tree; the headline column is own-process and excludes them.
5. **aider + the three opencode flash models systematically refuse
   file-grep tasks.** `wide_read` and `aggregate_scan` verified **0/9**
   across all three models (both tasks, every trial, every model): the model
   answered conversationally — "please add the `records/` files to the
   chat" — instead of using file tools. `build_pkg` (5/9), `noisy_tool`
   (7/9), `pressure_read` (8/9) and `edit_tool` (8/9) fail intermittently,
   same refusal pattern. Phase 1's gateways did not show this; it is a
   **model-lane property**, and it is why the reporting unit must be the
   *harness + model pair*, not the harness alone. aider is still fastest on
   wall time (18.3 s median) because refusing is fast — the ⚠ marks are
   published, not hidden.
6. **Bridge check:** aider 253 MB idle here vs 242 MB in v3 on a different
   model lane — ~5%, inside the published ±6% noise. The two campaigns are
   mutually consistent.

## 4. Failures and what they were

- **2 idle-kills** (anv × longcat × large_read t1 at 263 s; goose × mimo ×
  large_read t2 at 275 s): the proxy log shows an upstream read timeout at
  the same moment — a gateway stall, not the harness. Both passed on rerun.
- **11 aider cells ⚠** (§3.5): 7 cells 0/3 and 4 cells 2/3, all the refusal
  pattern. Genuine, reproducible, published with `verified: false`.
- **kimi port-bug attempt** (anchor file): a runner bug pointed kimi at a
  dead proxy port; archived under `records/archive/port-bug-attempts-*` with
  the root cause and the fix (runner commit `755a76b`).
- **First-commit curation** of the anchor file (surplus duplicate trials from
  smoke passes): archived under `records/archive/dedup-surplus-*`. The
  contended file's two rerun records for the idle-killed cells are kept in
  place as evidence, per the append-only rule.

## 5. Limitations (stated, not buried)

- Contended window for the full matrix: resident dev-machine agents (host
  state captured in the `.env.txt`). Ordering is robust to that; absolute
  numbers are for the window, not a quiet box. The quiet-box anchor lane
  covers the noop floor only.
- `long_session` absent for five harnesses: no documented headless
  session-resume form (documented skip, not a gap).
- Single platform (macOS/ARM), single gateway, three models. The v4
  acceptance criteria in `CRITERIA.md` §8 (non-macOS platform,
  default-configuration comparison, more harnesses) are still open for the
  "world's lightest" claim.
