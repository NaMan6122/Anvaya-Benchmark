# Records

One JSON object per run, one run per line (`schema: "agent-rss-run/1"`), written
by `scripts/measure-agent-rss.py`. These are the raw evidence behind
[`../FINDINGS.md`](../FINDINGS.md); every published number is derived from them
by `scripts/agent-rss-report.py`.

## Top-level files

| Files | What | Runs |
|---|---|---|
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
