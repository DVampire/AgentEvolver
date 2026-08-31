"""Base AgentEvolver runtime configuration."""

#---------------GENERAL CONFIG-------------
tag = "base"
# Pre-binding default only: bind_session_roots() repoints this at the
# session sandbox as soon as real work starts. `tag` stays as a label, not a
# directory level, so it cannot collide with an owner name. Startup logs land in
# the owner tree beside that owner's sessions, not in the machine-level
# `.runtime` — nothing about a run's own pre-session window belongs to the host.
log_path = "base.log"
model_name = "google/gemini-3.1-pro-preview"

# Named model roles — behavior×model orthogonality (borrowed from HarnessX ModelConfig).
# Callers resolve a model by ROLE (`model_manager(role="judge", ...)`) instead of a
# hardcoded name; a missing role falls back to `main`. Point cheap roles at cheaper
# models to run the main loop on a big model while judging/summarizing on a small one.
# `name=` still works unchanged, so this is purely additive.
model_roles = dict(
    main=model_name,
    judge=model_name,
    summarize=model_name,
    smoke=model_name,   # cheap model for the extension replay smoke gate
)

#---------------TRACE INTEGRITY-------------
# interactive: checkpoint failures emit `integrity_degraded` and the run continues.
# training:    model requests, mutating tools, and completed steps require durable Trace.
# high_risk:   same fail-closed durability contract, named separately so deployments can
#              attach stricter approval/retention policy without changing run configs.
trace_integrity_profile = "interactive"

# A Tool policy ASK is a one-shot human decision, never an unbounded suspension.
# The Gateway keeps it listable across client reconnects and rejects it after this bound.
approval_timeout_seconds = 300.0

# Optional OpenTelemetry export. Trace JSONL/SQLite remains authoritative; OTLP is a
# best-effort operational mirror enabled either here or by an OTEL exporter endpoint env.
otel_enabled = False
otel_service_name = "agentevolver"
otel_endpoint = ""

#---------------SANDBOX EGRESS CONFIG---------------
# What every sandbox this run acquires may and may not reach. `sandbox_deny_hosts` always
# wins over `sandbox_allow_hosts`, and a single `sandbox_manager.acquire(...)` can override
# either when one sandbox genuinely differs.
#
# How they combine with a sandbox's own `network` flag:
#   network=True  + deny  -> open, minus the denied hosts
#   network=False + allow -> the sandbox gets NO network interface, and the allowed hosts
#                            are reachable only through a relay running outside it. An
#                            unlisted host is not filtered, it is unreachable.
#   network=False, no allow -> no egress at all
#
# Empty here on purpose: the framework's default posture is whatever a sandbox asks for,
# and a task that needs isolation says so in its own config (see
# configs/programbench_agent*.py, where the agent's shell must not be able to fetch the
# source it is meant to reconstruct).
sandbox_allow_hosts = []
sandbox_deny_hosts = []

# When true, the model endpoints in use (derived from the `*_API_BASE` variables in the
# environment) are added to the allowlist. Needed whenever the agent *brain* runs inside
# the sandbox, since it has to reach a model to do anything at all.
#
# Off by default, and deliberately so: turning it on converts `network=False` from "this
# sandbox has no network interface" into "this sandbox has a relay with an allowlist".
# That is the right trade for a sandbox hosting the brain, and a silent downgrade for
# anyone who wrote `network=False` meaning airgapped. A config that wants it says so.
sandbox_allow_model_endpoints = False

#---------------MEMORY CONFIG---------------
memory_config = dict(
    type = "general_memory_system",
    model_name = "google/gemini-3.1-pro-preview",
    max_summaries = 20,
    max_insights = 100
)

#---------------MAX TOKENS CONFIG---------------
# Large enough to hold opus-4.8's thinking tokens plus a full structured-output
# JSON body on the same completion budget (16384 truncated the JSON mid-string).
max_tokens = 32768

#---------------Window Size Config---------------
window_size = (1024, 768)
