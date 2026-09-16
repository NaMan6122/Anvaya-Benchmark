# Archive — excluded from all results

Material kept for provenance, never averaged into published numbers.

## `superseded-2026-09-16/`

| File | Why it is excluded |
|---|---|
| `partial-wave-A-before-no-mind-correction.jsonl` | First Wave-A pass, before `anv` was switched to the `--no-mind` benchmark default; contains Mind-on anv records. Replaced by the corrected wave. |
| `A2-anv-mind-on-because-anv-no-mind-was-not-in-installed-build.jsonl` | anv pass that silently measured the Mind because the installed binary ignores `ANV_NO_MIND`. Replaced after a conformance-checked pre-release build. |
| `idle-killed-attempts-retried-with-900s.jsonl` | Three qwen large-read runs killed by the 300 s idle cap (shorter than the model's first-token latency on those reads). Re-run at 900 s; all three then passed. |

## `superseded-2026-09-15/`

v1–v2 transition material: stale anv v1 records (old binary), an interrupted
partial v2 campaign, and the legacy `MIND_DAEMON_BIN=/usr/bin/false` arm, which
forced a failed daemon spawn every turn and measured a failure path rather than
a configuration. Full context in `FINDINGS.md` §10 and `CRITERIA.md` §5.
