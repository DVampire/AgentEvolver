"""Prompt templates for DeepAnalyzerV2Agent.

Three prompts cover the full analysis pipeline:

  | Prompt                           | 用途                                         |
  |----------------------------------|----------------------------------------------|
  | deep_analyzer_v2_plan            | 每轮前生成分析角度；冲突时生成解决要点        |
  | deep_analyzer_v2_analyze         | 通用分析：有文件时感知媒体，无文件时直接推理  |
  | deep_analyzer_v2_eval            | 综合多模型结果、检测冲突、评估完整性          |
"""

from src.registry import PROMPT
from src.prompt.types import Prompt
from typing import Any, Dict
from pydantic import Field, ConfigDict


# ===========================================================================
# Plan — generate per-round analysis guidance
#
# Variables:
#   task      — the analysis task
#   file_info — newline list of "- path (type)" for each file; empty if no files
#   previous  — execution log of prior rounds (empty on round 1)
#
# Round 1 (previous empty): generate 3-5 concrete analysis angles from task + file types.
# Round N (previous has conflict/gap): generate targeted resolution points for each issue.
# ===========================================================================

PLAN_AGENT_PROFILE = """
You are an analysis strategist for a multi-model reasoning system.
Before each analysis round, you generate precise, targeted guidance that tells
the analysis models exactly what to look for and which angles to take.
"""

PLAN_RULES = """
<plan_rules>
**First round (no prior results)**
- Based only on the task intent and file types, suggest 3-5 broad analytical frameworks
  or general question categories — do NOT assume specific values, locations, or details
  that you have not yet observed in the content.
- Think in terms of "what kinds of things are worth examining" given this task type,
  not "what specific thing to find". The analysis models will discover the specifics.
  (e.g. for a chart task: "examine scale and units, overall trends, outliers, and any annotations"
   NOT "check the Y-axis at 2020" — you don't know what the chart looks like yet)

**Subsequent rounds (prior results with conflicts or gaps)**
- Now you have concrete findings from prior rounds — be more targeted.
- For each conflict: quote the exact disagreement from the prior summary and specify
  what evidence or observation would resolve it.
- For gaps: name the specific unexplored angle that is most likely to complete the answer.
- Do not revisit angles that are already settled.

Keep guidance under 200 words. Use concise bullet points only.
</plan_rules>
"""

PLAN_SYSTEM_PROMPT_TEMPLATE = """
{{ plan_agent_profile }}
{{ plan_rules }}
"""

PLAN_AGENT_MESSAGE_TEMPLATE = """Task: {{ task }}

Content available:
{{ file_info }}
{% if previous %}

Prior round results (conflicts / gaps to resolve):
{{ previous }}

Generate targeted guidance to resolve the above in this round.
{% else %}
Generate analysis guidance for round 1 — concrete angles and specific questions to examine.
{% endif %}"""

PLAN_SYSTEM_PROMPT = {
    "name": "deep_analyzer_v2_plan_system_prompt",
    "type": "system_prompt",
    "description": "System prompt for per-round analysis planning",
    "require_grad": True,
    "template": PLAN_SYSTEM_PROMPT_TEMPLATE,
    "variables": {
        "plan_agent_profile": {
            "name": "plan_agent_profile",
            "type": "system_prompt",
            "description": "Core identity of the planning component.",
            "require_grad": False,
            "template": None,
            "variables": PLAN_AGENT_PROFILE,
        },
        "plan_rules": {
            "name": "plan_rules",
            "type": "system_prompt",
            "description": "Rules for generating round-specific analysis guidance.",
            "require_grad": True,
            "template": None,
            "variables": PLAN_RULES,
        },
    },
}

PLAN_AGENT_MESSAGE_PROMPT = {
    "name": "deep_analyzer_v2_plan_agent_message_prompt",
    "type": "agent_message_prompt",
    "description": "Per-round dynamic context for analysis planning",
    "require_grad": False,
    "template": PLAN_AGENT_MESSAGE_TEMPLATE,
    "variables": {
        "task": {
            "name": "task",
            "type": "agent_message_prompt",
            "description": "The analysis task.",
            "require_grad": False,
            "template": None,
            "variables": None,
        },
        "file_info": {
            "name": "file_info",
            "type": "agent_message_prompt",
            "description": "Newline list of available files and their types; empty string if no files.",
            "require_grad": False,
            "template": None,
            "variables": None,
        },
        "previous": {
            "name": "previous",
            "type": "agent_message_prompt",
            "description": "Execution log of prior rounds; empty on round 1.",
            "require_grad": False,
            "template": None,
            "variables": None,
        },
    },
}


