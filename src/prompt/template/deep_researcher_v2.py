"""Prompt templates for DeepResearcherV2Agent.

Three top-level modules cover the research pipeline:

  | Prompt                           | 用途                                         |
  |----------------------------------|----------------------------------------------|
  | deep_researcher_v2_query         | 每轮生成搜索 query；有冲突时生成解决要点      |
  | deep_researcher_v2_llm_search    | LLM 联网搜索（每个模型一份报告）              |
  | deep_researcher_v2_eval          | 综合所有报告、检测冲突、评估完整性            |

Internal helpers (used inside the search step):
  | deep_researcher_v2_page_summary  | 单页内容摘要（Jina 抓取后）                  |
  | deep_researcher_v2_synthesis     | 合并页面摘要为一份 API 搜索报告（带引用）     |
"""

from src.registry import PROMPT
from src.prompt.types import Prompt
from typing import Any, Dict
from pydantic import Field, ConfigDict


# ===========================================================================
# Query — per-round search query generation
#
# Variables:
#   task             — the research task
#   image            — optional image path/URL; empty string if none
#   filter_year      — optional year to bias results; empty string if none
#   previous_context — execution log of prior rounds; empty if round 1
#
# Round 1 (previous_context empty): generate a broad initial query.
# Round N (previous_context has conflict/gap): generate a targeted query
#   to resolve the specific conflict or fill the gap described.
# ===========================================================================

QUERY_AGENT_PROFILE = """
You are a search strategist for a multi-round research system.
Before each search round, you generate an optimized query that maximizes the
chance of finding the information needed to answer the research task.
"""

QUERY_RULES = """
<query_rules>
**Query format**
- Write a short, direct question or statement — one sentence, as concise as possible.
- Strip all filler words and background context. Include only what is essential for finding the answer.
- Examples: "GELU paper original authors" is too short; "Who are the authors of the GELU paper?" is good; "I need to find out who originally authored the GELU activation function paper" is too long.
- Do NOT use keyword fragments. A grammatical sentence gives better semantic search results.

**First round (no prior results)**
- Generate a focused question or statement that captures the core intent of the task.

**Subsequent rounds (prior results exist)**
- You now know what was found and what remains unresolved — be more targeted.
- If prior evaluation describes a conflict: generate a query that targets evidence
  to resolve the specific disagreement.
- If prior evaluation describes a gap or missing information: generate a query that
  directly targets that missing piece.
- If prior rounds were simply incomplete: explore a different angle or related topic.
- Use different phrasing compared to previous rounds. Do not repeat a query already tried.

**Academic and technical topics — prioritise specialised sources**
For domain-specific queries, prefer authoritative sources over generic web search:
math/logic - `arxiv.org`, `mathoverflow.net`, `ncatlab.org`;
philosophy - `plato.stanford.edu`, `philpapers.org`;
sciences - `arxiv.org`, `pubmed.ncbi.nlm.nih.gov`, `semanticscholar.org`;
CS/AI/ML - `arxiv.org`, `semanticscholar.org`, `dl.acm.org`;
linguistics - `glottolog.org`, `wals.info`.

If filter_year is provided, bias the query toward recent results from that year.
If an image is provided, incorporate relevant visual information into the query.
Return ONLY the search query — no explanation, no punctuation at the end.
</query_rules>
"""

QUERY_SYSTEM_PROMPT_TEMPLATE = """
{{ query_agent_profile }}
{{ query_rules }}
"""

QUERY_AGENT_MESSAGE_TEMPLATE = """Research task: "{{ task }}"
{% if image %}

Image provided: {{ image }}
{% endif %}
{% if filter_year %}

Prefer results from year: {{ filter_year }}
{% endif %}
{% if previous_context %}

Prior research rounds (conflicts / gaps to resolve):
{{ previous_context }}

Generate a NEW search query that targets information NOT yet found or resolves the above conflict/gap.
{% else %}
Generate an optimized search query for this task.
{% endif %}

Return only the search query, nothing else."""

