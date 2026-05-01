"""Deep Analyzer V2 Agent — multi-round analysis with concurrent LLM calls.

Pipeline per round:
1. Plan  — generate analysis guidance (angles on round 1; conflict-resolution on round N)
2. Analyze — all files × all models in parallel, guided by plan output
   - image / pdf / audio: URL → download first; local → use directly
   - video: YouTube URL → use directly; other URL → download; local → use directly
   - text: URL → fetch content; local → read file
   - no files: direct multi-model reasoning
3. Eval  — synthesize multi-model results, detect conflicts, extract answer or loop

Prompts (src/prompt/template/deep_analyzer_v2.py):
  plan    — per-round guidance generation
  analyze — per-model analysis (media or direct), receives guidance from plan
  eval    — synthesize, detect conflicts, decide found_answer
"""

from __future__ import annotations

import asyncio
import os
import re
import urllib.request
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field

from src.agent.types import Agent, AgentExtra, AgentResponse
from src.logger import logger
from src.model import model_manager
from src.message import (
    HumanMessage,
    ContentPartText,
    ContentPartImage, ImageURL,
    ContentPartAudio, AudioURL,
    ContentPartVideo, VideoURL,
    ContentPartPdf, PdfURL,
)
from src.registry import AGENT
from src.prompt import prompt_manager
from src.utils import fetch_url, make_file_url


# ---------------------------------------------------------------------------
# Structured-output schemas
# ---------------------------------------------------------------------------

class FileTypeInfo(BaseModel):
    file: str = Field(description="Exact file path or URL as provided")
    file_type: str = Field(description="One of: text, pdf, image, audio, video")


class AnalysisResult(BaseModel):
    reasoning: str = Field(description="Key reasoning steps and conclusions. Capture the critical logic that led to the answer or identified the conflict — not a full derivation, but enough to understand why the conclusion was reached. For multiple-choice tasks, must include explicit analysis of every option (why correct or incorrect) before stating the answer.")
    found_answer: bool = Field(description="Whether the task has been fully answered with no unresolved conflicts")
    answer: Optional[str] = Field(default=None, description="The final answer if found_answer is True; concise and directly usable")
    has_conflict: bool = Field(default=False, description="Whether models disagreed on key points this round")


# ---------------------------------------------------------------------------
# Per-session state
# ---------------------------------------------------------------------------

class AnalysisRound(BaseModel):
    number: int
    reasoning: str
    found_answer: bool
    answer: Optional[str] = None
    has_conflict: bool = False
    timestamp: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    )


class AnalysisSession:
    def __init__(self, task: str) -> None:
        self.task = task
        self.rounds: List[AnalysisRound] = []

    def add_round(self, round_: AnalysisRound) -> None:
        self.rounds.append(round_)

    def execution_log_text(self) -> str:
        """Build context for the next round, highlighting unresolved conflicts."""
        if not self.rounds:
            return ""
        lines: List[str] = []
        for r in self.rounds:
            status = "CONFLICT — not resolved" if r.has_conflict else ("Answered" if r.found_answer else "Incomplete")
            lines.append(f"=== Round {r.number} [{status}] ===")
            lines.append(r.reasoning)
            if r.answer:
                lines.append(f"Answer: {r.answer}")
            lines.append("")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# File-type constants
# ---------------------------------------------------------------------------

_YOUTUBE_PATTERNS = [
    r"youtube\.com/watch\?v=",
    r"youtu\.be/",
    r"youtube\.com/embed/",
    r"youtube\.com/v/",
]
_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp", ".tiff", ".svg"}
_PDF_EXTS = {".pdf"}
_AUDIO_EXTS = {".mp3", ".wav", ".ogg", ".flac", ".aac", ".m4a", ".m4b", ".m4p"}
_VIDEO_EXTS = {".mp4", ".avi", ".mov", ".wmv", ".flv", ".webm", ".m4v"}

# ---------------------------------------------------------------------------
# DeepAnalyzerV2Agent
# ---------------------------------------------------------------------------

_DESCRIPTION = (
    "Multi-round analysis agent supporting text, image, PDF, audio, and video. "
    "Runs multiple LLMs in parallel per round; eval detects conflicts between models "
    "and loops until the task is answered or max_rounds is reached."
)

