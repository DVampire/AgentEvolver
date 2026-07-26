"""Type definitions for the plugins module.

A **plugin** is a *packaging* unit: it adapts an external provider (Yahoo, FMP,
Tushare, Akshare, …) into AgentEvolver. It deliberately mirrors Langflow's
``bundles`` and Claude's ``plugin`` concept — the plugin is the container, and
what it *surfaces* is a semantic capability. A plugin's ``kind`` says which
family it surfaces (``data_source`` fetches records; other kinds may come
later). Regardless of kind, every plugin returns the canonical
``{message, data, files}`` :class:`Response` envelope so its output composes
with any other capability on the canvas / in a workflow.

The plugin itself is never a workflow step. A ``data_source`` plugin shows up on
the canvas as a semantic **datasource** node (``StepType.DATASOURCE``); the
runtime dispatches that node to ``plugin_manager``.

This module also defines the shared bases for the migrated Langflow **bundles**:
:class:`BundlePlugin` (the common mold — grouping metadata, preserved icon,
credential resolution, canonical envelopes) and the per-family templates built
on it — :class:`LLMPlugin`, :class:`EmbeddingPlugin`, :class:`RerankPlugin`,
:class:`VectorStorePlugin`, :class:`MemoryPlugin`, :class:`ComposioPlugin`. Every
migrated bundle tool under ``plugins/default/<bundle>/`` subclasses one of these.
"""

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

from agentevolver.session import BaseContext
from agentevolver.response.types import Response, ResponseType


class PluginContext(BaseContext):
    """Context passed into the plugin manager and individual plugin instances."""

    model_config = ConfigDict(arbitrary_types_allowed=True, extra="allow")

    id: str = Field(default="", description="Unique identifier for this plugin call.")
    name: str = Field(default="", description="Name of the plugin being called.")
    input: Dict[str, Any] = Field(default_factory=dict, description="Input payload passed to the plugin.")
    extra: Dict[str, Any] = Field(default_factory=dict, description="Arbitrary extra data attached to this context.")


class Plugin(BaseModel):
    """Base class for external-provider plugins.

    Subclasses implement :meth:`__call__` (the fetch/action) and return a
    :class:`Response` whose ``data`` carries the payload (for a ``data_source``:
    ``{"records": [...], ...}``), ``message`` a human summary, and ``files`` an
    optional artifact path.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True, extra="allow")

    name: str = Field(default="", description="Registered plugin name (e.g. ``yahoo``).")
    description: str = Field(default="", description="One-line description of the provider.")
    kind: str = Field(default="data_source", description="Plugin family: data_source / software / …")
    instruction: str = Field(default="", description="Full usage instruction, fetched on demand.")
    metadata: Dict[str, Any] = Field(default_factory=dict, description="Arbitrary plugin metadata.")
    permission_mode: str = Field(default="read_only", description="Permission mode for this plugin's side effects.")

    async def initialize(self) -> None:
        """Optional async setup (open API clients, read credentials)."""

    async def __call__(self, **kwargs) -> Response:
        """Run the plugin's action and return a canonical Response."""
        raise NotImplementedError("All plugins must implement __call__")

    async def cleanup(self) -> None:
        """Optional teardown of any provider resources."""


# Where a bundle's preserved SVG lives, relative to its package dir.
ICON_FILE = os.path.join("resources", "icon.svg")