@PROMPT.register_module(force=True)
class DeepAnalyzerV2PlanSystemPrompt(Prompt):
    """System prompt for per-round analysis planning."""
    model_config = ConfigDict(arbitrary_types_allowed=True, extra="allow")

    type: str = Field(default="system_prompt")
    name: str = Field(default="deep_analyzer_v2_plan")
    description: str = Field(default="System prompt for per-round analysis planning")
    require_grad: bool = Field(default=True)
    metadata: Dict[str, Any] = Field(default_factory=dict)

    prompt_config: Dict[str, Any] = Field(default=PLAN_SYSTEM_PROMPT)


@PROMPT.register_module(force=True)
class DeepAnalyzerV2PlanAgentMessagePrompt(Prompt):
    """Agent message prompt for per-round analysis planning."""
    model_config = ConfigDict(arbitrary_types_allowed=True, extra="allow")

    type: str = Field(default="agent_message_prompt")
    name: str = Field(default="deep_analyzer_v2_plan")
    description: str = Field(default="Per-round dynamic context for analysis planning")
    require_grad: bool = Field(default=False)
    metadata: Dict[str, Any] = Field(default_factory=dict)

    prompt_config: Dict[str, Any] = Field(default=PLAN_AGENT_MESSAGE_PROMPT)


# ===========================================================================
# Analyze — unified prompt for media-file analysis and direct reasoning
#
# Variables:
#   task      — the analysis task (always required)
#   file_type — "image" / "audio" / "video" when a media file is attached;
#               empty string "" when no file is present (direct reasoning)
#   previous  — execution log of prior rounds; empty string "" on round 1
#
# The template uses Jinja2 conditionals to adapt the instruction based on
# whether a file is present and whether prior context exists.
# ===========================================================================

ANALYZE_AGENT_PROFILE = """
You are a thorough analysis and reasoning assistant. Depending on the task, you may
be given a media file to examine (image, audio, or video) or asked to reason directly
from your knowledge. In either case, produce a comprehensive, precise answer.
"""

ANALYZE_RULES = """
<analyze_rules>
- Answer the task directly and comprehensively based on what you observe or know.
- Take into account all prior reasoning from previous rounds — do not repeat already-established points.
- If the task cannot be answered with confidence, clearly state what is uncertain and why.

**When a media file is provided**
- Base your answer strictly on what you can directly perceive in the file. Do not
  infer content from filenames, titles, or surrounding metadata alone.
- If any part of the media is ambiguous or unclear, state that explicitly rather than
  guessing. A confident-sounding fabrication is worse than an honest "I cannot
  determine this from the provided content."
- When the task asks for precise detail (exact values, sequences, positions, timings),
  verify each item against the media before including it in your answer.
- For visual content (diagrams, equations, text, board positions), transcribe verbatim
  rather than paraphrasing — small differences in notation or arrangement are
  semantically significant.

**Precision self-check** — before finalising any answer, ask yourself:
- **Boundary conditions**: does my conclusion still hold at the extremes of the domain?
  Have I checked every degenerate case where the general formula might break down?
- **Every constant and coefficient**: have I verified each multiplicative factor, sign,
  and exponent in my derivation from first principles? An otherwise correct argument
  with one wrong constant is a wrong answer.
- **Exact qualifiers in notation**: does my answer preserve every qualifier present in
  the task (strict vs non-strict, open vs closed, modifier superscripts/subscripts,
  subgroup notation, etc.)? Do not silently drop or simplify any part of the required form.
- **Implicit conventions in the problem**: what definitions or conventions does this
  problem assume? Are there terms whose scope or meaning in this specific domain
  differs from their everyday or default interpretation? Resolve those before computing.
- **Multiple choice — all options checked**: have I explicitly evaluated every option,
  including meta-options ("none of the above", combinations), before committing to
  an answer? Never stop at the first plausible-looking option.
</analyze_rules>
"""

ANALYZE_SYSTEM_PROMPT_TEMPLATE = """
{{ analyze_agent_profile }}
{{ analyze_rules }}
"""

ANALYZE_AGENT_MESSAGE_TEMPLATE = """Task: {{ task }}
{% if guidance %}

Analysis guidance for this round:
{{ guidance }}
{% endif %}
{% if previous %}

Prior round results:
{{ previous }}
{% endif %}
{% if file_type %}
Analyze the provided {{ file_type }} following the guidance above and answer the task.
{% else %}
Provide a thorough analysis and answer following the guidance above.
{% endif %}"""