QUERY_SYSTEM_PROMPT = {
    "name": "deep_researcher_v2_query_system_prompt",
    "type": "system_prompt",
    "description": "System prompt for per-round search query generation",
    "require_grad": True,
    "template": QUERY_SYSTEM_PROMPT_TEMPLATE,
    "variables": {
        "query_agent_profile": {
            "name": "query_agent_profile",
            "type": "system_prompt",
            "description": "Core identity of the query generation component.",
            "require_grad": False,
            "template": None,
            "variables": QUERY_AGENT_PROFILE,
        },
        "query_rules": {
            "name": "query_rules",
            "type": "system_prompt",
            "description": "Rules for generating focused search queries.",
            "require_grad": True,
            "template": None,
            "variables": QUERY_RULES,
        },
    },
}

QUERY_AGENT_MESSAGE_PROMPT = {
    "name": "deep_researcher_v2_query_agent_message_prompt",
    "type": "agent_message_prompt",
    "description": "Per-round dynamic context for search query generation",
    "require_grad": False,
    "template": QUERY_AGENT_MESSAGE_TEMPLATE,
    "variables": {
        "task": {
            "name": "task",
            "type": "agent_message_prompt",
            "description": "The research task.",
            "require_grad": False,
            "template": None,
            "variables": None,
        },
        "image": {
            "name": "image",
            "type": "agent_message_prompt",
            "description": "Optional image path or URL; empty string if none.",
            "require_grad": False,
            "template": None,
            "variables": None,
        },
        "filter_year": {
            "name": "filter_year",
            "type": "agent_message_prompt",
            "description": "Optional year to bias search results toward; empty string if none.",
            "require_grad": False,
            "template": None,
            "variables": None,
        },
        "previous_context": {
            "name": "previous_context",
            "type": "agent_message_prompt",
            "description": "Execution log of prior research rounds; empty string on round 1.",
            "require_grad": False,
            "template": None,
            "variables": None,
        },
    },
}


@PROMPT.register_module(force=True)
class DeepResearcherV2QuerySystemPrompt(Prompt):
    """System prompt for per-round search query generation."""
    model_config = ConfigDict(arbitrary_types_allowed=True, extra="allow")

    type: str = Field(default="system_prompt")
    name: str = Field(default="deep_researcher_v2_query")
    description: str = Field(default="System prompt for per-round search query generation")
    require_grad: bool = Field(default=True)
    metadata: Dict[str, Any] = Field(default_factory=dict)

    prompt_config: Dict[str, Any] = Field(default=QUERY_SYSTEM_PROMPT)


@PROMPT.register_module(force=True)
class DeepResearcherV2QueryAgentMessagePrompt(Prompt):
    """Agent message prompt for per-round search query generation."""
    model_config = ConfigDict(arbitrary_types_allowed=True, extra="allow")

    type: str = Field(default="agent_message_prompt")
    name: str = Field(default="deep_researcher_v2_query")
    description: str = Field(default="Per-round dynamic context for search query generation")
    require_grad: bool = Field(default=False)
    metadata: Dict[str, Any] = Field(default_factory=dict)

    prompt_config: Dict[str, Any] = Field(default=QUERY_AGENT_MESSAGE_PROMPT)


# ===========================================================================
# Page Summary (internal) — per-page content summarization (Jina fetch → LLM)
# ===========================================================================

PAGE_SUMMARY_AGENT_PROFILE = """
You are a precise information extractor. Given a web page and a search query,
your job is to extract only the information relevant to the query.
"""

PAGE_SUMMARY_RULES = """
<page_summary_rules>
- Extract key information relevant to the query in 2-4 concise sentences.
- Prioritize facts, figures, and direct answers over background context.
- Do not include information unrelated to the query.
- If the page contains no relevant information, respond with a single sentence saying so.
</page_summary_rules>
"""