class BundlePlugin(Plugin):
    """One tool of a migrated Langflow bundle.

    Subclasses set the class-level fields below; :meth:`_fill` derives the
    canvas/palette ``metadata`` from them. Override :meth:`__call__` to
    implement the capability — until then the inherited stub returns a clear
    "structure-phase" failure so the node is visible but honestly non-functional.
    """

    # Bundle grouping (what the palette nests tools under).
    bundle: str = ""                 # bundle id, e.g. "youtube"
    bundle_label: str = ""           # human label, e.g. "YouTube"
    display_name: str = ""           # tool label, e.g. "YouTube Channel"
    # Which palette section the tool's node belongs to (data / tools / models / …).
    category: str = "data"
    # Provenance + completeness.
    source: str = ""                 # langflow bundle path this was migrated from
    status: str = "structure"        # structure (stub) | partial | complete

    @model_validator(mode="after")
    def _fill(self) -> "BundlePlugin":
        # Every tool carries its bundle grouping + preserved icon so the catalog
        # can surface it and the palette can nest it under its bundle.
        md: Dict[str, Any] = dict(self.metadata or {})
        md.setdefault("canvas_category", self.category)
        md.setdefault("bundle", self.bundle)
        md.setdefault("bundle_label", self.bundle_label or self.bundle)
        md.setdefault("icon", f"bundle:{self.bundle}" if self.bundle else None)
        md.setdefault("status", self.status)
        md.setdefault("display_name", self.display_name or self.name)
        self.metadata = md
        return self

    def _secret(self, arg_value: Any = "", *env_names: str) -> str:
        """Resolve a provider credential: explicit arg → ``config[<bundle>_plugin]``
        block → environment variable(s). Mirrors how ``yahoo``/``fmp`` read keys."""
        if arg_value:
            return str(arg_value).strip()
        try:
            from agentevolver.config import config
            cfg = config.get(f"{self.bundle}_plugin", {}) or {}
            for k in ("api_key", "apikey", "token", "key"):
                if cfg.get(k):
                    return str(cfg[k]).strip()
        except Exception:  # noqa: BLE001 — config is best-effort
            pass
        for env in env_names:
            val = os.environ.get(env)
            if val:
                return val.strip()
        return ""

    @staticmethod
    def _ok(message: str, **data: Any) -> Response:
        """Canonical success envelope for an implemented bundle tool."""
        return Response(type=ResponseType.TOOL, success=True, message=message, data=data)

    @staticmethod
    def _fail(message: str) -> Response:
        """Canonical failure envelope (bad input / provider error)."""
        return Response(type=ResponseType.TOOL, success=False, message=message)

    def _stub(self) -> Response:
        """Uniform structure-phase response for a not-yet-implemented tool."""
        return Response(
            type=ResponseType.TOOL,
            success=False,
            message=(
                f"{self.name}: this tool from the '{self.bundle_label or self.bundle}' "
                "bundle is registered (structure phase) but not implemented yet."
            ),
            data={"bundle": self.bundle, "tool": self.name, "status": self.status},
        )

    async def __call__(self, **kwargs) -> Response:  # noqa: D401 — stub by default
        return self._stub()


class LLMPlugin(BundlePlugin):
    """One migrated Langflow LLM-provider bundle tool (prompt → generated text)."""

    category: str = "agent"
    # Default OpenAI-compatible base URL + env var for the api key (subclasses
    # that are OpenAI-compatible set these and use :meth:`_openai_compatible`).
    default_base_url: str = ""
    key_env: str = ""

    def _openai_compatible(self, model: str, api_key: str = "", base_url: str = "",
                           temperature: float = 0.1, **kw: Any) -> Any:
        """Build a ChatOpenAI pointed at an OpenAI-compatible endpoint."""
        from langchain_openai import ChatOpenAI  # noqa: PLC0415

        key = self._secret(api_key, *( [self.key_env] if self.key_env else []), "OPENAI_API_KEY")
        if not key:
            raise ValueError(f"no API key (set api_key or {self.key_env or 'OPENAI_API_KEY'}).")
        return ChatOpenAI(model=model, api_key=key,
                          base_url=(base_url or self.default_base_url or None),
                          temperature=temperature, **kw)

    def _model(self, **cfg: Any) -> Any:  # pragma: no cover - overridden
        """Construct the provider's langchain chat model. Subclasses implement."""
        raise NotImplementedError

    async def _generate(self, prompt: str = "", **cfg: Any) -> Response:
        """Build the model → invoke(prompt) → return generated text."""
        prompt = str(prompt or cfg.pop("input_value", "") or "").strip()
        if not prompt:
            return self._fail(f"{self.name}: 'prompt' is required.")
        try:
            model = self._model(**cfg)
            result = model.invoke(prompt)
            text = result.content if hasattr(result, "content") else str(result)
        except Exception as exc:  # noqa: BLE001 — missing SDK / bad key / provider error
            return self._fail(f"{self.name}: {type(exc).__name__}: {exc}")
        return self._ok(str(text), text=str(text), model=cfg.get("model_name") or cfg.get("model"))


