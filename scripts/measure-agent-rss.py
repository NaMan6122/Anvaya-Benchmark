#!/usr/bin/env python3
"""Cross-harness RSS / active-memory measurement harness.

Drives each agentic CLI headless over the agent-RSS corpus and records what its
whole process *tree* costs in memory, per task, so harnesses can be compared on
memory rather than on marketing.

    scripts/measure-agent-rss.py --harnesses anv,jcode,codex,opencode \
        --models siemens:qwen-3.8-27b --tasks noop,large_read --trials 1

Why the whole tree: every one of these harnesses delegates real memory to helper
processes (anv to anvd/repomap/mind, jcode to its daemon, node harnesses to
child workers), so sampling only the top-level pid understates them, and by
different amounts. Sampling the tree is the only comparable unit.

Three metrics per run, because on macOS no single one alone is honest (see
docs/PERFORMANCE_BENCHMARKS_2026-08-14.md §2):

  peak_main_rss_mb   RSS of the harness's *own* process — the headline number,
                     and the one that answers "what does this CLI cost?". For
                     jcode/codex/opencode this is effectively the whole tree,
                     since they spawn no long-lived helpers.
  peak_aux_rss_mb    RSS of everything else in the tree at peak — tool children
                     and auxiliary services (anv's `mind-daemon`). Reported
                     separately rather than folded in, so a platform daemon's
                     cost is visible but is not attributed to the CLI.
  peak_tree_rss_mb   sum of the two: total resident cost of running this harness
                     on this task, which is what a user actually pays.
  peak_footprint_mb  `footprint -p` phys_footprint, the kernel's own accounting,
                     for the tree leader only.

  peak_main_breakdown names which process held what when the headline peaked, so
  a surprising main-process figure can be attributed without re-running.

Discovery rule: a process counts once it has been *seen as a descendant* of the
run leader. That keeps a reparented daemon in the total after its parent dies,
while excluding the mind-daemon/anvd instances that already belong to some other
project on this machine.

Output: one JSON object per run (JSONL), append-only. Also prints a summary
table. Run records land wherever --out points; fixtures come from
fixtures/.

Exit codes: 0 all runs completed, 1 one or more runs failed to execute.
"""

import argparse
import concurrent.futures
import datetime
import hashlib
import http.server
import json
import os
import re
import shutil
import signal
import statistics
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
import uuid

DEVNULL = subprocess.DEVNULL
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
FIXTURES = os.path.join(ROOT, "fixtures")
CREDENTIALS = os.path.expanduser("~/.anvaya/credentials.json")

# Harnesses whose own memory we are measuring. `bin` is resolved on PATH unless
# absolute, so a release build elsewhere can be pinned via the env var.
HARNESS_BINS = {
    "anv": os.environ.get("ANV_BIN", os.path.expanduser("~/.anvaya/bin/anv")),
    "codex": "codex",
    "opencode": "opencode",
    "jcode": "jcode",
    "claude": "claude",
    "aider": "aider",
    "copilot": "copilot",
    "pi": "pi",
    "goose": os.path.expanduser("~/.local/bin/goose"),
    "hermes": os.path.expanduser("~/.local/bin/hermes"),
    "kimi": "kimi",
}

# Local proxy in front of the vendor models. anv names it `CoCode` in its own
# config; every other harness is configured for it once, outside this script
# (see COCODE_SETUP in BENCHMARK_CONTENDERS.md).
COCODE_ORIGIN = "http://localhost:4141"
ANV_PROVIDER_NAME = {"cocode": "CoCode"}

# Which provider/model pairs each harness can actually reach, and why not.
# Verified live on 2026-09-15 for the v2 four; claude/aider verified 2026-09-16.
SUPPORT = {
    "anv": {"siemens", "opencode", "cocode"},
    "opencode": {"siemens", "opencode"},
    "jcode": {"siemens", "opencode"},
    # codex >= 0.154 dropped wire_api="chat" and needs the Responses API. Siemens
    # speaks it; opencode's gateway routes on x-opencode-session and returns 400.
    "codex": {"siemens"},
    # claude speaks the Anthropic Messages API, and both operators' gateways
    # expose a Messages endpoint (siemens /llm/v1/messages, opencode
    # /zen/go/v1/messages) — no translation proxy is involved.
    "claude": {"siemens", "opencode"},
    # aider speaks the OpenAI wire via litellm. opencode additionally routes on
    # x-opencode-session, which litellm cannot set; aider reaches it through the
    # local header-injecting pass-through this script starts (OpencodeProxy).
    "aider": {"siemens", "opencode", "cocode"},
    "copilot": set(),  # vendor models only; no base-url override
    # cocode (localhost:4141) serves OpenAI chat and Anthropic messages, no auth.
    "pi": {"cocode", "opencode"}, "goose": {"cocode", "opencode"},
    "hermes": {"cocode", "opencode"}, "kimi": {"cocode", "opencode"},
}

UNSUPPORTED_REASON = {
    ("codex", "opencode"): "opencode gateway requires x-opencode-session; codex sends none",
    ("copilot", "siemens"): "copilot CLI has no base-url override",
    ("copilot", "opencode"): "copilot CLI has no base-url override",
}

# Provider-level support is not always model-level: opencode's gateway serves
# the Anthropic wire for some models and 500s for others. Verified live
# 2026-09-16.
UNSUPPORTED_MODELS = {
    ("claude", "opencode", "glm-5.3-flash"):
        "opencode /v1/messages returns 500 for glm-5.3-flash (OpenAI wire only)",
}

# Harnesses with a clean, documented way to resume the same session in the same
# workspace. Multi-turn tasks (`tasks[].prompts`) run only on these; the others
# are skipped rather than approximated. jcode's resume needs a session id that
# its headless output does not surface, and aider has no headless resume at all.
CONTINUABLE = {"anv", "codex", "opencode", "claude"}

# Anthropic Messages gateways: base URL such that `<base>/v1/messages` resolves
# to the operator's endpoint. Claude Code appends that path itself.
CLAUDE_ANTHROPIC_BASE = {
    "siemens": "https://api.siemens.com/llm",
    "opencode": "https://opencode.ai/zen/go",
}

# OpenAI-compatible base URLs for aider (litellm `openai/` provider).
AIDER_OPENAI_BASE = {
    "cocode": COCODE_ORIGIN + "/v1",
    "siemens": "https://api.siemens.com/llm/v1",
    # "opencode" is filled in at run time by OpencodeProxy.start().
}

# opencode's gateway origin (both wires hang off it: /v1/chat/completions and
# /v1/messages).
OPENCODE_ORIGIN = "https://opencode.ai/zen/go"

# Context window per (provider, model), so harnesses that ask for one get the
# real number rather than an assumed default. Claude Code warns and assumes
# 200K when it does not recognise a model.
MODEL_CONTEXT = {
    ("siemens", "qwen-3.8-27b"): 131072,
    ("siemens", "deepseek-v4-flash"): 1000000,
    ("opencode", "deepseek-v4.1-flash"): 1000000,
    ("opencode", "mimo-v2.5"): 1000000,
    ("cocode", "claude-sonnet-5.5"): 936000,
    ("opencode", "mimo-v2.6-flash"): 1000000,
    ("opencode", "longcat-2.5-preview-free"): 262144,
}