PAGE_SUMMARY_SYSTEM_PROMPT_TEMPLATE = """
{{ page_summary_agent_profile }}
{{ page_summary_rules }}
"""

PAGE_SUMMARY_AGENT_MESSAGE_TEMPLATE = """
Given search query: "{{ query }}"

Page title: {{ title }}
Page content:
{{ content }}

Extract the key information relevant to the query in 2-4 concise sentences.
"""

PAGE_SUMMARY_SYSTEM_PROMPT = {
    "name": "deep_researcher_v2_page_summary_system_prompt",
    "type": "system_prompt",
    "description": "System prompt for per-page content summarization",
    "require_grad": True,
    "template": PAGE_SUMMARY_SYSTEM_PROMPT_TEMPLATE,
    "variables": {
        "page_summary_agent_profile": {
            "name": "page_summary_agent_profile",
            "type": "system_prompt",
            "description": "Core identity of the page summarization component.",
            "require_grad": False,
            "template": None,
            "variables": PAGE_SUMMARY_AGENT_PROFILE,
        },
        "page_summary_rules": {
            "name": "page_summary_rules",
            "type": "system_prompt",
            "description": "Rules for extracting relevant content from a web page.",
            "require_grad": True,
            "template": None,
            "variables": PAGE_SUMMARY_RULES,
        },
    },
}

PAGE_SUMMARY_AGENT_MESSAGE_PROMPT = {
    "name": "deep_researcher_v2_page_summary_agent_message_prompt",
    "type": "agent_message_prompt",
    "description": "Per-page dynamic context for content summarization",
    "require_grad": False,
    "template": PAGE_SUMMARY_AGENT_MESSAGE_TEMPLATE,
    "variables": {
        "query": {
            "name": "query",
            "type": "agent_message_prompt",
            "description": "The search query used to retrieve this page.",
            "require_grad": False,
            "template": None,
            "variables": None,
        },
        "title": {
            "name": "title",
            "type": "agent_message_prompt",
            "description": "The page title.",
            "require_grad": False,
            "template": None,
            "variables": None,
        },
        "content": {
            "name": "content",
            "type": "agent_message_prompt",
            "description": "The fetched page content (markdown).",
            "require_grad": False,
            "template": None,
            "variables": None,
        },
    },
}


@PROMPT.register_module(force=True)
class DeepResearcherV2PageSummarySystemPrompt(Prompt):
    """System prompt for per-page content summarization."""
    model_config = ConfigDict(arbitrary_types_allowed=True, extra="allow")

    type: str = Field(default="system_prompt")
    name: str = Field(default="deep_researcher_v2_page_summary")
    description: str = Field(default="System prompt for per-page content summarization")
    require_grad: bool = Field(default=True)
    metadata: Dict[str, Any] = Field(default_factory=dict)

    prompt_config: Dict[str, Any] = Field(default=PAGE_SUMMARY_SYSTEM_PROMPT)


@PROMPT.register_module(force=True)
class DeepResearcherV2PageSummaryAgentMessagePrompt(Prompt):
    """Agent message prompt for per-page content summarization."""
    model_config = ConfigDict(arbitrary_types_allowed=True, extra="allow")

    type: str = Field(default="agent_message_prompt")
    name: str = Field(default="deep_researcher_v2_page_summary")
    description: str = Field(default="Per-page dynamic context for content summarization")
    require_grad: bool = Field(default=False)
    metadata: Dict[str, Any] = Field(default_factory=dict)

    prompt_config: Dict[str, Any] = Field(default=PAGE_SUMMARY_AGENT_MESSAGE_PROMPT)


# ===========================================================================
# Synthesis (internal) — combine page summaries into a coherent report
# ===========================================================================

