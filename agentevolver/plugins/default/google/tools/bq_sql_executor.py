"""BigQuery."""

from typing import Any, List, Optional

from agentevolver.response.types import Response
from agentevolver.plugins.types import PluginTool


class GoogleBqSqlExecutorTool(PluginTool):
    """BigQuery."""

    name: str = 'google_bq_sql_executor'
    display_name: str = 'BigQuery'
    description: str = 'Execute SQL queries on Google BigQuery.'

    output = {'records': 'list', 'count': 'any'}

    def _render(self, data):
        return f"BigQuery returned {data['count']} rows."

    async def __call__(self, query: str = "", project: str = "", credentials_json: str = "", **kwargs) -> Response:
        if not query:
            return self._fail("google.bigquery: 'query' is required.")
        try:
            from google.cloud import bigquery
            client = bigquery.Client(project=project or None)
            rows = [dict(r) for r in client.query(query).result()]
        except Exception as exc:  # noqa: BLE001
            return self._fail(f"google.bigquery: {type(exc).__name__}: {exc}")
        return self._ok(records=rows, count=len(rows))