# jcode reaches OpenAI-compatible endpoints through named profiles, created with
# `jcode provider add` (see FINDINGS.md §2 for the exact commands).
JCODE_PROFILE = {
    "siemens": "siemens-bench",
    "opencode": "opencode-bench",
}

# The same gateway carries different names per harness: anv's config calls it
# `opencode`, while the opencode CLI calls it `opencode-go`. Benchmark models are
# specified with the shared id, so map it to whatever the harness needs.
OPENCODE_CLI_PROVIDER = {"opencode": "opencode-go", "siemens": "siemens"}

# Per-harness environment hygiene so a run does not pay for instrumentation.
HARNESS_ENV = {
    "anv": {},
    "codex": {"OTEL_SDK_DISABLED": "true", "CODEX_DISABLE_TELEMETRY": "1"},
    "opencode": {"OPENCODE_DISABLE_TELEMETRY": "1"},
    "jcode": {"JCODE_TELEMETRY_DISABLED": "1"},
    # DISABLE_AUTOUPDATER so a campaign cannot silently change the binary it
    # already fingerprinted.
    "claude": {"DISABLE_TELEMETRY": "1", "DISABLE_ERROR_REPORTING": "1",
               "DISABLE_AUTOUPDATER": "1"},
    "aider": {"AIDER_ANALYTICS": "false"},
    "copilot": {},
    "pi": {"PI_TELEMETRY": "0", "PI_SKIP_VERSION_CHECK": "1", "CI": "1"},
    "goose": {"GOOSE_DISABLE_KEYRING": "1", "GOOSE_TELEMETRY_OFF": "1"},
    "hermes": {"HERMES_DISABLE_TELEMETRY": "1"},
    "kimi": {"KIMI_DISABLE_TELEMETRY": "1", "DISABLE_AUTOUPDATE": "1"},
}

# jcode's daemon is shared per user, not per workspace: two jcode runs
# concurrently would have the second attach to the first's daemon, which is not
# its descendant and so would not be counted — a silent understatement. jcode
# runs are therefore serialised even when --jobs > 1.
JCODE_LOCK = threading.Lock()


# ── Local pass-through for the opencode gateway (aider) ──────────────────────

class OpencodeProxy:
    """Local pass-through that adds opencode's required routing headers.

    opencode's gateway rejects OpenAI-wire requests that do not carry
    `x-opencode-session` and an accepted User-Agent. aider/litellm has no
    per-request header override, so the benchmark bridges it locally: aider
    points at this proxy and requests are forwarded verbatim upstream.

    The proxy is measurement scaffolding, not part of any harness, and it is
    started by this script so it never appears in a sampled process tree.
    """

    def __init__(self, api_key, upstream=OPENCODE_ORIGIN,
                 user_agent="agent-rss-bench/3"):
        self.api_key = api_key
        self.upstream = upstream.rstrip("/")
        self.user_agent = user_agent
        self.session = str(uuid.uuid4())
        self.httpd = None
        self.port = None

    def start(self):
        outer = self

        class Handler(http.server.BaseHTTPRequestHandler):
            # HTTP/1.0: the response is delimited by connection close, so the
            # upstream body can be copied without re-chunking it.
            protocol_version = "HTTP/1.0"

            def do_POST(self):
                length = int(self.headers.get("content-length") or 0)
                body = self.rfile.read(length)
                req = urllib.request.Request(
                    outer.upstream + self.path, data=body, method="POST")
                req.add_header("Authorization", f"Bearer {outer.api_key}")
                req.add_header("content-type", "application/json")
                req.add_header("x-opencode-session", outer.session)
                req.add_header("User-Agent", outer.user_agent)
                try:
                    with urllib.request.urlopen(req, timeout=900) as resp:
                        self.send_response(resp.status)
                        for k, v in resp.headers.items():
                            if k.lower() in ("transfer-encoding", "connection",
                                             "content-length"):
                                continue
                            self.send_header(k, v)
                        self.end_headers()
                        shutil.copyfileobj(resp, self.wfile)
                except urllib.error.HTTPError as exc:
                    payload = exc.read()
                    self.send_response(exc.code)
                    self.send_header("content-type", "application/json")
                    self.send_header("content-length", str(len(payload)))
                    self.end_headers()
                    self.wfile.write(payload)
                except Exception:
                    self.send_error(502)

            def log_message(self, *args):
                pass

        self.httpd = http.server.ThreadingHTTPServer(
            ("127.0.0.1", int(os.environ.get("OPENCODE_PROXY_PORT", "0"))), Handler)
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        return f"http://127.0.0.1:{self.port}/v1"

    def stop(self):
        if self.httpd is not None:
            try:
                self.httpd.shutdown()
            except Exception:
                pass


# ── Provider credentials ─────────────────────────────────────────────────────

def load_credentials():
    """API keys live in ~/.anvaya/credentials.json; never hardcode them here."""
    try:
        with open(CREDENTIALS) as fh:
            creds = json.load(fh)
    except Exception as exc:
        print(f"warning: cannot read {CREDENTIALS}: {exc}", file=sys.stderr)
        return {}
    mapping = {
        "SIEMENS_API_KEY": creds.get("siemens_api_key"),
        "OPENCODE_API_KEY": creds.get("opencode_api_key"),
    }
    return {k: v for k, v in mapping.items() if v}


# ── Command construction ─────────────────────────────────────────────────────