SYNTHESIS_AGENT_PROFILE = """
You are an expert research synthesizer. Given multiple web page summaries retrieved
for a search query, your job is to integrate them into a single, coherent research summary.
"""

SYNTHESIS_RULES = """
<synthesis_rules>
- Integrate the key findings from all sources into a coherent, well-structured summary.
- Prioritize information that directly answers the search query.
- Resolve contradictions between sources by noting the disagreement.
- Do not list sources inline — they will be appended as a reference list separately.
- Do not introduce information not present in the provided summaries.
</synthesis_rules>
"""

SYNTHESIS_SYSTEM_PROMPT_TEMPLATE = """
{{ synthesis_agent_profile }}
{{ synthesis_rules }}
"""

SYNTHESIS_AGENT_MESSAGE_TEMPLATE = """
Search query: "{{ query }}"

The following are summaries from {{ num_sources }} web pages retrieved for this query:

{{ sources }}

Synthesize these into a single, comprehensive, well-structured research summary.
Integrate the key findings coherently. Do not list sources inline —
they will be appended as a reference list separately.
"""

SYNTHESIS_SYSTEM_PROMPT = {
    "name": "deep_researcher_v2_synthesis_system_prompt",
    "type": "system_prompt",
    "description": "System prompt for synthesizing page summaries into a research report",
    "require_grad": True,
    "template": SYNTHESIS_SYSTEM_PROMPT_TEMPLATE,
    "variables": {
        "synthesis_agent_profile": {
            "name": "synthesis_agent_profile",
            "type": "system_prompt",
            "description": "Core identity of the synthesis component.",
            "require_grad": False,
            "template": None,
            "variables": SYNTHESIS_AGENT_PROFILE,
        },
        "synthesis_rules": {
            "name": "synthesis_rules",
            "type": "system_prompt",
            "description": "Rules for synthesizing multiple page summaries.",
            "require_grad": True,
            "template": None,
            "variables": SYNTHESIS_RULES,
        },
    },
}

SYNTHESIS_AGENT_MESSAGE_PROMPT = {
    "name": "deep_researcher_v2_synthesis_agent_message_prompt",
    "type": "agent_message_prompt",
    "description": "Dynamic context for page summary synthesis",
    "require_grad": False,
    "template": SYNTHESIS_AGENT_MESSAGE_TEMPLATE,
    "variables": {
        "query": {
            "name": "query",
            "type": "agent_message_prompt",
            "description": "The search query whose results are being synthesized.",
            "require_grad": False,
            "template": None,
            "variables": None,
        },
        "num_sources": {
            "name": "num_sources",
            "type": "agent_message_prompt",
            "description": "Number of source pages being synthesized.",
            "require_grad": False,
            "template": None,
            "variables": None,
        },
        "sources": {
            "name": "sources",
            "type": "agent_message_prompt",
            "description": "Pre-formatted list of [i] title (url)\\nsummary entries.",
            "require_grad": False,
            "template": None,
            "variables": None,
        },
    },
}


@PROMPT.register_module(force=True)
class DeepResearcherV2SynthesisSystemPrompt(Prompt):
    """System prompt for page summary synthesis."""
    model_config = ConfigDict(arbitrary_types_allowed=True, extra="allow")

    type: str = Field(default="system_prompt")
    name: str = Field(default="deep_researcher_v2_synthesis")
    description: str = Field(default="System prompt for synthesizing page summaries into a research report")
    require_grad: bool = Field(default=True)
    metadata: Dict[str, Any] = Field(default_factory=dict)

    prompt_config: Dict[str, Any] = Field(default=SYNTHESIS_SYSTEM_PROMPT)