@AGENT.register_module(force=True)
class DeepAnalyzerV2Agent(Agent):
    """Multi-round file analysis agent with concurrent LLM calls.

    Each round: analyze (all models in parallel) → eval (synthesize, detect conflicts, assess completeness).
    Loops up to max_rounds. On conflict, eval description is passed to the next round via previous context.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True, extra="allow")

    name: str = Field(default="deep_analyzer_v2_agent")
    description: str = Field(
        default=_DESCRIPTION
    )
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
        max_rounds: int = 3,
        max_file_size: int = 10 * 1024 * 1024,
        summary_model_name: Optional[str] = None,
        general_analyze_models: Optional[List[str]] = None,
        llm_analyze_models: Optional[List[str]] = None,
        advanced_analyze_models: Optional[List[str]] = None,
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
        self.max_file_size = max_file_size

        # LLM Models
        self.summary_model_name = summary_model_name # small model for summarizing long content
        self.general_analyze_models = general_analyze_models # can analyze audio, video, image, pdf, etc.
        self.llm_analyze_models = llm_analyze_models # can analyze complex text with image
        self.advanced_analyze_models = advanced_analyze_models # expensive but powerful model for deep analysis

    # ------------------------------------------------------------------
    # Main call
    # ------------------------------------------------------------------

    async def __call__(
        self,
        task: str,
        files: Optional[List[str]] = None,
        **kwargs,
    ) -> AgentResponse:
        logger.info(f"| 🔍 DeepAnalyzerV2Agent starting: {task}")
        if files:
            logger.info(f"| 📂 Attached files: {files}")

        session = AnalysisSession(task=task)

        try:
            # Validate and classify files
            valid_files: List[str] = []
            if files:
                for f in files:
                    if self._validate_file(f):
                        valid_files.append(f)
                    else:
                        logger.warning(f"| Skipping invalid file: {f}")

            classifications: List[FileTypeInfo] = []
            if valid_files:
                classifications = self._classify_files(valid_files)
                for fi in classifications:
                    logger.info(f"| 📋 {fi.file}: {fi.file_type}")

            # Determine whether any file requires general-only mode (pdf/audio/video)
            has_heavy_media = any(fi.file_type in {"pdf", "audio", "video"} for fi in classifications)

            for round_num in range(1, self.max_rounds + 1):
                logger.info(f"| 📋 Round {round_num}/{self.max_rounds}")

                # Build active model list for this round
                if has_heavy_media:
                    # pdf/audio/video: only general_analyze_models can handle these
                    active_models = list(self.general_analyze_models or [])
                else:
                    # text / image: general + llm always; add advanced from round 2+ onward
                    active_models = list(self.general_analyze_models or []) + list(self.llm_analyze_models or [])
                    if round_num > 1 and self.advanced_analyze_models:
                        active_models += list(self.advanced_analyze_models)

                logger.info(f"| 🤖 Active models ({len(active_models)}): {active_models}")

                # Step 1: Generate analysis guidance for this round
                guidance = await self._plan(task, session, classifications)

                # Step 2: Analyze all files concurrently (parallel models per file)
                if classifications:
                    file_parts = await asyncio.gather(
                        *[self._analyze(task, session, fi, guidance, active_models) for fi in classifications],
                        return_exceptions=True,
                    )
                    raw_parts: List[str] = []
                    for fi, result in zip(classifications, file_parts):
                        if isinstance(result, Exception):
                            logger.warning(f"| ❌ File analysis failed ({fi.file}): {result}")
                        elif result:
                            raw_parts.append(f"### {fi.file_type} — {fi.file}\n\n{result}")
                    combined = "\n\n---\n\n".join(raw_parts) if raw_parts else ""
                else:
                    combined = await self._analyze(task, session, guidance=guidance, active_models=active_models)

                if not combined:
                    logger.warning(f"| ❌ No analysis output in round {round_num}")
                    session.add_round(AnalysisRound(
                        number=round_num,
                        reasoning="No analysis output.",
                        found_answer=False,
                        answer=None,
                    ))
                    continue

                # Step 2: Evaluate completeness
                eval_result = await self._evaluate(task, combined, session)
                logger.info(
                    f"| {'✅' if eval_result.found_answer else '❌'} "
                    f"Round {round_num} — found_answer={eval_result.found_answer} "
                    f"has_conflict={eval_result.has_conflict}"
                )
                if eval_result.reasoning:
                    logger.info(f"|   Reasoning: {eval_result.reasoning[:200]}")

                session.add_round(AnalysisRound(
                    number=round_num,
                    reasoning=eval_result.reasoning,
                    found_answer=eval_result.found_answer,
                    answer=eval_result.answer,
                    has_conflict=eval_result.has_conflict,
                ))

                if eval_result.found_answer:
                    logger.info(f"| ✅ Answer found in round {round_num}")
                    break

            # Build final response
            answer_found = any(r.found_answer for r in session.rounds)
            final_round = next((r for r in reversed(session.rounds) if r.found_answer), session.rounds[-1] if session.rounds else None)
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
                summary = "No analysis completed."

            logger.info(
                f"| ✅ DeepAnalyzerV2Agent finished after "
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
                        "answer": final_round.answer if final_round else None,
                        "history": [
                            {
                                "round_number": r.number,
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
            logger.error(f"| ❌ DeepAnalyzerV2Agent error: {exc}", exc_info=True)
            return AgentResponse(success=False, message=f"Error during analysis: {exc}")

    # ------------------------------------------------------------------
    # Analysis: run llm_analyze_models in parallel, then merge
    # ------------------------------------------------------------------

    async def _analyze(
        self,
        task: str,
        session: AnalysisSession,
        fi: Optional[FileTypeInfo] = None,
        guidance: str = "",
        active_models: Optional[List[str]] = None,
    ) -> str:
        """Run all models in parallel on a task, optionally with a file attached.

        If fi is None, performs direct multi-model reasoning (no file).
        If fi is provided, builds a content part and attaches it to the prompt.
        guidance is injected after the task to steer analysis angles each round.
        """
        messages = await prompt_manager.get_messages(
            prompt_name="deep_analyzer_v2_analyze",
            agent_modules={
                "task": task,
                "file_type": fi.file_type if fi else "",
                "guidance": guidance,
                "previous": session.execution_log_text(),
            },
        )

        if fi is not None:
            content_part = await self._build_content_part(fi)
            if content_part is None:
                return ""
            last = messages[-1]
            text_content = last.content if isinstance(last.content, str) else str(last.content)
            messages = messages[:-1] + [HumanMessage(content=[
                ContentPartText(text=text_content),
                content_part,
            ])]

        caller = f"v2/{fi.file_type}" if fi else "v2/direct"
        label = fi.file if fi else ""
        return await self._run_models_parallel(messages, active_models=active_models, caller=caller, label=label)

    async def _build_content_part(self, fi: FileTypeInfo) -> Optional[Any]:
        """Build the content part for any file type.

        Download rules:
          image / pdf / audio : URL → download first; local → use directly
          video               : YouTube URL → use directly; other URL → download; local → use directly
          text                : URL → fetch content via fetch_url; local → read file
        """
        file = fi.file
        file_type = fi.file_type

        if file_type == "image":
            local = await self._download_file(file, "image") if self._is_url(file) else file
            if not local:
                return None
            return ContentPartImage(image_url=ImageURL(url=make_file_url(file_path=local), detail="high"))

        if file_type == "pdf":
            local = await self._download_file(file, "pdf") if self._is_url(file) else file
            if not local:
                return None
            return ContentPartPdf(pdf_url=PdfURL(url=make_file_url(file_path=local)))

        if file_type == "audio":
            local = await self._download_file(file, "audio") if self._is_url(file) else file
            if not local:
                return None
            return ContentPartAudio(audio_url=AudioURL(url=make_file_url(file_path=local)))

        if file_type == "video":
            if self._is_url(file):
                if self._is_youtube_url(file):
                    url = file  # YouTube URLs are natively supported
                else:
                    local = await self._download_file(file, "video")
                    url = make_file_url(file_path=local) if local else None
            else:
                url = make_file_url(file_path=file)
            if not url:
                return None
            return ContentPartVideo(video_url=VideoURL(url=url))

        # text: fetch or read content and embed as text
        try:
            if self._is_url(file):
                result = await fetch_url(file)
                content = result.get("markdown") or result.get("text", "") if result else ""
            else:
                with open(file, "r", encoding="utf-8", errors="ignore") as fh:
                    content = fh.read()
            if not content:
                return None
            return ContentPartText(text=f"File: {file}\n\n{content}")
        except Exception as exc:
            logger.error(f"| ❌ Failed to read text file ({file}): {exc}")
            return None

    async def _download_file(self, url: str, file_type: str = "file") -> Optional[str]:
        """Download a URL to a local file and return the local path."""
        try:
            downloads_dir = os.path.join(self.workdir, "downloads")
            os.makedirs(downloads_dir, exist_ok=True)
            parsed = urlparse(url)
            filename = os.path.basename(parsed.path)
            if not filename or "." not in filename:
                default_exts = {"video": ".mp4", "audio": ".mp3", "image": ".jpg", "pdf": ".pdf"}
                filename = f"{file_type}_{hash(url) % 100000}{default_exts.get(file_type, '.bin')}"
            local_path = os.path.join(downloads_dir, filename)
            logger.info(f"| 📥 Downloading {file_type} from {url}")
            await asyncio.to_thread(urllib.request.urlretrieve, url, local_path)
            return local_path
        except Exception as exc:
            logger.error(f"| ❌ Download failed for {url}: {exc}")
            return None

    async def _run_models_parallel(
        self, messages: list, active_models: Optional[List[str]], caller: str, label: str = ""
    ) -> str:
        """Run active_models in parallel and return all results for eval to synthesize."""
        models = active_models or []
        if not models:
            logger.warning("| ⚠️ No active models to run")
            return ""
        results = await asyncio.gather(
            *[self._call_model_with_messages(m, messages, caller) for m in models],
            return_exceptions=True,
        )
        parts: List[str] = []
        suffix = f" for {label}" if label else ""
        for model, r in zip(models, results):
            if isinstance(r, Exception):
                logger.warning(f"| ❌ {model} failed{suffix}: {r}")
            elif r:
                parts.append(f"**[{model}]**\n{r}")
                logger.info(f"| ✅ {model} done{suffix} ({len(r)} chars)")
        return "\n\n".join(parts)

    async def _call_model_with_messages(self, model: str, messages: list, caller: str = "") -> str:
        resp = await model_manager(model=model, messages=messages, caller=caller)
        return resp.message.strip() if resp and resp.message else ""

    # ------------------------------------------------------------------
    # Planning
    # ------------------------------------------------------------------

    async def _plan(
        self,
        task: str,
        session: AnalysisSession,
        classifications: List[FileTypeInfo],
    ) -> str:
        """Generate analysis guidance for the current round.

        Round 1: derives concrete analysis angles from task + file types.
        Round N: derives targeted conflict-resolution or gap-filling points from prior eval summaries.
        """
        file_info = (
            "\n".join(f"- {fi.file} ({fi.file_type})" for fi in classifications)
            if classifications else "No files — direct text reasoning"
        )
        try:
            messages = await prompt_manager.get_messages(
                prompt_name="deep_analyzer_v2_plan",
                agent_modules={
                    "task": task,
                    "file_info": file_info,
                    "previous": session.execution_log_text(),
                },
            )
            resp = await model_manager(
                model=self.model_name,
                messages=messages,
                caller="v2/plan",
            )
            if resp and resp.message and resp.message.strip():
                guidance = resp.message.strip()
                logger.info(f"| 🗺️ Guidance generated ({len(guidance)} chars)")
                return guidance
        except Exception as exc:
            logger.warning(f"| ⚠️ Planning failed: {exc}")
        return ""

    # ------------------------------------------------------------------
    # Evaluation
    # ------------------------------------------------------------------

    async def _evaluate(
        self, task: str, content: str, session: AnalysisSession
    ) -> AnalysisResult:
        """Evaluate whether the analysis fully answers the task."""
        previous = session.execution_log_text()
        try:
            messages = await prompt_manager.get_messages(
                prompt_name="deep_analyzer_v2_eval",
                agent_modules={"task": task, "previous": previous, "content": content},
            )
            resp = await model_manager(
                model=self.model_name,
                messages=messages,
                response_format=AnalysisResult,
                caller="v2/eval",
            )
            if resp and resp.extra and hasattr(resp.extra, "parsed_model") and resp.extra.parsed_model:
                return resp.extra.parsed_model
            return AnalysisResult(
                reasoning=resp.message.strip() if resp else "",
                found_answer=False,
            )
        except Exception as exc:
            logger.warning(f"| ❌ Evaluation failed: {exc}")
            return AnalysisResult(reasoning=content[:500], found_answer=False)

    # ------------------------------------------------------------------
    # File utilities
    # ------------------------------------------------------------------

    def _is_url(self, path: str) -> bool:
        return path.startswith(("http://", "https://"))

    def _is_youtube_url(self, url: str) -> bool:
        return any(re.search(p, url, re.IGNORECASE) for p in _YOUTUBE_PATTERNS)

    def _infer_file_type(self, path: str) -> str:
        """Infer file type from extension (or YouTube URL pattern)."""
        if self._is_url(path):
            if self._is_youtube_url(path):
                return "video"
            _, ext = os.path.splitext(urlparse(path).path.lower())
        else:
            _, ext = os.path.splitext(path.lower())
        if ext in _IMAGE_EXTS:
            return "image"
        if ext in _PDF_EXTS:
            return "pdf"
        if ext in _AUDIO_EXTS:
            return "audio"
        if ext in _VIDEO_EXTS:
            return "video"
        return "text"

    def _validate_file(self, path: str) -> bool:
        if self._is_url(path):
            return True
        if not os.path.exists(path):
            logger.warning(f"File not found: {path}")
            return False
        if os.path.getsize(path) > self.max_file_size:
            logger.warning(f"File too large: {path}")
        return True

    def _classify_files(self, files: List[str]) -> List[FileTypeInfo]:
        return [FileTypeInfo(file=f, file_type=self._infer_file_type(f)) for f in files]