ANALYZE_SYSTEM_PROMPT = {
    "name": "deep_analyzer_v2_analyze_system_prompt",
    "type": "system_prompt",
    "description": "System prompt for unified analysis (media files or direct reasoning)",
    "require_grad": True,
    "template": ANALYZE_SYSTEM_PROMPT_TEMPLATE,
    "variables": {
        "analyze_agent_profile": {
            "name": "analyze_agent_profile",
            "type": "system_prompt",
            "description": "Core identity of the analysis component.",
            "require_grad": False,
            "template": None,
            "variables": ANALYZE_AGENT_PROFILE,
        },
        "analyze_rules": {
            "name": "analyze_rules",
            "type": "system_prompt",
            "description": "Rules for analysis covering both media and direct reasoning.",
            "require_grad": True,
            "template": None,
            "variables": ANALYZE_RULES,
        },
    },
}

ANALYZE_AGENT_MESSAGE_PROMPT = {
    "name": "deep_analyzer_v2_analyze_agent_message_prompt",
    "type": "agent_message_prompt",
    "description": "Dynamic context for unified analysis (media or direct)",
    "require_grad": False,
    "template": ANALYZE_AGENT_MESSAGE_TEMPLATE,
    "variables": {
        "task": {
            "name": "task",
            "type": "agent_message_prompt",
            "description": "The analysis task.",
            "require_grad": False,
            "template": None,
            "variables": None,
        },
        "file_type": {
            "name": "file_type",
            "type": "agent_message_prompt",
            "description": "Media type (image/audio/video) when a file is attached; empty string for direct reasoning.",
            "require_grad": False,
            "template": None,
            "variables": None,
        },
        "guidance": {
            "name": "guidance",
            "type": "agent_message_prompt",
            "description": "Analysis guidance from the planner: angles for round 1, conflict-resolution points for later rounds.",
            "require_grad": False,
            "template": None,
            "variables": None,
        },
        "previous": {
            "name": "previous",
            "type": "agent_message_prompt",
            "description": "Plain-text log of prior analysis rounds; empty string on round 1.",
            "require_grad": False,
            "template": None,
            "variables": None,
        },
    },
}


@PROMPT.register_module(force=True)
class DeepAnalyzerV2AnalyzeSystemPrompt(Prompt):
    """System prompt for unified analysis (media files or direct reasoning)."""
    model_config = ConfigDict(arbitrary_types_allowed=True, extra="allow")

    type: str = Field(default="system_prompt")
    name: str = Field(default="deep_analyzer_v2_analyze")
    description: str = Field(default="System prompt for unified analysis (media files or direct reasoning)")
    require_grad: bool = Field(default=True)
    metadata: Dict[str, Any] = Field(default_factory=dict)

    prompt_config: Dict[str, Any] = Field(default=ANALYZE_SYSTEM_PROMPT)


@PROMPT.register_module(force=True)
class DeepAnalyzerV2AnalyzeAgentMessagePrompt(Prompt):
    """Agent message prompt for unified analysis."""
    model_config = ConfigDict(arbitrary_types_allowed=True, extra="allow")

    type: str = Field(default="agent_message_prompt")
    name: str = Field(default="deep_analyzer_v2_analyze")
    description: str = Field(default="Dynamic context for unified analysis (media or direct)")
    require_grad: bool = Field(default=False)
    metadata: Dict[str, Any] = Field(default_factory=dict)

    prompt_config: Dict[str, Any] = Field(default=ANALYZE_AGENT_MESSAGE_PROMPT)


# ===========================================================================
# Eval — synthesize multi-model results, detect conflicts, assess completeness
# ===========================================================================

EVAL_AGENT_PROFILE = """
You are a rigorous analysis evaluator. You receive analyses from one or more models,
synthesize their findings, detect any contradictions between them, and determine
whether the task has been fully answered.
"""

EVAL_RULES = """
<eval_rules>
- Each model's analysis is prefixed with **[model-name]**.
- Check whether the models agree or contradict each other on key points.

**reasoning field** (required):
- Capture the key conclusions and the critical logic that led to them.
- If models agree: state the shared conclusion and the strongest supporting argument.
- If models conflict: describe the exact disagreement and what evidence would resolve it.
- If found_answer is true: explain briefly *why* the answer is correct (key reasoning step).
- Do NOT require or reproduce a full derivation — record the insight, not the work.
- **Multiple-choice tasks**: if the task presents discrete options (A/B/C/D or similar), the reasoning MUST include an explicit analysis of EVERY option — stating why each is correct or incorrect — before committing to an answer. Do not stop at the first plausible-looking option.

**found_answer field**:
- Set found_answer=true when the answer is clearly determined and models agree, even if
  a full step-by-step derivation was not shown. A consistent, well-reasoned answer from
  one or more models is sufficient — do NOT demand exhaustive proof before accepting an answer.
- Set found_answer=false only when the answer is genuinely uncertain, contradicted, or missing.

**answer field**:
- If found_answer is true, extract the final answer as concisely as possible.
- Preserve exact mathematical notation, qualifiers, and any symbolic form required by the task.
- **Scope the answer strictly to what the task asks for.** If the task asks for one specific
  item and additional related material was provided only as context or examples, include ONLY
  the requested item — do not append the auxiliary context to the answer.
  (e.g. if the task asks "decipher ciphertext 1" and ciphertext 2 was provided as a hint,
  the answer is ONLY the decryption of ciphertext 1, not both)

**has_conflict field**:
- Set has_conflict=true if models produced meaningfully contradictory conclusions on
  task-relevant points. Minor wording differences do not count as conflicts.

- Respond in JSON with fields: reasoning, found_answer, answer, has_conflict.
</eval_rules>
"""

