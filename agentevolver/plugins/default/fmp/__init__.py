"""Financial Modeling Prep (FMP) data-source plugin.

The second provider after Yahoo — same mold (a ``Plugin`` of kind
``data_source`` returning ``{message, data, files}``), showing the extension
path: one shape, N providers. FMP is a REST API keyed by ``apikey``; the key is
read from an arg, the ``fmp_plugin.api_key`` config block, or ``FMP_API_KEY``.
FMP ships a public ``demo`` key that works for ``AAPL`` only — handy for a live
smoke test without credentials.
"""

import os
from typing import Any, Dict, List

from pydantic import Field

from agentevolver.registry import PLUGIN
from agentevolver.response.types import Response, ResponseType
from agentevolver.plugins.types import Plugin

_HISTORY_URL = "https://financialmodelingprep.com/api/v3/historical-price-full/{symbol}"
_HEADERS = {"User-Agent": "Mozilla/5.0 (AgentEvolver data-source plugin)"}


@PLUGIN.register_module(force=True)
class FMPPlugin(Plugin):
    """Financial Modeling Prep — fetch OHLCV price history for a ticker symbol."""

    name: str = "fmp"
    description: str = "Fetch market data (OHLCV price history) from Financial Modeling Prep."
    kind: str = "data_source"
    instruction: str = (
        "## Provider\nFinancial Modeling Prep price history (REST, api-keyed).\n\n"
        "## Parameters\n"
        "- symbol (str): ticker, e.g. ``AAPL`` (required).\n"
        "- api_key (str): FMP api key; falls back to config/``FMP_API_KEY``. Public ``demo`` works for AAPL.\n"
        "- limit (int): most-recent N candles (default 30).\n\n"
        "## Output\n``data.records`` = list of {date, open, high, low, close, adj_close, volume}."
    )
    metadata: Dict[str, Any] = Field(default_factory=lambda: {
        "canvas_category": "data", "bundle": "fmp", "bundle_label": "FMP",
        "display_name": "FMP", "icon": "bundle:fmp"})
    api_key: str = Field(default="", description="FMP api key (else FMP_API_KEY / config).")

    def _resolve_key(self, arg_key: str) -> str:
        return str(arg_key or self.api_key or os.environ.get("FMP_API_KEY", "")).strip()

    async def __call__(self, symbol: str = "", api_key: str = "", limit: int = 30,
                       timeout: float = 30.0, **kwargs) -> Response:
        import httpx

        symbol = str(symbol or kwargs.get("ticker") or "").strip().upper()
        if not symbol:
            return Response(type=ResponseType.TOOL, success=False, message="fmp: 'symbol' is required.")
        key = self._resolve_key(api_key)
        if not key:
            return Response(type=ResponseType.TOOL, success=False,
                            message="fmp: no api key (set api_key / FMP_API_KEY; 'demo' works for AAPL).")

        try:
            limit = max(1, int(limit))
        except (TypeError, ValueError):
            limit = 30
        params = {"apikey": key, "timeseries": limit}
        try:
            async with httpx.AsyncClient(timeout=timeout, headers=_HEADERS, follow_redirects=True) as client:
                resp = await client.get(_HISTORY_URL.format(symbol=symbol), params=params)
        except Exception as exc:  # noqa: BLE001 — network failure is a failed result
            return Response(type=ResponseType.TOOL, success=False, message=f"fmp: request failed: {exc}")

        if resp.status_code >= 400:
            return Response(type=ResponseType.TOOL, success=False,
                            message=f"fmp: HTTP {resp.status_code} for {symbol}: {resp.text[:300]}")

        payload = resp.json()
        historical = (payload or {}).get("historical") if isinstance(payload, dict) else None
        if not historical:
            return Response(type=ResponseType.TOOL, success=False,
                            message=f"fmp: no data for {symbol} (check symbol/key).")

        records: List[Dict[str, Any]] = [
            {
                "date": row.get("date"),
                "open": row.get("open"),
                "high": row.get("high"),
                "low": row.get("low"),
                "close": row.get("close"),
                "adj_close": row.get("adjClose"),
                "volume": row.get("volume"),
            }
            for row in historical if isinstance(row, dict)
        ]
        # FMP returns newest-first; normalize to oldest-first like Yahoo.
        records.reverse()
        return Response(
            type=ResponseType.TOOL, success=True,
            message=f"Fetched {len(records)} daily candles for {symbol} from FMP.",
            data={"symbol": symbol, "records": records, "count": len(records)},
        )
