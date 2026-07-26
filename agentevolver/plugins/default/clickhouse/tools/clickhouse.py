"""ClickHouse — from the Langflow `clickhouse` vector-store bundle (ported)."""

from typing import Any, List, Optional

from agentevolver.registry import PLUGIN
from agentevolver.response.types import Response
from agentevolver.plugins.types import VectorStorePlugin


@PLUGIN.register_module(force=True)
class ClickhouseClickhousePlugin(VectorStorePlugin):
    name: str = "clickhouse.clickhouse"
    display_name: str = 'ClickHouse'
    description: str = 'ClickHouse Vector Store with search capabilities'
    kind: str = "vectorstore"
    bundle: str = "clickhouse"
    bundle_label: str = 'ClickHouse'
    source: str = "langflow/bundles/clickhouse"
    status: str = "complete"
    needs_embedding: bool = True

    def _build(self, embedding: Any, **conn: Any) -> Any:
        from langchain_community.vectorstores import Clickhouse, ClickhouseSettings
        settings = ClickhouseSettings(host=conn.get("host") or "localhost",
                                      port=int(conn.get("port") or 8123),
                                      username=conn.get("username") or "default",
                                      password=conn.get("password") or "",
                                      database=conn.get("database") or "default",
                                      table=conn.get("table") or "langflow")
        return Clickhouse(embedding, config=settings)

    async def __call__(self, host: str = "localhost", port: int = 8123, username: str = "default", password: str = "", database: str = "default", table: str = "langflow", query: str = "", texts: Optional[List[str]] = None,
                       embedding: str = "", k: int = 4, **kwargs) -> Response:
        return await self._run(query=query, texts=texts, embedding=embedding, k=int(k),
            host=host, port=int(port), username=username, password=password,
            database=database, table=table)
