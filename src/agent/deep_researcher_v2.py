"""Deep Researcher V2 Agent — multi-round research with API search and LLM search.

Three-module pipeline per round:
1. Query  — LLM generates an optimized search query (broad on round 1; targeted on conflicts)
2. Search — Concurrent searches, each producing a labeled report:
             - If image present: multimodal_search only (GoogleLensSearch)
               → fetch/summarize → LLM synthesis → one report  (**[google_lens_search]**)
             - If no image: api_search + llm_search × N run concurrently, results merged
               → api_search uses FirecrawlSearch (**[firecrawl_search]**)
               → LLM search × N: one web-search-capable LLM call per model (**[model-name]**)
3. Eval   — Receives all labeled reports, detects conflicts between sources,
             evaluates completeness → loop until found_answer or max_rounds
"""

from __future__ import annotations

import asyncio
import os
import re
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field

from src.agent.types import Agent, AgentExtra, AgentResponse
from src.logger import logger
from src.model import model_manager
from src.message.types import HumanMessage
from src.prompt import prompt_manager
from src.registry import AGENT
from src.tool.default_tools.search.firecrawl_search import FirecrawlSearch
from src.tool.default_tools.search.google_lens_search import GoogleLensSearch
from src.utils import assemble_project_path, make_file_url, fetch_url


# ---------------------------------------------------------------------------
# Structured output schemas
# ---------------------------------------------------------------------------

class ResearchSummary(BaseModel):
    """Evaluation result for a research round."""
    reasoning: str = Field(description="Key conclusions and critical logic. Capture what the sources found and why the answer is correct or the conflict/gap that remains — not a full recap, but enough to understand the conclusion. For multiple-choice tasks, must include explicit analysis of every option (why correct or incorrect) before stating the answer.")
    found_answer: bool = Field(description="Whether a complete answer was found")
    answer: Optional[str] = Field(default=None, description="The final answer if found_answer is True; concise and directly usable")
    has_conflict: bool = Field(default=False, description="Whether sources contradicted each other on task-relevant points")


# ---------------------------------------------------------------------------
# Per-session state
# ---------------------------------------------------------------------------

class ResearchRound(BaseModel):
    """Record of one research round."""
    number: int
    query: str
    reasoning: str
    found_answer: bool
    answer: Optional[str] = None
    has_conflict: bool = False
    timestamp: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    )


class ResearchSession:
    """Tracks the state of one research invocation."""

    def __init__(self, task: str) -> None:
        self.task = task
        self.rounds: List[ResearchRound] = []

    def add_round(self, round_: ResearchRound) -> None:
        self.rounds.append(round_)

    def execution_log_text(self) -> str:
        """Plain-text log of all completed rounds (for query generation and eval context)."""
        if not self.rounds:
            return ""
        lines: List[str] = []
        for r in self.rounds:
            status = "CONFLICT — not resolved" if r.has_conflict else ("Answered" if r.found_answer else "Incomplete")
            lines.append(f"=== Round {r.number} [{status}] — {r.timestamp} ===")
            lines.append(f"Query: {r.query}")
            lines.append(f"Reasoning: {r.reasoning}")
            if r.answer:
                lines.append(f"Answer: {r.answer}")
            lines.append("")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# SearchLogger — saves each round's search results as markdown files
# ---------------------------------------------------------------------------

class SearchLogger:
    """Saves api_search and llm_search results per round as markdown files.

    File naming:
      round_{N}_api_search.md
      round_{N}_llm_{model_slug}.md
    """

    def __init__(self, base_dir: str, session_id: str):
        self.log_dir = os.path.join(base_dir, "search", session_id)
        os.makedirs(self.log_dir, exist_ok=True)

    @staticmethod
    def _model_slug(model: str) -> str:
        return re.sub(r'[^\w\-.]', '_', model)[:50]

    def _write(self, path: str, content: str) -> None:
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)

    def save_api_search(self, round_num: int, query: str, content: str) -> None:
        path = os.path.join(self.log_dir, f"round_{round_num}_api_search.md")
        self._write(path, f"# Round {round_num} — API Search\n\n**Query:** `{query}`\n\n---\n\n{content}\n")

    def save_llm_search(self, round_num: int, model: str, content: str) -> None:
        slug = self._model_slug(model)
        path = os.path.join(self.log_dir, f"round_{round_num}_llm_{slug}.md")
        self._write(path, f"# Round {round_num} — LLM Search\n\n**Model:** `{model}`\n\n---\n\n{content}\n")


