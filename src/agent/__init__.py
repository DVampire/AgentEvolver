"""Agents module for multi-agent system."""

from .tool_calling import ToolCallingAgent
from .sop import SopAgent
from .planning import PlanningAgent
from .deep_researcher import DeepResearcherAgent
from .deep_researcher_light import DeepResearcherLightAgent
from .deep_researcher_v2 import DeepResearcherV2Agent
from .deep_researcher_v3 import DeepResearcherV3Agent
from .deep_analyzer import DeepAnalyzerAgent
from .deep_analyzer_v2 import DeepAnalyzerV2Agent
from .deep_analyzer_v3 import DeepAnalyzerV3Agent
from .deep_analyzer_light import DeepAnalyzerLightAgent
from .claude_code_agent import ClaudeCodeAgent
from .opencode_agent import OpencodeAgent
from .wiki_searcher import WikiSearcherAgent
from .server import agent_manager


__all__ = [
    "ToolCallingAgent",
    "SopAgent",
    "PlanningAgent",
    "DeepResearcherAgent",
    "DeepResearcherLightAgent",
    "DeepResearcherV2Agent",
    "DeepResearcherV3Agent",
    "DeepAnalyzerAgent",
    "DeepAnalyzerV2Agent",
    "DeepAnalyzerV3Agent",
    "DeepAnalyzerLightAgent",
    "ClaudeCodeAgent",
    "OpencodeAgent",
    "WikiSearcherAgent",
    "agent_manager",
]