@PROMPT.register_module(force=True)
class DeepResearcherV2SynthesisAgentMessagePrompt(Prompt):
    """Agent message prompt for page summary synthesis."""
    model_config = ConfigDict(arbitrary_types_allowed=True, extra="allow")

    type: str = Field(default="agent_message_prompt")
    name: str = Field(default="deep_researcher_v2_synthesis")
    description: str = Field(default="Dynamic context for page summary synthesis")
    require_grad: bool = Field(default=False)
    metadata: Dict[str, Any] = Field(default_factory=dict)

    prompt_config: Dict[str, Any] = Field(default=SYNTHESIS_AGENT_MESSAGE_PROMPT)


# ===========================================================================
# LLM Search — prompt for web-search-capable LLMs (one report per model)
#
# Variables:
#   task  — the original research task (context)
#   query — the optimized search query for this round
# ===========================================================================

LLM_SEARCH_AGENT_PROFILE = """
You are a research assistant with web search capability. Your job is to research a query
and return a comprehensive, well-cited summary of the findings.
"""

LLM_SEARCH_RULES = """
<llm_search_rules>
- Research the query thoroughly and provide a comprehensive summary with citations where possible.
- Include the most relevant and up-to-date information.
- Cite your sources inline where possible (e.g., [Source Name](URL)).
- Return your findings as a comprehensive, well-structured summary.
</llm_search_rules>
"""

LLM_SEARCH_SYSTEM_PROMPT_TEMPLATE = """
{{ llm_search_agent_profile }}
{{ llm_search_rules }}
"""

LLM_SEARCH_AGENT_MESSAGE_TEMPLATE = """Original task context: {{ task }}

Search query: {{ query }}

Research the query and return your findings as a comprehensive summary."""

LLM_SEARCH_SYSTEM_PROMPT = {
    "name": "deep_researcher_v2_llm_search_system_prompt",
    "type": "system_prompt",
    "description": "System prompt for LLM-based web search (one report per model)",
    "require_grad": True,
    "template": LLM_SEARCH_SYSTEM_PROMPT_TEMPLATE,
    "variables": {
        "llm_search_agent_profile": {
            "name": "llm_search_agent_profile",
            "type": "system_prompt",
            "description": "Core identity of the LLM search component.",
            "require_grad": False,
            "template": None,
            "variables": LLM_SEARCH_AGENT_PROFILE,
        },
        "llm_search_rules": {
            "name": "llm_search_rules",
            "type": "system_prompt",
            "description": "Rules for conducting and reporting LLM web search.",
            "require_grad": True,
            "template": None,
            "variables": LLM_SEARCH_RULES,
        },
    },
}

LLM_SEARCH_AGENT_MESSAGE_PROMPT = {
    "name": "deep_researcher_v2_llm_search_agent_message_prompt",
    "type": "agent_message_prompt",
    "description": "Per-round dynamic context for LLM web search",
    "require_grad": False,
    "template": LLM_SEARCH_AGENT_MESSAGE_TEMPLATE,
    "variables": {
        "task": {
            "name": "task",
            "type": "agent_message_prompt",
            "description": "The original research task for context.",
            "require_grad": False,
            "template": None,
            "variables": None,
        },
        "query": {
            "name": "query",
            "type": "agent_message_prompt",
            "description": "The optimized search query for this round.",
            "require_grad": False,
            "template": None,
            "variables": None,
        },
    },
}


@PROMPT.register_module(force=True)
class DeepResearcherV2LLMSearchSystemPrompt(Prompt):
    """System prompt for LLM-based web search."""
    model_config = ConfigDict(arbitrary_types_allowed=True, extra="allow")

    type: str = Field(default="system_prompt")
    name: str = Field(default="deep_researcher_v2_llm_search")
    description: str = Field(default="System prompt for LLM-based web search (one report per model)")
    require_grad: bool = Field(default=True)
    metadata: Dict[str, Any] = Field(default_factory=dict)

    prompt_config: Dict[str, Any] = Field(default=LLM_SEARCH_SYSTEM_PROMPT)


