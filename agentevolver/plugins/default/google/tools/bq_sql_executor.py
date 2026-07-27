"""BigQuery — from the Langflow `google` bundle (ported)."""

from typing import Any, List, Optional

from agentevolver.registry import PLUGIN
from agentevolver.response.types import Response
from agentevolver.plugins.types import BundlePlugin


@PLUGIN.register_module(force=True)
class GoogleGoogleBqSqlExecutorPlugin(BundlePlugin):
    name: str = "google.google_bq_sql_executor"
    display_name: str = 'BigQuery'
    description: str = 'Execute SQL queries on Google BigQuery.'
    kind: str = "tool"
    bundle: str = "google"
    bundle_label: str = 'Google'
    category: str = "data"
    source: str = "langflow/bundles/google"
    status: str = "complete"

    async def __call__(self, query: str = "", project: str = "", credentials_json: str = "", **kwargs) -> Response:
        if not query:
            return self._fail("google.bigquery: 'query' is required.")
        try:
            from google.cloud import bigquery
            client = bigquery.Client(project=project or None)
            rows = [dict(r) for r in client.query(query).result()]
        except Exception as exc:  # noqa: BLE001
            return self._fail(f"google.bigquery: {type(exc).__name__}: {exc}")
        return self._ok(f"BigQuery returned {len(rows)} rows.", records=rows, count=len(rows))