class VectorStorePlugin(BundlePlugin):
    """One migrated Langflow vector-store bundle tool (ingest + similarity search)."""

    category: str = "knowledge"
    # Some backends (Vectara, Upstash's built-in model) manage embeddings
    # server-side — they don't need an external Embeddings model.
    needs_embedding: bool = True

    def _embedding(self, spec: str = "") -> Any:
        """Resolve an Embeddings model.

        ``spec`` may name a provider/model as ``openai:text-embedding-3-small``;
        by default OpenAI embeddings keyed by ``OPENAI_API_KEY`` (or the
        ``openai_plugin`` config block). Raises ``ValueError`` with a clear
        message if none can be built.
        """
        provider, _, model = (spec or "").partition(":")
        provider = (provider or "openai").strip().lower()
        if provider == "openai":
            key = self._secret("", "OPENAI_API_KEY")
            if not key:
                raise ValueError("no embedding available (set OPENAI_API_KEY or pass embedding='provider:model').")
            from langchain_openai import OpenAIEmbeddings  # noqa: PLC0415

            return OpenAIEmbeddings(model=model or "text-embedding-3-small", api_key=key)
        raise ValueError(f"unsupported embedding provider '{provider}' (try 'openai:<model>').")

    def _build(self, embedding: Any, **conn: Any) -> Any:  # pragma: no cover - overridden
        """Construct the backend langchain vector store. Subclasses implement."""
        raise NotImplementedError

    async def _run(self, query: str = "", texts: Optional[List[str]] = None,
                   embedding: str = "", k: int = 4, **conn: Any) -> Response:
        """Resolve embedding → build store → optional ingest → similarity search."""
        query = str(query or "").strip()
        texts = [t for t in (texts or []) if str(t).strip()]
        if not query and not texts:
            return self._fail(f"{self.name}: provide 'query' to search or 'texts' to ingest.")
        emb = None
        if self.needs_embedding:
            try:
                emb = self._embedding(embedding)
            except Exception as exc:  # noqa: BLE001
                return self._fail(f"{self.name}: {exc}")
        try:
            store = self._build(emb, **conn)
            ingested = 0
            if texts:
                store.add_texts(texts)
                ingested = len(texts)
            records = []
            if query:
                for doc in store.similarity_search(query, k=int(k)):
                    records.append({"content": doc.page_content, "metadata": getattr(doc, "metadata", {})})
        except Exception as exc:  # noqa: BLE001 — missing lib / unreachable store / bad creds
            return self._fail(f"{self.name}: {type(exc).__name__}: {exc}")
        msg = []
        if ingested:
            msg.append(f"ingested {ingested} text(s)")
        if query:
            msg.append(f"found {len(records)} match(es) for '{query}'")
        return self._ok(f"{self.bundle_label}: " + ", ".join(msg) + ".",
                        query=query, records=records, count=len(records), ingested=ingested)


class EmbeddingPlugin(BundlePlugin):
    """One migrated Langflow embedding bundle tool (text → vector)."""

    category: str = "knowledge"
    key_env: str = ""
    default_base_url: str = ""

    def _embeddings(self, **cfg: Any) -> Any:  # pragma: no cover - overridden
        raise NotImplementedError

    async def _embed(self, text: str = "", **cfg: Any) -> Response:
        text = str(text or cfg.pop("input_value", "") or "").strip()
        if not text:
            return self._fail(f"{self.name}: 'text' is required.")
        try:
            model = self._embeddings(**cfg)
            vector = model.embed_query(text)
        except Exception as exc:  # noqa: BLE001
            return self._fail(f"{self.name}: {type(exc).__name__}: {exc}")
        vector = list(vector)
        return self._ok(f"{self.bundle_label}: embedded text into a {len(vector)}-dim vector.",
                        vector=vector, dims=len(vector))