@PROMPT.register_module(force=True)
class DeepResearcherV2LLMSearchAgentMessagePrompt(Prompt):
    """Agent message prompt for LLM-based web search."""
    model_config = ConfigDict(arbitrary_types_allowed=True, extra="allow")

    type: str = Field(default="agent_message_prompt")
    name: str = Field(default="deep_researcher_v2_llm_search")
    description: str = Field(default="Per-round dynamic context for LLM web search")
    require_grad: bool = Field(default=False)
    metadata: Dict[str, Any] = Field(default_factory=dict)

    prompt_config: Dict[str, Any] = Field(default=LLM_SEARCH_AGENT_MESSAGE_PROMPT)


# ===========================================================================
# Eval — synthesize all search reports, detect conflicts, assess completeness
#
# Variables:
#   task     — the research task
#   previous — execution log of prior rounds; empty string on round 1
#   content  — all search reports from this round, each prefixed with
#              **[api_search]** or **[model-name]**
#
# Input format:
#   **[api_search]**
#   <synthesized report with citations>
#
#   **[model-name]**
#   <llm search report>
#   ...
# ===========================================================================

EVAL_AGENT_PROFILE = """
You are a rigorous research evaluator. You receive reports from multiple search sources
(API search and one or more LLM search models), synthesize their findings, detect any
contradictions between them, and determine whether the research task has been fully answered.
"""

EVAL_RULES = """
<eval_rules>
- Each source's report is prefixed with **[source-name]** (e.g., **[api_search]**, **[model-name]**).
- Check whether the sources agree or contradict each other on key points relevant to the task.

**reasoning field** (required):
- Capture the key conclusions and critical logic from the search results.
- Always name the source(s) that provided each key finding, using the label in the report
  header (e.g. "**[google_lens_search]** identified X", "**[firecrawl_search]** found Y").
- If sources agree: state the shared finding and strongest supporting evidence.
- If sources conflict: describe the exact disagreement and what evidence would resolve it.
- If found_answer is true: explain briefly *why* the answer is correct (key evidence/logic).
- Do NOT reproduce all the search content — record the insight, not the report.
- **Multiple-choice tasks**: if the task presents discrete options (A/B/C/D or similar), the reasoning MUST include an explicit analysis of EVERY option — stating why each is correct or incorrect — before committing to an answer. Do not stop at the first plausible-looking option.

**found_answer field**:
- Set found_answer=true when the answer is clearly determined and supported by sources, even
  if not every source confirmed it. Consistent evidence from one or more credible sources is
  sufficient — do NOT demand unanimous confirmation before accepting an answer.
- Set found_answer=false only when the answer is genuinely uncertain, contradicted, or missing.

**answer field**:
- If found_answer is true, extract the final answer as concisely as possible.
- Preserve exact notation, qualifiers, and any symbolic form required by the task.
- **Scope the answer strictly to what the task asks for.** If the task asks for one specific
  item and additional related material was provided only as context or examples, include ONLY
  the requested item — do not append the auxiliary context to the answer.
  (e.g. if the task asks about item A and item B was provided as background, answer is only about A)

**has_conflict field**:
- Set has_conflict=true if sources produced meaningfully contradictory conclusions on
  task-relevant points. Minor wording or coverage differences do not count as conflicts.

**Source authority for image tasks**:
- When a **[google_lens_search]** report is present, the task involves an image.
  In that case, the Google Lens result carries higher evidentiary authority than
  LLM-generated reasoning for questions about the image's origin, title, author,
  or identity — because Google Lens performs a direct reverse image search against
  a real index, while LLM sources reason from general knowledge.
- If **[google_lens_search]** returned a concrete identification (name, title,
  source) and an LLM source returned a different or vaguer answer, prefer the
  Google Lens result and do NOT treat this as a conflict requiring another round.
- Only set has_conflict=true for a Google Lens result if it is internally
  inconsistent or clearly impossible.

- Respond in JSON with fields: reasoning, found_answer, answer, has_conflict.
</eval_rules>
"""

