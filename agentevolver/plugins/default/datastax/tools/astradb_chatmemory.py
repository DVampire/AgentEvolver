"""Astra DB Chat Memory — from the Langflow `datastax` bundle (ported)."""

from typing import Any, List, Optional

from agentevolver.registry import PLUGIN
from agentevolver.response.types import Response
from agentevolver.plugins.types import MemoryPlugin


@PLUGIN.register_module(force=True)
class DatastaxAstradbChatmemoryPlugin(MemoryPlugin):
    name: str = "datastax.astradb_chatmemory"
    display_name: str = 'Astra DB Chat Memory'
    description: str = 'Retrieves and stores chat messages from Astra DB.'
    kind: str = "memory"
    bundle: str = "datastax"
    bundle_label: str = 'Astra DB'
    category: str = "agent"
    source: str = "langflow/bundles/datastax"
    status: str = "complete"

    def _history(self, session_id: str, **cfg: Any) -> Any:
        from langchain_astradb import AstraDBChatMessageHistory
        token = self._secret(cfg.get("token"), "ASTRA_DB_APPLICATION_TOKEN")
        endpoint = cfg.get("api_endpoint") or self._secret("", "ASTRA_DB_API_ENDPOINT")
        if not token or not endpoint:
            raise ValueError("Astra DB needs a token and api_endpoint.")
        return AstraDBChatMessageHistory(session_id=session_id, token=token, api_endpoint=endpoint,
                                         collection_name=cfg.get("collection_name") or "chat_history")

    async def __call__(self, action: str = "get", session_id: str = "default", message: str = "",
                       role: str = "user", token: str = "", api_endpoint: str = "", collection_name: str = "chat_history", **kwargs) -> Response:
        return await self._memory(action=action, session_id=session_id, message=message, role=role,
                                  **{k: v for k, v in kwargs.items()})