class RerankPlugin(BundlePlugin):
    """One migrated Langflow rerank bundle tool (query + docs → ranked docs)."""

    category: str = "knowledge"
    key_env: str = ""

    def _reranker(self, **cfg: Any) -> Any:  # pragma: no cover - overridden
        raise NotImplementedError

    async def _rerank(self, query: str = "", documents: Optional[List[str]] = None,
                      top_n: int = 3, **cfg: Any) -> Response:
        query = str(query or "").strip()
        documents = [str(d) for d in (documents or []) if str(d).strip()]
        if not query or not documents:
            return self._fail(f"{self.name}: 'query' and non-empty 'documents' are required.")
        try:
            from langchain_core.documents import Document  # noqa: PLC0415

            compressor = self._reranker(top_n=int(top_n), **cfg)
            docs = [Document(page_content=d) for d in documents]
            ranked = compressor.compress_documents(documents=docs, query=query)
        except Exception as exc:  # noqa: BLE001
            return self._fail(f"{self.name}: {type(exc).__name__}: {exc}")
        records = [{"content": d.page_content,
                    "score": (d.metadata or {}).get("relevance_score")} for d in ranked]
        return self._ok(f"{self.bundle_label}: reranked to {len(records)} document(s) for '{query}'.",
                        query=query, records=records, count=len(records))


class MemoryPlugin(BundlePlugin):
    """One migrated Langflow chat-memory bundle tool (get / add messages)."""

    category: str = "agent"

    def _history(self, session_id: str, **cfg: Any) -> Any:  # pragma: no cover - overridden
        """Build a langchain BaseChatMessageHistory for the session. Subclasses implement."""
        raise NotImplementedError

    async def _memory(self, action: str = "get", session_id: str = "default",
                      message: str = "", role: str = "user", **cfg: Any) -> Response:
        action = (action or "get").strip().lower()
        try:
            history = self._history(session_id, **cfg)
            if action == "add":
                if not message.strip():
                    return self._fail(f"{self.name}: 'message' is required to add.")
                if role == "ai":
                    history.add_ai_message(message)
                else:
                    history.add_user_message(message)
                return self._ok(f"{self.bundle_label}: added {role} message to '{session_id}'.",
                                session_id=session_id)
            records = [{"role": getattr(m, "type", "message"), "content": m.content}
                       for m in getattr(history, "messages", [])]
        except Exception as exc:  # noqa: BLE001
            return self._fail(f"{self.name}: {type(exc).__name__}: {exc}")
        return self._ok(f"{self.bundle_label}: {len(records)} message(s) in '{session_id}'.",
                        session_id=session_id, records=records, count=len(records))


class ComposioPlugin(BundlePlugin):
    """One migrated Langflow Composio app tool (execute an action for an app)."""

    category: str = "tool"
    app_name: str = ""  # composio toolkit slug (subclass sets it)

    async def __call__(self, action: str = "", arguments: Optional[Dict[str, Any]] = None,
                       api_key: str = "", entity_id: str = "default", **kwargs) -> Response:
        key = self._secret(api_key, "COMPOSIO_API_KEY")
        if not key:
            return self._fail(f"{self.name}: no Composio API key (set api_key or COMPOSIO_API_KEY).")
        action = str(action or "").strip()
        if not action:
            return self._fail(
                f"{self.name}: 'action' is required (a Composio action slug for the "
                f"'{self.app_name}' app, e.g. {self.app_name.upper()}_...).")
        try:
            from composio import Composio  # noqa: PLC0415

            client = Composio(api_key=key)
            result = client.tools.execute(action, arguments=arguments or {}, user_id=entity_id)
        except Exception as exc:  # noqa: BLE001 — missing SDK / auth / action error
            return self._fail(f"{self.name}: {type(exc).__name__}: {exc}")
        return self._ok(f"{self.bundle_label}: executed '{action}'.", app=self.app_name,
                        action=action, result=result)


__all__ = ["Plugin", "PluginContext", "BundlePlugin", "ICON_FILE", "LLMPlugin", "EmbeddingPlugin", "RerankPlugin", "VectorStorePlugin", "MemoryPlugin", "ComposioPlugin"]