EVAL_SYSTEM_PROMPT_TEMPLATE = """
{{ eval_agent_profile }}
{{ eval_rules }}
"""

EVAL_AGENT_MESSAGE_TEMPLATE = """
Task: {{ task }}
{% if previous %}
Prior rounds:
{{ previous }}
{% endif %}

Current round — model analyses:
{{ content }}

Based on all model analyses above:
1. Detect any conflicts or contradictions between models.
2. Write reasoning: key conclusions + critical logic. If found_answer=true, explain why the answer is correct. If conflict, describe the exact disagreement. **If the task is multiple-choice, explicitly analyze every option (why correct or incorrect) before committing to an answer.**
3. Determine if the task is fully answered (found_answer: true/false). Trust a consistent answer even without a full derivation — do NOT require exhaustive proof.
4. If found_answer is true, extract the final answer into the answer field exactly as needed. Scope strictly to what the task asks — do NOT include auxiliary examples or context provided only as hints.
5. Set has_conflict=true only for meaningful contradictions on task-relevant points.
6. If found_answer is false, ensure reasoning precisely describes the conflict or gap so the next round can target it.
"""

EVAL_SYSTEM_PROMPT = {
    "name": "deep_analyzer_v2_eval_system_prompt",
    "type": "system_prompt",
    "description": "System prompt for per-round completeness evaluation",
    "require_grad": True,
    "template": EVAL_SYSTEM_PROMPT_TEMPLATE,
    "variables": {
        "eval_agent_profile": {
            "name": "eval_agent_profile",
            "type": "system_prompt",
            "description": "Core identity of the evaluation component.",
            "require_grad": False,
            "template": None,
            "variables": EVAL_AGENT_PROFILE,
        },
        "eval_rules": {
            "name": "eval_rules",
            "type": "system_prompt",
            "description": "Rules for evaluating analysis completeness.",
            "require_grad": True,
            "template": None,
            "variables": EVAL_RULES,
        },
    },
}

EVAL_AGENT_MESSAGE_PROMPT = {
    "name": "deep_analyzer_v2_eval_agent_message_prompt",
    "type": "agent_message_prompt",
    "description": "Per-round dynamic context for completeness evaluation",
    "require_grad": False,
    "template": EVAL_AGENT_MESSAGE_TEMPLATE,
    "variables": {
        "task": {
            "name": "task",
            "type": "agent_message_prompt",
            "description": "The analysis task.",
            "require_grad": False,
            "template": None,
            "variables": None,
        },
        "previous": {
            "name": "previous",
            "type": "agent_message_prompt",
            "description": "Plain-text log of prior analysis rounds.",
            "require_grad": False,
            "template": None,
            "variables": None,
        },
        "content": {
            "name": "content",
            "type": "agent_message_prompt",
            "description": "Combined analysis output from all models this round, each prefixed with **[model-name]**.",
            "require_grad": False,
            "template": None,
            "variables": None,
        },
    },
}


@PROMPT.register_module(force=True)
class DeepAnalyzerV2EvalSystemPrompt(Prompt):
    """System prompt for per-round completeness evaluation."""
    model_config = ConfigDict(arbitrary_types_allowed=True, extra="allow")

    type: str = Field(default="system_prompt")
    name: str = Field(default="deep_analyzer_v2_eval")
    description: str = Field(default="System prompt for per-round completeness evaluation")
    require_grad: bool = Field(default=True)
    metadata: Dict[str, Any] = Field(default_factory=dict)

    prompt_config: Dict[str, Any] = Field(default=EVAL_SYSTEM_PROMPT)


@PROMPT.register_module(force=True)
class DeepAnalyzerV2EvalAgentMessagePrompt(Prompt):
    """Agent message prompt for per-round completeness evaluation."""
    model_config = ConfigDict(arbitrary_types_allowed=True, extra="allow")

    type: str = Field(default="agent_message_prompt")
    name: str = Field(default="deep_analyzer_v2_eval")
    description: str = Field(default="Per-round dynamic context for completeness evaluation")
    require_grad: bool = Field(default=False)
    metadata: Dict[str, Any] = Field(default_factory=dict)

    prompt_config: Dict[str, Any] = Field(default=EVAL_AGENT_MESSAGE_PROMPT)
