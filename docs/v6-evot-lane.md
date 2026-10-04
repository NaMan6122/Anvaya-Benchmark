# v6 — evot lane

Third opencode-gateway contender lane after the Go pair (2026-10-05).
Single harness, 3 models × 9 task classes × n=3 = 81 runs, serial,
anchor-grade (load ~1.5 at start).

## 0. What this campaign is

`evot` v2026.9.29 is a **native arm64 binary** (no node shim — the
`~/.evotai/bin/evot` executable is the measured process, 65.5 MB on disk).
It speaks the OpenAI wire, so like reasonix it reaches the opencode gateway
through the runner's local header-injecting pass-through (the gateway
requires `x-opencode-session`, which a plain client cannot set). The per-run
`evot.env` is written into the workspace and passed via `--env-file`;
`EVOT_AUTO_DOWNLOAD=0` prevents the binary from self-updating mid-campaign.

## 1. Headline — own-process RSS

| metric | value |
|---|---|
| median own RSS (81 runs) | **56.7 MB** |
| range (min–max of run medians) | 49.6–80.3 MB |
| idle floor (noop, n=27) | 51.8 MB |
| wall median | 29.5 s |
| tree median | 59.6 MB |
| kernel footprint median | 19.0 MB |
| aux median | 0.0 MB |

## 2. Position in the cumulative field

```
anv (--no-mind)   23.4 MB   #1
jcode             45.0 MB   #2 (lightest zero-config)
evot              56.7 MB   #3 (NEW)
reasonix          65.6 MB
goose             96.5 MB
codex             97.2 MB
crush            107.8 MB
...
```

evot slots between jcode and reasonix: a flat ~52 MB floor that climbs only
~8 MB under the heaviest edits — the same flat-profile shape as the Go pair,
and the third consecutive light native-harness data point (Go ×2, this one
native arm64) against the Node field's 200+ MB floors.

Per model (median of 27 runs each):

| model | median own RSS (range) |
|---|---|
| opencode:mimo-v2.6-flash | 56.2 (50-66) MB |
| opencode:deepseek-v4.1-flash | 57.3 (51-67) MB |
| opencode:longcat-2.5-preview-free | 56.3 (50-80) MB |

Model choice moves evot's memory by <1 MB — the footprint is
runtime-inherent, not model-driven, exactly as in the Go pair.

## 3. Method findings (wiring)

1. `--model` refs are `provider:model` with a **colon**; the slash form
   fails immediately with `conf error: no provider with model 'openai/...'`.
2. One-shot `-p` **auto-approves tools** with no approval stall — verified
   live with a file-edit task on a non-TTY pipe (no `--yolo` flag exists).
   This made long_session the only skip.
3. The base URL must be the **bound** proxy port from the run config, never
   a URL rebuilt from `$OPENCODE_PROXY_PORT` (port=0 binds an ephemeral
   port) — the kimi silent-hang lesson, applied up front.

## 4. Failures and integrity

None. 81/81 verified, 0 timeouts, 0 idle-kills, no `--resume` retries
needed. Corpus SHA-256 `205507698927dd67` matches the v4/v5/v6-gopair lanes;
harness build `6185ca17a0cc68af` (size 65526064, mtime 2026-10-04T18:17:04Z)
is recorded in every row.

## 5. Limitations (stated, not buried)

macOS/ARM only; three opencode-gateway models; serial (n=3 per cell,
±6% session noise still applies per v2); `long_session` skipped (no headless
resume); claim scope unchanged — this strengthens the cumulative field, it
does not widen the "lightest" claim beyond what the stated scope already
carries.