def build_command(harness, provider, model, prompt, workspace, cfg, turn_idx=0):
    """Return argv for one headless turn. cwd must be set to `workspace` too.

    `turn_idx > 0` means this is a continuation of the same session (only built
    for harnesses in CONTINUABLE), and each builder uses that harness's own
    resume form rather than a generic flag.
    """
    if harness == "anv":
        # --project pins the root explicitly; --yolo removes the approval stall.
        argv = [HARNESS_BINS["anv"], "--no-tui", "--yolo",
                "-p", ANV_PROVIDER_NAME.get(provider, provider), "-m", model, "--project", workspace]
        if turn_idx > 0:
            argv.append("--continue")
        return argv + [prompt]

    if harness == "codex":
        # workspace-write + bypass keep the sandbox from re-execing the tree and
        # from stalling on approvals. `exec resume` has no -s flag; the bypass
        # flag is what carries across.
        if turn_idx > 0:
            return [HARNESS_BINS["codex"], "exec", "resume", "--last",
                    "--skip-git-repo-check",
                    "--dangerously-bypass-approvals-and-sandbox",
                    "-c", f'model_provider="{provider}"',
                    "-m", model,
                    prompt]
        return [HARNESS_BINS["codex"], "exec",
                "--skip-git-repo-check",
                "-s", cfg["codex_sandbox"],
                "--dangerously-bypass-approvals-and-sandbox",
                "-c", f'model_provider="{provider}"',
                "-m", model,
                prompt]

    if harness == "opencode":
        # --dir is load-bearing: without it opencode resolves paths against an
        # ambient project root and writes outside the seeded workspace.
        cli_provider = OPENCODE_CLI_PROVIDER.get(provider, provider)
        argv = [HARNESS_BINS["opencode"], "run",
                "--dir", workspace,
                "-m", f"{cli_provider}/{model}"]
        if turn_idx > 0:
            argv.append("--continue")
        return argv + [prompt]

    if harness == "claude":
        # Both operators expose the Anthropic Messages wire, so Claude Code
        # points straight at the gateway (see CLAUDE_ANTHROPIC_BASE). The effort
        # level is pinned: siemens rejects Claude Code's default of "high" and
        # accepts xhigh (the gateway's own default), medium, and low.
        argv = [HARNESS_BINS["claude"]]
        if turn_idx > 0:
            argv.append("--continue")
        return argv + ["-p", prompt,
                       "--dangerously-skip-permissions",
                       "--output-format", "text",
                       "--model", model,
                       "--effort", cfg["claude_effort"]]

    if harness == "aider":
        if turn_idx > 0:
            raise SystemExit("aider has no headless session resume")
        base = cfg["aider_openai_base"].get(provider)
        if not base:
            raise SystemExit(f"no aider base URL for provider {provider!r}")
        # --no-stream keeps the local proxy path plain-JSON; --exit makes the
        # message the whole run instead of dropping into the REPL.
        return [HARNESS_BINS["aider"],
                "--model", f"openai/{model}",
                "--weak-model", f"openai/{model}",
                "--openai-api-base", base,
                "--no-pretty", "--no-stream", "--yes-always",
                "--no-auto-commits", "--no-check-update", "--no-analytics",
                "--no-show-model-warnings",
                "--message", prompt, "--exit"]

    if harness == "jcode":
        profile = JCODE_PROFILE.get(provider)
        if not profile:
            raise SystemExit(f"no jcode profile for provider {provider!r}")
        return [HARNESS_BINS["jcode"],
                "--provider-profile", profile,
                "--model", model,
                "--no-update",
                "run", prompt]

    if harness == "pi":
        return [HARNESS_BINS["pi"], "-p", "--provider", provider,
                "--model", model, prompt]

    if harness == "goose":
        return [HARNESS_BINS["goose"], "run", "-t", prompt]

    if harness == "hermes":
        # -z is one-shot; --yolo removes the approval stall. --ignore-rules keeps
        # a stray AGENTS.md in the fixture from changing the prompt.
        return [HARNESS_BINS["hermes"], "--yolo", "-m", model, "-z", prompt]

    if harness == "kimi":
        # -p is already non-interactive; it rejects --auto ("cannot combine").
        return [HARNESS_BINS["kimi"], "-m", model, "-p", prompt]

    raise SystemExit(f"no command builder for harness {harness!r}")