EVAL_SYSTEM_PROMPT_TEMPLATE = """
{{ eval_agent_profile }}
{{ eval_rules }}
"""

EVAL_AGENT_MESSAGE_TEMPLATE = """Task: {{ task }}
{% if previous %}

Prior research rounds:
{{ previous }}
{% endif %}

Current round — search reports:
{{ content }}

Based on all search reports above:
1. Detect any conflicts or contradictions between sources.
2. Write reasoning: key conclusions + critical evidence. If found_answer=true, explain why the answer is correct. If conflict, describe the exact disagreement. **If the task is multiple-choice, explicitly analyze every option (why correct or incorrect) before committing to an answer.**
3. Determine if the task is fully answered (found_answer: true/false). Trust consistent evidence from one or more credible sources — do NOT require unanimous confirmation.
4. If found_answer is true, extract the final answer exactly as needed. Scope strictly to what the task asks — do NOT include auxiliary examples or context provided only as hints.
5. Set has_conflict=true only for meaningful contradictions on task-relevant points.
6. If found_answer is false, ensure reasoning precisely describes the conflict or gap so the next round's query can target it."""

EVAL_SYSTEM_PROMPT = {
    "name": "deep_researcher_v2_eval_system_prompt",
    "type": "system_prompt",
    "description": "System prompt for per-round research completeness evaluation",
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
            "description": "Rules for evaluating research completeness and detecting conflicts.",
            "require_grad": True,
            "template": None,
            "variables": EVAL_RULES,
        },
    },
}

EVAL_AGENT_MESSAGE_PROMPT = {
    "name": "deep_researcher_v2_eval_agent_message_prompt",
    "type": "agent_message_prompt",
    "description": "Per-round dynamic context for research completeness evaluation",
    "require_grad": False,
    "template": EVAL_AGENT_MESSAGE_TEMPLATE,
    "variables": {
        "task": {
            "name": "task",
            "type": "agent_message_prompt",
            "description": "The research task.",
            "require_grad": False,
            "template": None,
            "variables": None,
        },
        "previous": {
            "name": "previous",
            "type": "agent_message_prompt",
            "description": "Execution log of prior research rounds (reasoning + answer per round); empty string on round 1.",
            "require_grad": False,
            "template": None,
            "variables": None,
        },
        "content": {
            "name": "content",
            "type": "agent_message_prompt",
            "description": "All search reports from this round, each prefixed with **[source-name]** (e.g., **[api_search]**, **[model-name]**).",
            "require_grad": False,
            "template": None,
            "variables": None,
        },
    },
}


@PROMPT.register_module(force=True)
class DeepResearcherV2EvalSystemPrompt(Prompt):
    """System prompt for per-round research completeness evaluation."""
    model_config = ConfigDict(arbitrary_types_allowed=True, extra="allow")

    type: str = Field(default="system_prompt")
    name: str = Field(default="deep_researcher_v2_eval")
    description: str = Field(default="System prompt for per-round research completeness evaluation")
    require_grad: bool = Field(default=True)
    metadata: Dict[str, Any] = Field(default_factory=dict)

    prompt_config: Dict[str, Any] = Field(default=EVAL_SYSTEM_PROMPT)


@PROMPT.register_module(force=True)
class DeepResearcherV2EvalAgentMessagePrompt(Prompt):
    """Agent message prompt for per-round research completeness evaluation."""
    model_config = ConfigDict(arbitrary_types_allowed=True, extra="allow")

    type: str = Field(default="agent_message_prompt")
    name: str = Field(default="deep_researcher_v2_eval")
    description: str = Field(default="Per-round dynamic context for research completeness evaluation")
    require_grad: bool = Field(default=False)
    metadata: Dict[str, Any] = Field(default_factory=dict)

    prompt_config: Dict[str, Any] = Field(default=EVAL_AGENT_MESSAGE_PROMPT)
