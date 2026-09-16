#!/bin/bash
# Full agent-RSS campaign v3 (2026-09-16).
#
# Scope change over v2: six harnesses (adds claude, aider) on three
# model/provider pairs (adds opencode:glm-5.3-flash), over a ten-task corpus
# (adds pressure_read, aggregate_scan, noisy_tool, build_pkg, long_session).
#
# `anv` means `--no-mind` here (ANV_NO_MIND=1): no store, no daemon, `mind`
# tool dropped. That is anvaya's benchmark default mode — the Mind is a young,
# unoptimised ONNX-retrieval subsystem, deferred to a Mind-optimisation round
# rather than ranked as harness overhead (v2 records and FINDINGS §3.1
# document its ~190 MB cost).
#
# Structure is three waves, because the measurements have different
# requirements:
#   A  single-turn coverage, parallel (jobs=6) with a free-memory gate.
#   B  long-horizon continuation, parallel (jobs=4). Only harnesses with a
#      clean resume form run; the others are skipped by the runner with reason.
#   C  serial RSS anchor (jobs=1): the noop floor and one real task per arm with
#      nothing else in flight. These records are what the RSS ranking quotes.
#
# Every invocation uses --resume, so an interruption costs only the in-flight
# runs, and the three 2026-09-16 qwen idle-kills are retried with --idle-timeout
# 900 (the earlier 300 s cap was shorter than qwen's first-token latency on
# multi-hundred-KB reads, which is a measurement artifact, not a result).
#
# v2 (scripts/agent-rss-campaign.sh) is kept as-is so its records remain
# reproducible.
#
# Detached via nohup so no harness/monitor timeout can kill an in-flight run.
set -u
cd /Users/dest/Documents/Projects/Anvaya-CLI

REC=records
mkdir -p "$REC"

JOBS=6
BJOBS=4
GATE=4          # GB free+inactive; runner holds a run below this
TIMEOUT=1800
IDLE=900
SINGLE=noop,wide_read,large_read,pressure_read,deep_read,aggregate_scan,noisy_tool,edit_tool,build_pkg
MODELS=siemens:qwen-3.8-27b,opencode:deepseek-v4.1-flash,opencode:glm-5.3-flash
OTHERS=jcode,codex,opencode,claude,aider

run() {
  echo
  echo "########## $* ##########"
  python3 scripts/measure-agent-rss.py "$@"
  echo "exit=$?"
}

echo "########## CAMPAIGN v3 START $(date -u +%Y-%m-%dT%H:%M:%SZ) ##########"

run --harnesses "$OTHERS" \
    --models "$MODELS" --tasks "$SINGLE" --trials 1 \
    --jobs "$JOBS" --min-free-gb "$GATE" --resume \
    --timeout "$TIMEOUT" --idle-timeout "$IDLE" \
    --out "$REC/v3-campaign-A-single-turn.jsonl"

run --harnesses anv --arm anv-no-mind \
    --models "$MODELS" --tasks "$SINGLE" --trials 1 \
    --jobs "$JOBS" --min-free-gb "$GATE" --resume \
    --timeout "$TIMEOUT" --idle-timeout "$IDLE" \
    --out "$REC/v3-campaign-A2-anv-no-mind.jsonl"

echo "########## WAVE B: long-horizon continuation ##########"

run --harnesses "$OTHERS" \
    --models "$MODELS" --tasks long_session --trials 1 \
    --jobs "$BJOBS" --min-free-gb "$GATE" --resume \
    --timeout "$TIMEOUT" --idle-timeout "$IDLE" \
    --out "$REC/v3-campaign-B-long-session.jsonl"

run --harnesses anv --arm anv-no-mind \
    --models "$MODELS" --tasks long_session --trials 1 \
    --jobs 3 --min-free-gb "$GATE" --resume \
    --timeout "$TIMEOUT" --idle-timeout "$IDLE" \
    --out "$REC/v3-campaign-B2-anv-no-mind-long-session.jsonl"

echo "########## WAVE C: serial RSS anchor (jobs=1) ##########"

run --harnesses "$OTHERS" \
    --models siemens:qwen-3.8-27b --tasks noop \
    --trials 2 --resume --idle-timeout "$IDLE" --timeout "$TIMEOUT" \
    --out "$REC/v3-anchor-serial-noop-qwen.jsonl"

run --harnesses anv --arm anv-no-mind \
    --models siemens:qwen-3.8-27b --tasks noop \
    --trials 2 --resume --idle-timeout "$IDLE" --timeout "$TIMEOUT" \
    --out "$REC/v3-anchor-serial-anv-no-mind-noop-qwen.jsonl"

run --harnesses "$OTHERS" \
    --models siemens:qwen-3.8-27b --tasks edit_tool \
    --trials 1 --resume --idle-timeout "$IDLE" --timeout "$TIMEOUT" \
    --out "$REC/v3-anchor-serial-edit-tool-qwen.jsonl"

run --harnesses anv --arm anv-no-mind \
    --models siemens:qwen-3.8-27b --tasks edit_tool \
    --trials 1 --resume --idle-timeout "$IDLE" --timeout "$TIMEOUT" \
    --out "$REC/v3-anchor-serial-anv-no-mind-edit-tool-qwen.jsonl"

run --harnesses jcode,opencode,aider \
    --models opencode:glm-5.3-flash --tasks noop \
    --trials 2 --resume --idle-timeout "$IDLE" --timeout "$TIMEOUT" \
    --out "$REC/v3-anchor-serial-noop-glm.jsonl"

run --harnesses anv --arm anv-no-mind \
    --models opencode:glm-5.3-flash --tasks noop \
    --trials 2 --resume --idle-timeout "$IDLE" --timeout "$TIMEOUT" \
    --out "$REC/v3-anchor-serial-anv-no-mind-noop-glm.jsonl"

run --harnesses claude,aider \
    --models opencode:deepseek-v4.1-flash --tasks noop \
    --trials 2 --resume --idle-timeout "$IDLE" --timeout "$TIMEOUT" \
    --out "$REC/v3-anchor-serial-noop-deepseek.jsonl"

echo
echo "########## CAMPAIGN v3 COMPLETE $(date -u +%Y-%m-%dT%H:%M:%SZ) ##########"
wc -l "$REC"/v3-*.jsonl
