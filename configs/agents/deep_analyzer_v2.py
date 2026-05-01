deep_analyzer_v2_agent = dict(
    workdir = "workdir/deep_analyzer_v2",
    name = "deep_analyzer_v2_agent",
    type = "Agent",
    description = (
        "Multi-round analysis agent supporting pure-text tasks and multimodal "
        "text+image/pdf/audio/video tasks (task as text, files as local image paths or image URLs). "
        "Images, pdf and audio accept any URL or local path; "
        "video supports YouTube URLs only. Runs multiple LLMs in parallel per round "
        "and synthesizes findings across up to 3 rounds."
    ),
    model_name = "openrouter/gemini-3.1-pro-preview",
    memory_name = "general_memory_system",
    require_grad = False,
    max_rounds = 3,
    summary_model_name = "openrouter/grok-4.1-fast",
    general_analyze_models = [
        "openrouter/gemini-3.1-pro-preview-plugins", # can analyze audio, video, image, pdf, etc.
    ],
    llm_analyze_models = [
        "openrouter/gpt-5.4", # can analyze complex text with image
    ],
    advanced_analyze_models = [
        "openrouter/gpt-5.4-pro", # expensive but powerful model for deep analysis
    ],
)