# ---------------------------------------------------------------------------
# DeepResearcherV2Agent
# ---------------------------------------------------------------------------

_DESCRIPTION = (
    "Multi-round web research agent supporting both pure-text tasks and multimodal "
    "image+text tasks (task as text, files as local image paths or image URLs). "
    "Generates optimized search queries, retrieves and synthesizes web content, "
    "and evaluates completeness across multiple rounds."
)

@AGENT.register_module(force=True)
class DeepResearcherV2Agent(Agent):
    """Multi-round research agent with concurrent API search and LLM search.

    Three-module pipeline per round:
    1. Query  — generates optimized search query
    2. Search — if image: multimodal_search only (Google Lens);
                if no image: concurrent api_search (Firecrawl) + llm_search × N, results merged
    3. Eval   — detects conflicts, evaluates completeness → loop until found_answer or max_rounds
    """

    model_config = ConfigDict(arbitrary_types_allowed=True, extra="allow")

    name: str = Field(default="deep_researcher_v2_agent")
    description: str = Field(default=_DESCRIPTION)
    metadata: Dict[str, Any] = Field(default_factory=dict)
    require_grad: bool = Field(default=False)

    def __init__(
        self,
        workdir: str,
        name: Optional[str] = None,
        description: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        model_name: Optional[str] = None,
        prompt_name: Optional[str] = None,
        memory_name: Optional[str] = None,
        require_grad: bool = False,
        # Research config
        max_rounds: int = 3,
        num_results: int = 10,
        # Summary model for Serper page summarization and synthesis
        summary_model_name: Optional[str] = None,
        # LLM search models (run in parallel alongside api_search)
        llm_search_models: Optional[List[str]] = None,
        # Page fetch timeout
        fetch_timeout: float = 20.0,
        # Search logging
        enable_search_log: bool = True,
        **kwargs,
    ):
        super().__init__(
            workdir=workdir,
            name=name,
            description=description,
            metadata=metadata,
            model_name=model_name or "openrouter/gemini-3.1-pro-preview",
            prompt_name=prompt_name,
            memory_name=memory_name,
            require_grad=require_grad,
            **kwargs,
        )
        self.max_rounds = max_rounds
        self.num_results = num_results
        self.summary_model_name = summary_model_name or "openrouter/gemini-3-flash-preview"
        self.llm_search_models: List[str] = llm_search_models or []
        self.fetch_timeout = fetch_timeout
        self.enable_search_log = enable_search_log

        self._firecrawl_search = FirecrawlSearch()
        self._google_lens_search = GoogleLensSearch()

    # ------------------------------------------------------------------
    # Main call — multi-round research loop
    # ------------------------------------------------------------------

    async def __call__(
        self,
        task: str,
        files: Optional[List[str]] = None,
        **kwargs,
    ) -> AgentResponse:
        filter_year: int = kwargs.get(
            "filter_year", __import__("datetime").datetime.now().year
        )
        start_time = time.time()

        image: Optional[str] = None
        if files:
            f = files[0]
            if f.lower().endswith((".jpg", ".jpeg", ".png")):
                image = f

        slog: Optional[SearchLogger] = None
        if self.enable_search_log:
            session_id = kwargs.get(
                "session_id",
                datetime.now().strftime("%Y%m%d_%H%M%S"),
            )
            slog = SearchLogger(base_dir=self.workdir, session_id=session_id)

        logger.info(f"| 🔍 DeepResearcherV2Agent starting: {task}")
        session = ResearchSession(task=task)

        try:
            for round_num in range(1, self.max_rounds + 1):
                logger.info(f"| 📋 Round {round_num}/{self.max_rounds}")

                # Module 1: Query
                query = await self._query(task, image, session, filter_year)
                logger.info(f"| ✅ Query for round {round_num}: {query}")

                # Module 2: Search
                # If image present: multimodal_search only (Google Lens)
                # If no image: api_search (Firecrawl) + llm_search × N, concurrent
                reports: List[str] = []

                if image:
                    mm_result = await self._multimodal_search(query, self.num_results, filter_year, round_num, slog, image)
                    if isinstance(mm_result, Exception):
                        logger.warning(f"| ❌ Multimodal search failed: {mm_result}")
                    else:
                        mm_text, _, _, mm_label = mm_result
                        if mm_text:
                            reports.append(f"**[{mm_label}]**\n{mm_text}")
                else:
                    api_coro = self._api_search(query, self.num_results, filter_year, round_num, slog)
                    llm_coros = [
                        self._llm_search(m, task, query)
                        for m in self.llm_search_models
                    ]
                    gathered = await asyncio.gather(api_coro, *llm_coros, return_exceptions=True)

                    api_result = gathered[0]
                    if isinstance(api_result, Exception):
                        logger.warning(f"| ❌ API search failed: {api_result}")
                    else:
                        api_text, _, _, api_label = api_result
                        if api_text:
                            reports.append(f"**[{api_label}]**\n{api_text}")

                    for m, r in zip(self.llm_search_models, gathered[1:]):
                        if isinstance(r, Exception):
                            logger.warning(f"| ❌ LLM search {m} failed: {r}")
                        elif r:
                            logger.info(f"|   ✅ LLM search {m} done ({len(r)} chars)")
                            if slog:
                                slog.save_llm_search(round_num, m, r)
                            reports.append(f"**[{m}]**\n{r}")

                if not reports:
                    logger.warning(f"| ❌ No results from any source in round {round_num}")
                    session.add_round(ResearchRound(
                        number=round_num,
                        query=query,
                        reasoning="No results from any source.",
                        found_answer=False,
                    ))
                    continue

                combined_content = "\n\n---\n\n".join(reports)

                # Module 3: Eval
                eval_result = await self._evaluate(task, combined_content, session)
                logger.info(
                    f"| {'✅' if eval_result.found_answer else '❌'} "
                    f"Round {round_num} — found_answer={eval_result.found_answer} "
                    f"has_conflict={eval_result.has_conflict}"
                )
                if eval_result.reasoning:
                    logger.info(f"|   Reasoning: {eval_result.reasoning[:200]}")

                session.add_round(ResearchRound(
                    number=round_num,
                    query=query,
                    reasoning=eval_result.reasoning or combined_content[:500],
                    found_answer=eval_result.found_answer,
                    answer=eval_result.answer,
                    has_conflict=eval_result.has_conflict,
                ))

                if eval_result.found_answer:
                    logger.info(f"| ✅ Answer found in round {round_num}")
                    break

            # ------------------------------------------------------------------
            # Build final response
            # ------------------------------------------------------------------
            answer_found = any(r.found_answer for r in session.rounds)
            final_round = session.rounds[-1] if session.rounds else None

            if answer_found and final_round:
                parts = []
                if final_round.reasoning:
                    parts.append("**Reasoning:**\n" + final_round.reasoning)
                if final_round.answer:
                    parts.append(f"**Answer:** {final_round.answer}")
                summary = "\n\n".join(parts) if parts else final_round.answer or final_round.reasoning
            elif final_round and final_round.has_conflict:
                summary = (
                    f"Unresolved conflict after {len(session.rounds)} round(s).\n\n"
                    f"**Reasoning:**\n{final_round.reasoning}"
                )
            elif final_round:
                summary = (
                    f"Insufficient information after {len(session.rounds)} round(s).\n\n"
                    f"**Reasoning:**\n{final_round.reasoning}"
                )
            else:
                summary = "No research completed."

            logger.info(
                f"| ✅ DeepResearcherV2Agent finished after "
                f"{len(session.rounds)} round(s), answer_found={answer_found}"
            )

            return AgentResponse(
                success=answer_found,
                message=summary,
                extra=AgentExtra(
                    data={
                        "task": task,
                        "rounds": len(session.rounds),
                        "answer_found": answer_found,
                        "history": [
                            {
                                "round_number": r.number,
                                "query": r.query,
                                "reasoning": r.reasoning,
                                "found_answer": r.found_answer,
                                "has_conflict": r.has_conflict,
                                "answer": r.answer,
                            }
                            for r in session.rounds
                        ],
                    }
                ),
            )

        except Exception as exc:
            logger.error(f"| ❌ DeepResearcherV2Agent error: {exc}", exc_info=True)
            return AgentResponse(
                success=False,
                message=f"Error during research: {exc}",
            )

    # ------------------------------------------------------------------
    # Module 1: Query — generate optimized search query
    # ------------------------------------------------------------------

    async def _query(
        self,
        task: str,
        image: Optional[str],
        session: ResearchSession,
        filter_year: Optional[int] = None,
    ) -> str:
        """Generate an optimized search query for this round."""
        messages = await prompt_manager.get_messages(
            prompt_name="deep_researcher_v2_query",
            agent_modules={
                "task": task,
                "image": image or "",
                "filter_year": str(filter_year) if filter_year else "",
                "previous_context": session.execution_log_text(),
            },
        )

        if image and image.lower().endswith((".jpg", ".jpeg", ".png")):
            from src.message import ContentPartText, ContentPartImage, ImageURL
            last = messages[-1]
            text_content = last.content if isinstance(last.content, str) else str(last.content)
            messages[-1] = HumanMessage(content=[
                ContentPartText(text=text_content),
                ContentPartImage(image_url=ImageURL(
                    url=make_file_url(file_path=assemble_project_path(image)),
                    detail="high",
                )),
            ])

        response = await model_manager(model=self.model_name, messages=messages, caller="v2/query")
        return response.message.strip() if response and response.message else ""

    # ------------------------------------------------------------------
    # Module 2: Search — multimodal_search, api_search, llm_search
    # ------------------------------------------------------------------

    async def _multimodal_search(
        self,
        query: str,
        num_results: int,
        filter_year: Optional[int],
        round_num: int,
        slog: Optional[SearchLogger],
        image: str,
    ) -> tuple:
        """Search using GoogleLensSearch for image+text tasks.

        Returns (round_text, [], [], label).
        """
        label = "google_lens_search"
        try:
            resp = await self._google_lens_search(
                query=query,
                image=image,
                num_results=num_results,
                filter_year=filter_year,
            )
            search_items = (
                resp.extra.data.get("search_items", [])
                if resp.success and resp.extra and resp.extra.data
                else []
            )
            logger.info(f"| ✅ {self._google_lens_search.name} returned {len(search_items)} results")
        except Exception as e:
            logger.error(f"| ❌ {self._google_lens_search.name} failed: {e}")
            search_items = []

        if not search_items:
            return "", [], [], label

        for i, item in enumerate(search_items, 1):
            title = getattr(item, "title", "") or item.get("title", "")
            url = getattr(item, "url", "") or item.get("url", "")
            logger.info(f"|   [{i}] {title} — {url}")

        all_results, _ = await self._fetch_and_summarize(search_items, query, round_num)
        successful = [r for r in all_results if r.get("summary")]
        logger.info(f"| ✅ Fetched & summarized {len(successful)}/{len(all_results)} pages")

        for r in all_results:
            status = "✅" if r.get("summary") else "❌"
            logger.info(f"|   {status} {r.get('title', '?')} — {r.get('url', '?')}")

        synthesized = await self._synthesize(query, all_results)
        round_text = self._build_round_summary(synthesized, all_results)

        if slog:
            slog.save_api_search(round_num, query, round_text)

        return round_text, [], [], label

    async def _api_search(
        self,
        query: str,
        num_results: int,
        filter_year: Optional[int],
        round_num: int,
        slog: Optional[SearchLogger],
    ) -> tuple:
        """Search using FirecrawlSearch for text-only tasks.

        Returns (round_text, [], [], label).
        """
        label = "firecrawl_search"
        try:
            resp = await self._firecrawl_search(
                query=query,
                num_results=num_results,
                filter_year=filter_year,
            )
            search_items = (
                resp.extra.data.get("search_items", [])
                if resp.success and resp.extra and resp.extra.data
                else []
            )
            logger.info(f"| ✅ {self._firecrawl_search.name} returned {len(search_items)} results")
        except Exception as e:
            logger.error(f"| ❌ {self._firecrawl_search.name} failed: {e}")
            search_items = []

        if not search_items:
            return "", [], [], label

        for i, item in enumerate(search_items, 1):
            title = getattr(item, "title", "") or item.get("title", "")
            url = getattr(item, "url", "") or item.get("url", "")
            logger.info(f"|   [{i}] {title} — {url}")

        all_results, _ = await self._fetch_and_summarize(search_items, query, round_num)
        successful = [r for r in all_results if r.get("summary")]
        logger.info(f"| ✅ Fetched & summarized {len(successful)}/{len(all_results)} pages")

        for r in all_results:
            status = "✅" if r.get("summary") else "❌"
            logger.info(f"|   {status} {r.get('title', '?')} — {r.get('url', '?')}")

        synthesized = await self._synthesize(query, all_results)
        round_text = self._build_round_summary(synthesized, all_results)

        if slog:
            slog.save_api_search(round_num, query, round_text)

        return round_text, [], [], label

    async def _synthesize(self, query: str, results: List[Dict[str, Any]]) -> str:
        """Use LLM to synthesize individual page summaries into a coherent research summary."""
        if not results:
            return ""
        sources = "\n\n".join(
            f"[{i}] {r.get('title', 'Untitled')} ({r.get('url', '')})\n{r.get('summary', '')}"
            for i, r in enumerate(results, 1)
            if r.get("summary")
        )
        if not sources:
            return ""
        try:
            messages = await prompt_manager.get_messages(
                prompt_name="deep_researcher_v2_synthesis",
                agent_modules={
                    "query": query,
                    "num_sources": str(len(results)),
                    "sources": sources,
                },
            )
            resp = await model_manager(
                model=self.summary_model_name,
                messages=messages,
                caller="v2/synthesis",
            )
            if resp and resp.message and resp.message.strip():
                return resp.message.strip()
        except Exception as e:
            logger.warning(f"| ❌ Synthesis LLM failed: {e}")
        return "\n\n".join(r.get("summary", "") for r in results if r.get("summary"))

    async def _llm_search(self, model: str, task: str, query: str) -> str:
        """Search using an LLM with web search capability. Returns the raw report text."""
        messages = await prompt_manager.get_messages(
            prompt_name="deep_researcher_v2_llm_search",
            agent_modules={
                "task": task,
                "query": query,
            },
        )
        logger.info(f"| 🔍 LLM search using {model}")
        resp = await model_manager(model=model, messages=messages, caller="v2/llm_search")
        if not resp or not resp.success:
            raise ValueError(f"Model call failed: {resp.message if resp else 'no response'}")
        text = resp.message.strip() if resp.message else ""
        if not text:
            raise ValueError("Empty response")
        logger.info(f"| ✅ LLM search {model} done ({len(text)} chars)")
        return text

    # ------------------------------------------------------------------
    # Parallel fetch (Jina Reader) + LLM page_summary (internal to _api_search)
    # ------------------------------------------------------------------

    async def _fetch_and_summarize(
        self,
        items: list,
        query: str,
        round_num: int = 0,
    ) -> tuple:
        """For each search result: fetch page via Jina Reader, then LLM page_summary."""
        if not items:
            return [], []

        async def _process_one(item, index: int) -> tuple:
            url = getattr(item, "url", "") or item.get("url", "")
            title = getattr(item, "title", "") or item.get("title", "")
            snippet = getattr(item, "description", "") or item.get("description", "")

            log_entry: Dict[str, Any] = {
                "url": url, "title": title, "snippet": snippet,
                "fetch_success": False, "fetch_chars": 0, "fetch_duration": 0.0,
            }

            # Use pre-fetched content if available, otherwise fetch via Jina
            fetch_start = time.time()
            prefetched = getattr(item, "content", None) or (item.get("content") if isinstance(item, dict) else None)
            if prefetched:
                content = prefetched
                logger.info(f"|   📦 Using pre-fetched content for {url} ({len(content)} chars)")
            else:
                content = None
                try:
                    resp = await asyncio.wait_for(
                        fetch_url(url=url, timeout=self.fetch_timeout),
                        timeout=self.fetch_timeout,
                    )
                    if resp and resp.get("markdown"):
                        content = resp["markdown"]
                        logger.info(f"|   📥 Fetched {url} ({len(content)} chars)")
                    else:
                        logger.warning(f"|   ❌ Fetch failed for {url}: {resp.get('message', 'no response')}")
                except asyncio.TimeoutError:
                    logger.warning(f"|   ❌ Fetch timeout ({self.fetch_timeout}s) for {url}")
                except Exception as e:
                    logger.warning(f"|   ❌ Fetch error for {url}: {e}")

            log_entry["fetch_success"] = content is not None
            log_entry["fetch_chars"] = len(content) if content else 0
            log_entry["fetch_duration"] = round(time.time() - fetch_start, 1)

            summary = snippet
            if content:
                try:
                    page_messages = await prompt_manager.get_messages(
                        prompt_name="deep_researcher_v2_page_summary",
                        agent_modules={"query": query, "title": title, "content": content},
                    )
                    llm_resp = await model_manager(
                        model=self.summary_model_name,
                        messages=page_messages,
                        caller="v2/page_summary",
                    )
                    if llm_resp and llm_resp.message and llm_resp.message.strip():
                        summary = llm_resp.message.strip()
                        logger.info(f"|   📝 Summarized {url} ({len(summary)} chars)")
                    else:
                        logger.warning(f"|   ❌ LLM returned empty summary for {url}")
                except Exception as e:
                    logger.warning(f"|   ❌ Summary LLM failed for {url}: {e}")

            return {"title": title, "url": url, "summary": summary}, log_entry

        tasks = [_process_one(item, i + 1) for i, item in enumerate(items)]
        raw_results = await asyncio.gather(*tasks, return_exceptions=True)

        processed, summaries_log = [], []
        for r in raw_results:
            if isinstance(r, Exception):
                logger.error(f"|   ❌ Process exception: {r}")
            else:
                result, log_entry = r
                processed.append(result)
                summaries_log.append(log_entry)

        return processed, summaries_log

    def _build_round_summary(self, synthesized: str, results: List[Dict[str, Any]]) -> str:
        """Build round summary: synthesized content + reference list."""
        parts = []
        if synthesized:
            parts.append(synthesized)
            parts.append("")
        parts.append("## References")
        for i, r in enumerate(results, 1):
            title = r.get("title", "Untitled")
            url = r.get("url", "")
            parts.append(f"{i}. [{title}]({url})")
        return "\n".join(parts)

    # ------------------------------------------------------------------
    # Module 3: Eval — evaluate completeness and detect conflicts
    # ------------------------------------------------------------------

    async def _evaluate(self, task: str, content: str, session: ResearchSession) -> ResearchSummary:
        """Evaluate whether the round content answers the task. Detects cross-source conflicts."""
        messages = await prompt_manager.get_messages(
            prompt_name="deep_researcher_v2_eval",
            agent_modules={
                "task": task,
                "previous": session.execution_log_text(),
                "content": content,
            },
        )
        try:
            response = await model_manager(
                model=self.model_name,
                messages=messages,
                response_format=ResearchSummary,
                caller="v2/eval",
            )
            if (
                response
                and response.extra
                and hasattr(response.extra, "parsed_model")
                and response.extra.parsed_model
            ):
                return response.extra.parsed_model
            raw = response.message if response else ""
            return ResearchSummary(reasoning=raw.strip() if raw else "", found_answer=False)
        except Exception as exc:
            logger.warning(f"| Evaluation failed: {exc}")
            return ResearchSummary(reasoning="", found_answer=False)