def build_fingerprint(harness):
    """Content-addressed build identity for a harness binary.

    `--version` is not enough: anv reports the literal string "0.1.0" for every
    release build, so two builds made hours apart are indistinguishable in a
    record. Since RSS changes between builds, a record that cannot name its
    build cannot be compared against one that can. This hashes the executable
    instead, so the same build is recognisable and a rebuilt one is visibly
    different.
    """
    # Most harnesses are named on PATH rather than pinned by absolute path, so
    # resolve before stat-ing or the fingerprint silently comes back empty.
    path = HARNESS_BINS.get(harness) or ""
    if path and not os.path.isabs(path):
        path = shutil.which(path) or ""
    if not path or not os.path.exists(path):
        return None
    try:
        h = hashlib.sha256()
        with open(path, "rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                h.update(chunk)
        st = os.stat(path)
        return {
            "sha256": h.hexdigest()[:16],
            "size": st.st_size,
            "mtime_utc": datetime.datetime.fromtimestamp(
                st.st_mtime, datetime.timezone.utc).isoformat(timespec="seconds"),
        }
    except Exception:
        return None


def harness_version(harness):
    """Record the exact build, since RSS drifts across releases."""
    argv = {
        "anv": [HARNESS_BINS["anv"], "--version"],
        "codex": [HARNESS_BINS["codex"], "--version"],
        "opencode": [HARNESS_BINS["opencode"], "--version"],
        "jcode": [HARNESS_BINS["jcode"], "version"],
        "claude": [HARNESS_BINS["claude"], "--version"],
        "aider": [HARNESS_BINS["aider"], "--version"],
        "copilot": [HARNESS_BINS["copilot"], "--version"],
    }.get(harness, [])
    if not argv:
        return "unknown"
    try:
        r = subprocess.run(argv, capture_output=True, text=True, timeout=60)
        return (r.stdout + r.stderr).strip().splitlines()[0][:80] or "unknown"
    except Exception:
        return "unknown"


# ── Process-tree sampling ────────────────────────────────────────────────────

def ps_snapshot():
    """{pid: (ppid, rss_mb, command)} for every process on the box."""
    try:
        out = subprocess.check_output(
            ["ps", "-axo", "pid=,ppid=,rss=,command="], stderr=DEVNULL
        ).decode("utf-8", "replace")
    except Exception:
        return {}
    rows = {}
    for line in out.splitlines():
        parts = line.strip().split(None, 3)
        if len(parts) < 4:
            continue
        try:
            pid, ppid, rss = int(parts[0]), int(parts[1]), int(parts[2])
        except ValueError:
            continue
        rows[pid] = (ppid, rss / 1024.0, parts[3])
    return rows


def label_for(command):
    """Short readable process label, so a tree total can be attributed.

    The prefix test is case-insensitive because the Homebrew Python framework
    binary is capitalised (`Python`), and a case-sensitive match silently failed
    to attach the script name — which is how aider ended up with a main-process
    reading of 0.0 MB in the first v3 smoke run.
    """
    toks = command.split()
    if not toks:
        return "?"
    base = os.path.basename(toks[0])
    if base.lower().startswith(("node", "python")) and len(toks) > 1:
        nxt = os.path.basename(toks[1])
        if not nxt.startswith("-"):
            return f"{base}:{nxt}"
    return base


# The harness's own process, by executable basename. Deliberately an exact match
# rather than a prefix: "anvd" must not be mistaken for "anv", and the swarm
# daemon is not the CLI.
HARNESS_MAIN_BASENAME = {
    "anv": {"anv"},
    "jcode": {"jcode"},
    "codex": {"codex"},
    "opencode": {"opencode"},
    "claude": {"claude"},
    "aider": {"aider"},
    "copilot": {"copilot"},
    "pi": {"pi", "node"},
    "goose": {"goose"},
    "hermes": {"hermes", "python", "python3", "python3.14"},
    "kimi": {"kimi", "kimi-code"},
}


def is_main_proc(harness, label):
    """Whether a process label is the harness itself rather than a helper.

    Aider is a Python entry point, so its process label is e.g.
    `python3.12:aider`; matching either side of the `label_for` colon keeps the
    harness's own interpreter in the headline without pulling unrelated Python
    helpers in.

    The configured binary's own basename is also accepted, because a pinned
    build need not be named like the harness: `ANV_BIN=.../anv-nomind-bench`
    produces a process label of `anv-nomind-bench`, and matching only the
    literal "anv" silently moved the CLI's RSS into the aux column.
    """
    names = set(HARNESS_MAIN_BASENAME.get(harness, set()))
    binpath = HARNESS_BINS.get(harness) or ""
    if binpath:
        names.add(os.path.basename(binpath))
    # macOS reports a not-yet-reaped child's command as "(name)"; normalise
    # before comparing or the harness's own process goes unrecognised.
    norm = label.strip().strip("()")
    parts = norm.split(":")
    return parts[0] in names or parts[-1] in names


def expand_tree(leader, rows, discovered):
    """Grow `discovered` with descendants of leader; return pids of interest.

    Once a pid has been observed as a descendant it stays in `discovered` even
    after reparenting, which is what keeps a detached daemon inside the total.
    Pids that were never descendants are never added, so other projects' anvd /
    mind-daemon instances cannot leak into the measurement.
    """
    children = {}
    for pid, (ppid, _rss, _cmd) in rows.items():
        children.setdefault(ppid, []).append(pid)

    frontier = [leader] + [p for p in discovered if p in rows]
    seen = set()
    while frontier:
        pid = frontier.pop()
        if pid in seen:
            continue
        seen.add(pid)
        discovered.add(pid)
        frontier.extend(children.get(pid, []))

    me = os.getpid()
    live = [p for p in discovered
            if p in rows and p != me and "measure-agent-rss" not in rows[p][2]]
    return live


def footprint_mb(pid):
    """kernel phys_footprint via `footprint -p`; None when unavailable."""
    try:
        r = subprocess.run(["/usr/bin/footprint", "-p", str(pid)],
                           capture_output=True, text=True, timeout=25)
    except Exception:
        return None, None
    cur = peak = None
    for line in r.stdout.splitlines():
        m = re.search(r"phys_footprint:\s*([\d.]+)\s*(KB|MB|GB)", line)
        if m:
            val = float(m.group(1)) * {"KB": 1 / 1024.0, "MB": 1.0, "GB": 1024.0}[m.group(2)]
            cur = val
        m = re.search(r"phys_footprint_peak:\s*([\d.]+)\s*(KB|MB|GB)", line)
        if m:
            val = float(m.group(1)) * {"KB": 1 / 1024.0, "MB": 1.0, "GB": 1024.0}[m.group(2)]
            peak = val
    return cur, peak


def pid_alive(pid):
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def available_gb():
    """Rough free + inactive memory, in GB.

    macOS reclaims and compresses pages under pressure, and compressed pages do
    not appear in any process's RSS. So running harnesses concurrently under
    memory pressure makes a harness read *artificially low*, and the numbers
    stop being comparable. This exists so parallelism can be gated on real
    headroom instead of assumed.
    """
    try:
        out = subprocess.check_output(["vm_stat"], stderr=DEVNULL).decode()
    except Exception:
        return None
    # Page size is reported in the header; fall back to 16 KiB (Apple Silicon).
    page = 16384
    m = re.search(r"page size of (\d+) bytes", out)
    if m:
        page = int(m.group(1))
    free = inactive = 0
    for line in out.splitlines():
        if line.startswith("Pages free"):
            free = int(re.sub(r"\D", "", line.split(":")[1]) or 0)
        elif line.startswith("Pages inactive"):
            inactive = int(re.sub(r"\D", "", line.split(":")[1]) or 0)
    return (free + inactive) * page / (1024 ** 3)


def wait_for_headroom(min_gb, jobs, quiet):
    """Block until there is enough free memory for `jobs` concurrent runs."""
    if min_gb <= 0:
        return
    while True:
        avail = available_gb()
        if avail is None or avail >= min_gb:
            return
        if not quiet:
            print(f"      [gate] {avail:.1f}GB free < {min_gb:.1f}GB required for "
                  f"jobs={jobs}; waiting 10s", flush=True)
        time.sleep(10)


def kill_tree(leader, discovered, grace=5.0):
    """SIGTERM then SIGKILL the whole set, including detached members."""
    targets = [p for p in set(list(discovered) + [leader]) if pid_alive(p)]
    for pid in targets:
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            pass
    try:
        os.killpg(os.getpgid(leader), signal.SIGTERM)
    except Exception:
        pass

    deadline = time.time() + grace
    while time.time() < deadline:
        if not any(pid_alive(p) for p in targets):
            return
        time.sleep(0.15)

    for pid in targets:
        if pid_alive(pid):
            try:
                os.kill(pid, signal.SIGKILL)
            except OSError:
                pass
    try:
        os.killpg(os.getpgid(leader), signal.SIGKILL)
    except Exception:
        pass


# ── One run ──────────────────────────────────────────────────────────────────

def seed_workspace(task, cfg):
    """Fresh copy of the seed outside the repo, git-initialised if asked."""
    d = tempfile.mkdtemp(prefix=f"agent-rss-{task['id']}-", dir=cfg["work_dir"])
    shutil.copytree(task["_seed_path"], d, dirs_exist_ok=True)
    if cfg["git_init"]:
        subprocess.run(["git", "init", "-q"], cwd=d, capture_output=True)
        subprocess.run(["git", "add", "-A"], cwd=d, capture_output=True)
        subprocess.run(["git", "-c", "user.email=b@b", "-c", "user.name=bench",
                        "commit", "-qm", "seed"], cwd=d, capture_output=True)
    return d


def workspace_size(path):
    files = total = 0
    for base, _dirs, names in os.walk(path):
        for n in names:
            files += 1
            try:
                total += os.path.getsize(os.path.join(base, n))
            except OSError:
                pass
    return files, total


def harness_env(harness, provider, model, workspace, creds, cfg):
    """Environment for one run, including per-harness isolation.

    Isolation is what makes parallel runs possible without cross-talk: claude
    gets its own CLAUDE_CONFIG_DIR (so `--continue` can only see its own
    session, and no user plugin or hook loads), codex gets its own CODEX_HOME
    (so `exec resume --last` cannot pick a concurrent run's session), and the
    operator config for codex is copied in so the gateway still resolves.
    """
    env = dict(os.environ)
    env.update(creds)
    env.update(HARNESS_ENV.get(harness, {}))
    env["NO_COLOR"] = "1"
    if harness != "anv":
        env.pop("ANV_PROJECT", None)

    # Arm anv-nomind: runs anv with its Mind switched off entirely.
    #
    # This used to set MIND_DAEMON_BIN=/usr/bin/false, which does NOT disable the
    # mind cleanly — the bridge still tried to reach a daemon every turn, failed,
    # and retried, costing ~40s per turn against ~3s with the daemon up. That
    # measured a failure path rather than a configuration, and made the arm
    # useless for anything except a memory floor.
    #
    # `--no-mind` (spec'd alongside ANV_NO_MIND and config `no_mind`) is the real
    # switch: no store opened, no daemon contacted or spawned, and the `mind` tool
    # is dropped from the schema. That is the configuration a user could actually
    # run, so it is the one worth measuring.
    # Two distinct mechanisms, two distinct arm names, because the same arm name
    # meaning two different things is how a record stops being reproducible:
    #   anv-no-mind  the real switch, via ANV_NO_MIND=1
    #   anv-nomind   LEGACY: MIND_DAEMON_BIN=/usr/bin/false, which forced a
    #                failed-spawn retry every turn (~40s vs ~3s). Kept only so
    #                the 2026-09-15 campaign-C records can be reproduced.
    if harness == "anv":
        if cfg["arm"] == "anv-no-mind":
            env["ANV_NO_MIND"] = "1"
        elif cfg["arm"] == "anv-nomind":
            env["MIND_DAEMON_BIN"] = "/usr/bin/false"

    elif harness == "claude":
        cfgdir = os.path.join(workspace, ".claude-home")
        os.makedirs(cfgdir, exist_ok=True)
        env.update({
            "CLAUDE_CONFIG_DIR": cfgdir,
            "ANTHROPIC_BASE_URL": CLAUDE_ANTHROPIC_BASE[provider],
            "ANTHROPIC_MODEL": model,
            "ANTHROPIC_DEFAULT_HAIKU_MODEL": model,
            "ANTHROPIC_DEFAULT_SONNET_MODEL": model,
            "ANTHROPIC_DEFAULT_OPUS_MODEL": model,
            "CLAUDE_CODE_MAX_CONTEXT_TOKENS": str(
                MODEL_CONTEXT.get((provider, model), 131072)),
        })
        if provider == "opencode":
            env["ANTHROPIC_API_KEY"] = creds.get("OPENCODE_API_KEY", "")
            env["ANTHROPIC_CUSTOM_HEADERS"] = f"x-opencode-session: {uuid.uuid4()}"
        else:
            env["ANTHROPIC_AUTH_TOKEN"] = creds.get("SIEMENS_API_KEY", "")

    elif harness == "codex":
        home = os.path.join(workspace, ".codex-home")
        os.makedirs(home, exist_ok=True)
        src = os.path.expanduser("~/.codex/config.toml")
        if os.path.exists(src):
            shutil.copy2(src, os.path.join(home, "config.toml"))
        env["CODEX_HOME"] = home

    elif harness == "aider":
        key_name = {"siemens": "SIEMENS_API_KEY", "opencode": "OPENCODE_API_KEY"}
        # cocode needs no auth, but litellm refuses an empty key.
        env["OPENAI_API_KEY"] = (creds.get(key_name.get(provider, ""), "")
                                 or "cocode-bench")

    elif harness in ("pi", "goose", "hermes", "kimi"):
        # Isolate each run's state so a session from one task cannot be resumed
        # or recalled by the next; the provider endpoint itself lives in each
        # tool's one-time config (cocode is the only provider these run on).
        home = os.path.join(workspace, f".{harness}-home")
        os.makedirs(home, exist_ok=True)
        if harness == "pi":
            env["PI_CODING_AGENT_DIR"] = home
            src = os.path.expanduser("~/.pi/agent/models.json")
            if os.path.exists(src):
                shutil.copy2(src, os.path.join(home, "models.json"))
        elif harness == "goose":
            if provider == "opencode":
                env.update({"OPENAI_HOST": cfg["aider_openai_base"]["opencode"],
                            "OPENAI_API_KEY": "via-proxy", "GOOSE_PROVIDER": "openai", "GOOSE_MODEL": model})
            else:
                # cocode's OpenAI endpoint drops tool definitions -> Anthropic wire.
                env.update({"ANTHROPIC_HOST": COCODE_ORIGIN, "ANTHROPIC_API_KEY": "cocode-bench",
                            "GOOSE_PROVIDER": "anthropic", "GOOSE_MODEL": model})
        elif harness == "hermes":
            env.update({"ANTHROPIC_API_KEY": "cocode-bench", "OPENAI_API_KEY": "via-proxy"})
        elif harness == "kimi":
            # Kimi's config is per-user, not under KIMI_CODE_HOME. Install a
            # temporary config in a private HOME for each run.
            config_home = os.path.join(workspace, f".{harness}-config-home")
            config_dir = os.path.join(config_home, ".kimi-code")
            os.makedirs(config_dir, exist_ok=True)
            config = os.path.join(config_dir, "config.toml")
            with open(config, "w") as fh:
                fh.write(
                    f'default_model = "{model}"\n\n'
                    '[providers.opencode]\n'
                    'type = "openai"\n'
                    f'base_url = "{cfg["aider_openai_base"]["opencode"]}"\n'
                    'api_key = "via-proxy"\n\n'
                    f'[models."{model}"]\n'
                    'provider = "opencode"\n'
                    f'model = "{model}"\n'
                    'max_context_size = 1000000\n'
                )
            env["HOME"] = config_home
            env["KIMI_CODE_HOME"] = config_dir

    if cfg["extra_env"]:
        env.update(cfg["extra_env"])
    return env


def run_turn(argv, env, workspace, harness, cfg):
    """Execute one headless turn, sampling the process tree the whole time.

    Returns a per-turn stats dict. One turn = one harness process; multi-turn
    tasks call this once per prompt, and run_one aggregates the results.
    """
    jcode_daemon_before = any(
        "jcode" in (r[2] or "") and "server" in (r[2] or "")
        for r in ps_snapshot().values()
    )

    t0 = time.time()
    proc = subprocess.Popen(
        argv, cwd=workspace, env=env, stdin=DEVNULL,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        start_new_session=True,
    )

    state = {"last_out": time.time(), "buf": bytearray(), "first_out": None}

    def reader():
        try:
            while True:
                chunk = proc.stdout.read(4096)
                if not chunk:
                    break
                if state["first_out"] is None:
                    state["first_out"] = time.time()
                state["buf"] += chunk
                state["last_out"] = time.time()
        except Exception:
            pass

    rt = threading.Thread(target=reader, daemon=True)
    rt.start()

    discovered = set()
    samples = []            # (t, main_rss, aux_rss, tree_rss, n_procs)
    peak_tree = 0.0
    peak_tree_break = []
    peak_main = 0.0
    peak_main_break = []
    peak_aux = 0.0
    peak_fp = 0.0
    tick = 0
    timed_out = False
    idle_killed = False

    while True:
        tick += 1
        now = time.time()
        elapsed = now - t0

        if elapsed > cfg["timeout"]:
            timed_out = True
            break
        if (now - state["last_out"]) > cfg["idle_timeout"] and elapsed > 20:
            idle_killed = True
            break

        rows = ps_snapshot()
        live = expand_tree(proc.pid, rows, discovered)
        labelled = [(label_for(rows[p][2]), rows[p][1]) for p in live]
        main_rss = sum(v for n, v in labelled if is_main_proc(harness, n))
        tree_rss = sum(v for _n, v in labelled)
        aux_rss = tree_rss - main_rss
        breakdown = sorted(labelled, key=lambda kv: -kv[1])

        if main_rss > peak_main:
            peak_main = main_rss
            # Snapshot the whole tree at this instant, so the aux contribution
            # is attributable to the same moment as the headline.
            peak_main_break = [{"proc": n, "rss_mb": round(v, 1)}
                               for n, v in breakdown[:8]]
        if tree_rss > peak_tree:
            peak_tree = tree_rss
            peak_tree_break = breakdown[:8]
        peak_aux = max(peak_aux, aux_rss)

        if tick % cfg["footprint_every"] == 0 and pid_alive(proc.pid):
            cur, fpk = footprint_mb(proc.pid)
            if cur is not None:
                peak_fp = max(peak_fp, cur)
            if fpk is not None:
                peak_fp = max(peak_fp, fpk)

        samples.append((round(elapsed, 2), round(main_rss, 1), round(aux_rss, 1),
                        round(tree_rss, 1), len(live)))

        if proc.poll() is not None:
            break
        time.sleep(cfg["sample_ms"] / 1000.0)

    # Drain anything still buffered, then make sure nothing survives the run.
    time.sleep(0.4)
    kill_tree(proc.pid, discovered, grace=cfg["kill_grace"])
    rt.join(timeout=3)
    wall = time.time() - t0
    rc = proc.poll()

    return {
        "wall": round(wall, 2),
        "rc": rc,
        "timed_out": timed_out,
        "idle_killed": idle_killed,
        "out": bytes(state["buf"]).decode("utf-8", "replace"),
        "peak_main": peak_main,
        "peak_aux": peak_aux,
        "peak_tree": peak_tree,
        "peak_fp": peak_fp,
        "samples": samples,
        "peak_main_break": peak_main_break,
        "peak_tree_break": peak_tree_break,
        "processes_seen": len(discovered),
        "jcode_daemon_preexisting": jcode_daemon_before if harness == "jcode" else None,
        "first_output_s": (round(state["first_out"] - t0, 2)
                           if state["first_out"] is not None else None),
    }


def run_one(harness, provider, model, task, trial, cfg, creds, versions):
    workspace = seed_workspace(task, cfg)
    prompts = task.get("prompts") or [task["prompt"]]
    env = harness_env(harness, provider, model, workspace, creds, cfg)

    rec = {
        "schema": "agent-rss-run/1",
        "ts_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
        "corpus_sha256": cfg["corpus_sha256"],
        "scaffold_levels": cfg["scaffold_levels"],
        "harness": harness,
        "harness_version": versions.get(harness, "unknown"),
        "build": cfg["builds"].get(harness),
        "provider": provider,
        "model": model,
        "task_id": task["id"],
        "task_class": task.get("class", task["id"]),
        "trial": trial,
        "arm": cfg["arm"],
        # What the arm actually did to the environment, so a reader never has to
        # infer the mechanism from the arm's name.
        "arm_env": {k: v for k, v in env.items()
                    if k in ("ANV_NO_MIND", "MIND_DAEMON_BIN")},
        # Parallel runs share the machine, so every record says how many runs
        # were in flight. RSS under contention is deflated on macOS, which is
        # why the campaign treats serial records as the RSS anchor.
        "parallel_jobs": cfg["jobs"],
        "workspace": workspace,
    }

    if cfg["dry_run"]:
        argv = build_command(harness, provider, model, prompts[0], workspace, cfg)
        print(f"  {harness:<9} {provider}:{model:<20} {task['id']:<15} "
              f"turns={len(prompts)} {argv[-1][:60]!r}")
        rec["argv"] = argv[:-1] + ["<prompt>"]
        rec["dry_run"] = True
        return rec

    turns = []
    out_parts = []
    turn_records = []
    total_wall = 0.0
    for idx, prompt in enumerate(prompts):
        argv = build_command(harness, provider, model, prompt, workspace, cfg, idx)
        if idx == 0:
            rec["argv"] = argv[:-1] + ["<prompt>"]
        res = run_turn(argv, env, workspace, harness, cfg)
        turns.append(res)
        out_parts.append(res["out"])
        total_wall += res["wall"]
        turn_records.append({
            "turn": idx,
            "rc": res["rc"],
            "wall_s": res["wall"],
            "peak_main_rss_mb": round(res["peak_main"], 1),
            "peak_tree_rss_mb": round(res["peak_tree"], 1),
            "timed_out": res["timed_out"],
            "idle_killed": res["idle_killed"],
            "first_output_s": res["first_output_s"],
        })
        # A failed turn makes the rest of a multi-turn run meaningless.
        if res["timed_out"] or res["idle_killed"] or res["rc"] != 0:
            break

    best_main_turn = max(turns, key=lambda t: t["peak_main"])
    best_tree_turn = max(turns, key=lambda t: t["peak_tree"])
    all_samples = [s for t in turns for s in t["samples"]]
    out = "".join(out_parts)
    clean = all(not t["timed_out"] and not t["idle_killed"] and t["rc"] == 0
                for t in turns)

    rec.update({
        "turns_requested": len(prompts),
        "turns_completed": len(turns),
        "turn_records": turn_records,
        "turn_walls_s": [t["wall"] for t in turns],
        "turn_first_output_s": [t["first_output_s"] for t in turns],
        "wall_s": round(total_wall, 2),
        # Totals across turns: the run's rc is the last turn's.
        "rc": turns[-1]["rc"],
        "timed_out": any(t["timed_out"] for t in turns),
        "idle_killed": any(t["idle_killed"] for t in turns),
        # Headline: the harness's own process. `tree` is the total the user pays;
        # `aux` is the part that is not the harness (services and tool children),
        # reported rather than folded in.
        "peak_main_rss_mb": round(max(t["peak_main"] for t in turns), 1),
        "peak_aux_rss_mb": round(max(t["peak_aux"] for t in turns), 1),
        "peak_tree_rss_mb": round(best_tree_turn["peak_tree"], 1),
        "peak_footprint_mb": round(max((t["peak_fp"] for t in turns), default=0.0), 1)
                             or None,
        # (t, main, tree) — main and tree together show whether a spike is the
        # harness or something it spawned. Taken from the peak-tree turn.
        "rss_series": [(s[0], s[1], s[3]) for s in best_tree_turn["samples"][::4]],
        "tree_rss_series": [(s[0], s[3]) for s in best_tree_turn["samples"][::4]],
        "median_main_rss_mb": round(statistics.median([s[1] for s in all_samples]), 1)
                              if all_samples else None,
        "n_samples": len(all_samples),
        "peak_main_breakdown": best_main_turn["peak_main_break"],
        "peak_breakdown": [{"proc": n, "rss_mb": round(v, 1)}
                           for n, v in best_tree_turn["peak_tree_break"]],
        "processes_seen": max(t["processes_seen"] for t in turns),
        "jcode_daemon_preexisting": (turns[0]["jcode_daemon_preexisting"]
                                     if harness == "jcode" else None),
        "first_output_s": min((t["first_output_s"] for t in turns
                               if t["first_output_s"] is not None), default=None),
        "stdout_tail": out[-600:],
    })

    # Token counts where the harness prints them on its own; aider reports
    # "Tokens: N sent, M received" per message. Optional by design — absence
    # means "not reported", never zero.
    token_hits = re.findall(r"Tokens:\s*(\d+)\s*sent,\s*(\d+)\s*received", out)
    if token_hits:
        rec["tokens_sent"] = sum(int(a) for a, _b in token_hits)
        rec["tokens_received"] = sum(int(b) for _a, b in token_hits)

    # Verification runs in the same workspace the harness was pointed at.
    vrc = None
    vout = ""
    try:
        vr = subprocess.run(task["verify_cmd"], cwd=workspace,
                            capture_output=True, text=True, timeout=300)
        vrc, vout = vr.returncode, (vr.stdout + vr.stderr)
    except Exception as exc:
        vrc, vout = -1, f"verify error: {exc}"

    need = task.get("expect_stdout_contains") or []
    # `require_stdout` is checked against the harness's own output, not the
    # verifier's. Without it a task whose verify_cmd is satisfied by absence of
    # work (e.g. noop's vacuous `true`) would score a crashed run as verified.
    #
    # Matched case-insensitively on purpose. This check exists to prove the
    # harness *responded* rather than crashed — not to grade instruction
    # compliance. A case-sensitive match conflates the two: jcode answering
    # "Ready when you are." is a legitimate completion that a strict match
    # scores as a failure, polluting the integrity table with a non-result.
    # Whether a model obeys "exactly the word READY" is a quality observation,
    # reported separately from memory.
    need_out = task.get("require_stdout") or []
    stdout_ok = all(s.lower() in out.lower() for s in need_out)

    rec["verify_rc"] = vrc
    rec["verified"] = bool(vrc == 0 and all(s in vout for s in need) and stdout_ok)
    rec["stdout_ok"] = stdout_ok
    rec["clean_exit"] = clean
    rec["verify_output_tail"] = vout.strip()[-300:]

    files, size = workspace_size(workspace)
    rec["workspace_files_after"] = files
    rec["workspace_bytes_after"] = size

    if not cfg["keep_workspace"]:
        shutil.rmtree(workspace, ignore_errors=True)
    return rec


# ── Corpus ───────────────────────────────────────────────────────────────────

def load_corpus():
    path = os.path.join(FIXTURES, "manifest.json")
    if not os.path.exists(path):
        raise SystemExit(
            f"corpus not found at {path}\n"
            "Generate it first: scripts/agent-rss-fixtures.py"
        )
    with open(path) as fh:
        manifest = json.load(fh)
    for t in manifest["tasks"]:
        t["_seed_path"] = os.path.join(FIXTURES, t["seed_dir"])
    return manifest


def corpus_hash():
    p = os.path.join(FIXTURES, ".corpus.sha256")
    if os.path.exists(p):
        return open(p).read().strip()
    return "unknown"


# ── Summary ──────────────────────────────────────────────────────────────────

def summarize(records, group_by):
    rendered = [r for r in records if not r.get("dry_run")]
    if not rendered:
        return
    groups = {}
    for r in rendered:
        key = tuple(r[k] for k in group_by)
        groups.setdefault(key, []).append(r)

    hdr = "  ".join(f"{k[:14]:<14}" for k in group_by)
    print(f"\n{hdr}  {'n':>2}  {'main':>9}  {'aux':>8}  {'tree':>9}  "
          f"{'peak_fp':>8}  {'wall':>7}  {'ok':>4}")
    print("-" * (len(hdr) + 62))
    for key in sorted(groups, key=lambda k: str(k)):
        rs = groups[key]
        mains = [r["peak_main_rss_mb"] for r in rs if r.get("peak_main_rss_mb") is not None]
        auxes = [r.get("peak_aux_rss_mb") or 0 for r in rs]
        trees = [r["peak_tree_rss_mb"] for r in rs]
        fps = [r["peak_footprint_mb"] for r in rs if r.get("peak_footprint_mb")]
        walls = [r["wall_s"] for r in rs]
        ok = sum(1 for r in rs if r.get("verified"))
        print(f"{'  '.join(f'{str(v)[:14]:<14}' for v in key)}  "
              f"{len(rs):>2}  {(max(mains) if mains else 0):>8.1f}M  "
              f"{max(auxes):>7.1f}M  {max(trees):>8.1f}M  "
              f"{(max(fps) if fps else 0):>7.1f}M  "
              f"{statistics.median(walls):>6.1f}s  {ok:>3}/{len(rs)}")


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--harnesses", default="anv,jcode,codex,opencode",
                   help="comma-separated harnesses")
    p.add_argument("--models", default="siemens:qwen-3.8-27b",
                   help="comma-separated provider:model pairs")
    p.add_argument("--tasks", default="", help="comma-separated task ids (default: all)")
    p.add_argument("--trials", type=int, default=1)
    p.add_argument("--out", default="", help="JSONL output path (default: stdout only)")
    p.add_argument("--timeout", type=float, default=900.0, help="per-run wall cap (s)")
    p.add_argument("--idle-timeout", type=float, default=240.0,
                   help="kill a run after this long with no output (s)")
    p.add_argument("--sample-ms", type=int, default=250)
    p.add_argument("--footprint-every", type=int, default=4,
                   help="sample `footprint` every Nth tick")
    p.add_argument("--kill-grace", type=float, default=5.0)
    p.add_argument("--work-dir", default=tempfile.gettempdir())
    p.add_argument("--keep-workspace", action="store_true")
    p.add_argument("--no-git-init", dest="git_init", action="store_false", default=True)
    p.add_argument("--codex-sandbox", default="workspace-write",
                   choices=["read-only", "workspace-write", "danger-full-access"])
    p.add_argument("--arm", default="default",
                   choices=["default", "anv-no-mind", "anv-nomind"],
                   help="anv-no-mind runs anv with --no-mind (ANV_NO_MIND=1): no "
                        "store, no daemon, `mind` tool dropped. anv-nomind is the "
                        "LEGACY MIND_DAEMON_BIN=/usr/bin/false hack, kept only to "
                        "reproduce the 2026-09-15 campaign-C records")
    p.add_argument("--jobs", type=int, default=1,
                   help="concurrent runs. Default 1: macOS reclaims pages under "
                        "pressure, which deflates RSS, so parallel runs measure "
                        "contention unless there is real headroom.")
    p.add_argument("--min-free-gb", type=float, default=6.0,
                   help="with --jobs>1, hold a run until this much memory is free")
    p.add_argument("--claude-effort", default="xhigh",
                   choices=["low", "medium", "high", "xhigh", "max"],
                   help="Claude Code reasoning effort. xhigh is the gateways' "
                        "own default; siemens rejects Claude Code's default of "
                        "'high' outright")
    p.add_argument("--resume", action="store_true",
                   help="skip units already completed in --out. Failed attempts "
                        "(timeout, idle-kill, non-zero rc) are re-run: that is the "
                        "point of resuming after a timeout fix")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--quiet", action="store_true")
    args = p.parse_args()

    manifest = load_corpus()
    tasks = manifest["tasks"]
    if args.tasks:
        want = {t.strip() for t in args.tasks.split(",") if t.strip()}
        unknown = want - {t["id"] for t in tasks}
        if unknown:
            raise SystemExit(f"unknown task id(s): {', '.join(sorted(unknown))}")
        tasks = [t for t in tasks if t["id"] in want]

    harnesses = [h.strip() for h in args.harnesses.split(",") if h.strip()]
    models = []
    for spec in args.models.split(","):
        spec = spec.strip()
        if not spec:
            continue
        if ":" not in spec:
            raise SystemExit(f"--models entry needs provider:model, got {spec!r}")
        prov, mdl = spec.split(":", 1)
        models.append((prov, mdl))

    cfg = {
        "timeout": args.timeout,
        "idle_timeout": args.idle_timeout,
        "sample_ms": args.sample_ms,
        "footprint_every": args.footprint_every,
        "kill_grace": args.kill_grace,
        "work_dir": args.work_dir,
        "keep_workspace": args.keep_workspace,
        "git_init": args.git_init,
        "codex_sandbox": args.codex_sandbox,
        "dry_run": args.dry_run,
        "arm": args.arm,
        "jobs": args.jobs,
        "claude_effort": args.claude_effort,
        "aider_openai_base": dict(AIDER_OPENAI_BASE),
        "extra_env": {},
        "corpus_sha256": corpus_hash(),
        "scaffold_levels": manifest.get("scaffold_levels"),
    }

    creds = load_credentials()
    versions = {h: harness_version(h) for h in harnesses}
    cfg["builds"] = {h: build_fingerprint(h) for h in harnesses}
    if not args.quiet:
        print(f"corpus sha256:{cfg['corpus_sha256']}  tasks={len(tasks)}")
        for h in harnesses:
            b = cfg["builds"].get(h) or {}
            print(f"  {h:<9} {versions[h]}"
                  + (f"  build={b.get('sha256')} ({b.get('mtime_utc')})" if b else ""))
        print()

    # Plan, skipping pairings that cannot work rather than failing mid-run.
    # A multi-turn task is skipped on harnesses without a clean resume form.
    plan = []
    for task in tasks:
        multi_turn = len(task.get("prompts") or [1]) > 1
        for harness in harnesses:
            for prov, mdl in models:
                if prov not in SUPPORT.get(harness, set()):
                    reason = UNSUPPORTED_REASON.get(
                        (harness, prov), f"{harness} cannot use provider {prov}")
                    plan.append(("skip", harness, prov, mdl, task, reason))
                elif (harness, prov, mdl) in UNSUPPORTED_MODELS:
                    plan.append(("skip", harness, prov, mdl, task,
                                 UNSUPPORTED_MODELS[(harness, prov, mdl)]))
                elif multi_turn and harness not in CONTINUABLE:
                    plan.append(("skip", harness, prov, mdl, task,
                                 f"{harness} has no clean session-resume form"))
                else:
                    plan.append(("run", harness, prov, mdl, task, None))

    # Everything from here on can talk to the opencode gateway, and aider
    # cannot set x-opencode-session itself; start the pass-through once.
    proxy = None
    if any(kind == "run" and h in ("aider", "pi", "goose", "hermes", "kimi") and prov == "opencode"
           for kind, h, prov, _m, _t, _r in plan):
        proxy = OpencodeProxy(creds.get("OPENCODE_API_KEY", ""))
        cfg["aider_openai_base"]["opencode"] = proxy.start()
        print(f"opencode proxy: {cfg['aider_openai_base']['opencode']} -> "
              f"{OPENCODE_ORIGIN} (x-opencode-session injected)")

    # Records are flushed one at a time rather than buffered to the end: a
    # campaign is long, and an interrupted run must not cost the evidence
    # already gathered. Append-only, so a record is never rewritten.
    sink = None
    if args.out and not args.dry_run:
        os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
        sink = open(args.out, "a")

    for kind, harness, prov, mdl, task, reason in plan:
        if kind == "skip":
            print(f"SKIP  {harness} x {prov}:{mdl} {task['id']} — {reason}")

    # Resume support: a campaign interrupted mid-wave re-runs only the cells
    # that have no completed record yet. Failed attempts (timeout, idle-kill,
    # non-zero rc) are deliberately NOT counted as done — they are the cells a
    # longer idle timeout or a cleaner run is meant to retry.
    done_keys = set()
    if args.resume and args.out and os.path.exists(args.out):
        for line in open(args.out, errors="replace"):
            try:
                d = json.loads(line)
            except Exception:
                continue
            if d.get("timed_out") or d.get("idle_killed") or d.get("rc"):
                continue
            done_keys.add((d.get("harness"), d.get("arm") or "default",
                           d.get("provider"), d.get("model"),
                           d.get("task_id"), d.get("trial")))

    planned_runs = [(h, prov, mdl, task, trial)
                    for kind, h, prov, mdl, task, _r in plan if kind == "run"
                    for trial in range(1, args.trials + 1)]
    units = [u for u in planned_runs
             if (u[0], cfg["arm"], u[1], u[2], u[3]["id"], u[4]) not in done_keys]
    if done_keys:
        print(f"resume: {len(planned_runs) - len(units)} unit(s) already completed "
              f"in {os.path.basename(args.out)}; re-running the rest")

    if args.jobs > 1:
        avail = available_gb()
        print(f"parallel: jobs={args.jobs}, free+inactive="
              f"{'unknown' if avail is None else f'{avail:.1f}GB'}, "
              f"gate={args.min_free_gb}GB")
        print("note: concurrent runs under memory pressure deflate RSS — "
              "treat any parallel arm as indicative, not as the headline.")

    records = []
    failures = 0
    write_lock = threading.Lock()

    def execute(unit):
        harness, prov, mdl, task, trial = unit
        if args.jobs > 1:
            wait_for_headroom(args.min_free_gb, args.jobs, args.quiet)
        if not args.quiet:
            print(f"run   {harness} {prov}:{mdl} {task['id']} "
                  f"(trial {trial}/{args.trials}) ...", flush=True)
        lock = JCODE_LOCK if harness == "jcode" else None
        if lock is not None:
            lock.acquire()
        try:
            rec = run_one(harness, prov, mdl, task, trial, cfg, creds, versions)
        except Exception as exc:
            print(f"      ERROR {harness} {task['id']}: "
                  f"{type(exc).__name__}: {exc}", file=sys.stderr)
            return None
        finally:
            if lock is not None:
                lock.release()
        with write_lock:
            records.append(rec)
            if sink is not None:
                sink.write(json.dumps(rec) + "\n")
                sink.flush()
                os.fsync(sink.fileno())
        if not args.quiet and not rec.get("dry_run"):
            print(f"      main={rec['peak_main_rss_mb']}MB  "
                  f"aux={rec['peak_aux_rss_mb']}MB  "
                  f"tree={rec['peak_tree_rss_mb']}MB  "
                  f"wall={rec['wall_s']}s  rc={rec['rc']}  "
                  f"verified={rec['verified']}"
                  + ("  TIMEOUT" if rec["timed_out"] else "")
                  + ("  IDLE-KILL" if rec["idle_killed"] else ""), flush=True)
        return rec

    try:
        if args.jobs > 1:
            with concurrent.futures.ThreadPoolExecutor(max_workers=args.jobs) as ex:
                for res in ex.map(execute, units):
                    if res is None:
                        failures += 1
        else:
            for unit in units:
                if execute(unit) is None:
                    failures += 1
    finally:
        if sink is not None:
            sink.close()
        if proxy is not None:
            proxy.stop()

    if sink is not None:
        print(f"\nwrote {len(records)} record(s) to {args.out}")

    summarize(records, ["harness", "model", "task_id"])
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
