# Records

One JSON object per run, one run per line (`schema: "agent-rss-run/1"`), written
by `scripts/measure-agent-rss.py`. These are the raw evidence behind
[`../FINDINGS.md`](../FINDINGS.md); every published number is derived from them
by `scripts/agent-rss-report.py`.

## Top-level files

| Files | What | Runs |
|---|---|---|
| `v5-copilot-bundle-anchor-serial-2026-10-04.jsonl` | agent-RSS v5 — Copilot CLI BYOK lane (2026-10-04), two models (siemens qwen-3.8-27b, opencode longcat-2.5-preview-free) × 9 tasks × n=3 | 54 |
| `v6-evot-opencode-2026-10-05.jsonl` | agent-RSS v6 — evot lane (2026-10-05), evot v2026.9.29 (native arm64), three opencode models × 9 tasks × n=3 | 81 |
| `v6-gopair-anchor-serial-opencode-2026-10-04.jsonl` | agent-RSS v6 — Go-pair lane (2026-10-04), crush 0.97.1 + reasonix v1.39.7 (native Go binary), three opencode models × 9 tasks × n=3 | 162 |
| `v4-phase1.2-anchor-serial-2026-10-03.jsonl` | agent-RSS v4 phase 1.2 anchor (2026-10-03), six opencode harnesses, long_session + deep-research | 54 |
| `v4-anchor-serial-2026-10-03.jsonl` | agent-RSS v4 contended main (2026-09-30 → 10-03), six harnesses, 23 tasks, n=3 | 498 |
| `v3-*.jsonl` | agent-RSS v3 campaign (2026-09-16) | 176 |
| `v2-*.jsonl` | agent-RSS v2 campaign (2026-09-15/16), including the 40 records carried forward from v1 for byte-identical harness binaries | 66 |
| `corpus-v3.sha256` | corpus identity for v3 (`covers fixtures + manifest`) | — |

`archive/` holds superseded material that is deliberately **excluded** from all
results: failed attempts that were re-run, and partial campaigns from before
mid-campaign corrections. See [`archive/README.md`](archive/README.md).

## Rules this dataset follows

1. **Append-only.** A record is never edited or deleted. When a cell had to be
   re-run, the new record is appended and the old attempt is moved to
   `archive/`, not overwritten.
2. **Identity is content-addressed.** Each record carries `corpus_sha256` and
   the harness `build` fingerprint (sha256 of the binary, size, mtime) —
   `--version` strings are not accepted as identity because several harnesses
   report the same version across different builds.
3. **Failures are published.** Unverified runs stay in the dataset with
   `verified: false`, alongside the verifier output.

## Key fields

| Field | Meaning |
|---|---|
| `harness`, `provider`, `model`, `task_id`, `trial` | the cell |
| `arm` / `arm_env` | anv mode (v3: `anv-no-mind` is the benchmark default) |
| `build.sha256` | harness binary content hash |
| `corpus_sha256` | corpus that produced the seed workspace |
| `peak_main_rss_mb` / `peak_aux_rss_mb` / `peak_tree_rss_mb` | memory metrics (`CRITERIA.md` §3) |
| `peak_footprint_mb` | kernel `phys_footprint`, leader only |
| `wall_s`, `first_output_s`, `turn_walls_s` | timing |
| `verified`, `verify_rc`, `verify_output_tail`, `stdout_ok` | objective completion |
| `parallel_jobs` | concurrency at run time (1 = serial anchor) |
| `timed_out`, `idle_killed`, `clean_exit` | failure accounting |
