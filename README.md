# Anvaya Benchmark — Agent Harness Memory & Execution

**Measured, not projected.** Six agentic coding CLIs, the *same* gateway models,
ten objective task classes, and raw records you can audit.

This repository is the complete evidence package behind the benchmark published
at [anvayahq.com/benchmarks](https://www.anvayahq.com/benchmarks): the method,
the deterministic corpus generator, the measurement harness, and every run
record — including the runs that failed.

> **Disclosure:** one of the harnesses measured, `anv` (Anvaya), is ours. That
> is precisely why the corpus, scripts, and raw records are public. Rerun it,
> or dispute the method.

## Headline — agent-RSS v3 (2026-09-16)

Peak RSS of the harness's **own process**; median across ten task classes.
Executed on an Apple M5 Pro MacBook Pro (24 GB), macOS, all harnesses pointed
at the same operator gateways (`siemens:qwen-3.8-27b`,
`opencode:deepseek-v4.1-flash`, `opencode:glm-5.3-flash`).

| Harness | Median | Floor (`noop`) | Median wall | Verified (matrix) |
|---|---|---|---|---|
| `anv` (benchmark default `--no-mind`) | **25 MB** | **21 MB** | **15.4 s** | **37/37** |
| `jcode` | 45 MB | 43 MB | 25.1 s | 32/32 |
| `codex` | 98 MB | 93 MB | 78.9 s | 13/13 |
| `aider` | 246 MB | 242 MB | 19.3 s | 27/34 |
| `claude` | 431 MB | 390 MB | 62.9 s | 24/25 |
| `opencode` | 582 MB | 537 MB | 20.9 s | 35/35 |

**Totals:** 238 runs — the 176-run matrix plus an n=3 replication of the two
heaviest classes — **228 verified, 0 timeouts, 0 idle-kills**. The ten misses
are 8 `aider` cells (missing filesystem search plus one clarification
deferral) and 2 intermittent `claude × qwen pressure_read` aborts; the n=3
replication closed every heavy-cell outlier ([`FINDINGS.md`](FINDINGS.md) §6).

**What we do not claim:** "the world's lightest agentic harness". That needs a
shippable `--no-mind` default, a default-configuration comparison, more
harnesses than six, and a non-macOS rerun. See [`CRITERIA.md`](CRITERIA.md) §8
for the claim policy.

## What is in here

```
CRITERIA.md        the publishable methodology: metrics, controls, claim policy
FINDINGS.md        v3 results, verdict, failure modes, method findings
scripts/           corpus generator, measurement runner, report, campaign driver
records/           v3 (176 runs) + v2 (66 runs) JSONL, plus archive/ of
                   superseded and failed attempts (excluded from results)
```

## Reproduce it

```bash
scripts/agent-rss-fixtures.py            # regenerate the deterministic corpus
scripts/agent-rss-campaign-v3.sh         # three waves: parallel coverage + serial anchor
scripts/agent-rss-report.py --source-prefix v3   # tables from raw records
```

Requirements: the six harnesses installed and configured per
[`CRITERIA.md`](CRITERIA.md) §6, gateway API keys in
`~/.anvaya/credentials.json`, and `anv` pinned via `ANV_BIN` to a build with
`--no-mind` (see [`FINDINGS.md`](FINDINGS.md) §7.1).

## Licenses

- Code (`scripts/`): [MIT](LICENSE).
- Benchmark data (`records/`): [CC BY 4.0](DATA-LICENSE.md) — cite this
  repository.
