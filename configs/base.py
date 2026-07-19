#---------------GENERAL CONFIG-------------
tag = "base"
project_root = f"output/{tag}"
log_path = "base.log"
model_name = "openrouter/claude-opus-4.8"

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

#---------------MEMORY CONFIG---------------
memory_config = dict(
    type = "general_memory_system",
    model_name = "openrouter/gemini-3.5-flash",
    max_summaries = 20,
    max_insights = 100
)

#---------------MAX TOKENS CONFIG---------------
# Large enough to hold opus-4.8's thinking tokens plus a full structured-output
# JSON body on the same completion budget (16384 truncated the JSON mid-string).
max_tokens = 32768

#---------------Window Size Config---------------
window_size = (1024, 768)
