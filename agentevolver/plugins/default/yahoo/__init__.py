"""Yahoo Finance data-source plugin.

Fetches OHLCV history for a ticker via Yahoo's public chart endpoint over plain
HTTP (``httpx``) — no ``yfinance`` dependency. Returns the canonical Response
envelope with ``data = {"symbol", "records": [...], "count"}`` so a downstream
``process`` step can clean it and a ``benchmark`` step can evaluate it.
"""

from datetime import datetime, timezone
from typing import Any, Dict, List

from pydantic import Field

from agentevolver.registry import PLUGIN
from agentevolver.response.types import Response, ResponseType
from agentevolver.plugins.types import Plugin

_CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
# Yahoo rejects request without a browser-ish UA (429/empty), so send one.
_HEADERS = {"User-Agent": "Mozilla/5.0 (AgentEvolver data-source plugin)"}


@PLUGIN.register_module(force=True)
class YahooPlugin(Plugin):
    """Yahoo Finance — fetch OHLCV price history for a ticker symbol."""

    name: str = "yahoo"
    description: str = "Fetch market data (OHLCV price history) from Yahoo Finance."
    kind: str = "data_source"
    instruction: str = (
        "## Provider\nYahoo Finance price history.\n\n"
        "## Parameters\n"
        "- symbol (str): ticker, e.g. ``AAPL`` (required).\n"
        "- range (str): time span — 1d/5d/1mo/3mo/6mo/1y/2y/5y/max (default ``1mo``).\n"
        "- interval (str): candle interval — 1m/5m/1h/1d/1wk/1mo (default ``1d``).\n\n"
        "## Output\n``data.records`` = list of {date, open, high, low, close, adj_close, volume}."
    )
    metadata: Dict[str, Any] = Field(default_factory=lambda: {
        "canvas_category": "data", "bundle": "yahoo", "bundle_label": "Yahoo Finance",
        "display_name": "Yahoo Finance", "icon": "bundle:yahoo"})

    async def __call__(self, symbol: str = "", range: str = "1mo", interval: str = "1d",  # noqa: A002
                       timeout: float = 30.0, **kwargs) -> Response:
        import httpx

        symbol = str(symbol or kwargs.get("ticker") or "").strip().upper()
        if not symbol:
            return Response(type=ResponseType.TOOL, success=False, message="yahoo: 'symbol' is required.")

        params = {"range": range, "interval": interval}
        try:
            async with httpx.AsyncClient(timeout=timeout, headers=_HEADERS, follow_redirects=True) as client:
                resp = await client.get(_CHART_URL.format(symbol=symbol), params=params)
        except Exception as exc:  # noqa: BLE001 — network failure is a failed result
            return Response(type=ResponseType.TOOL, success=False, message=f"yahoo: request failed: {exc}")

        if resp.status_code >= 400:
            return Response(type=ResponseType.TOOL, success=False,
                            message=f"yahoo: HTTP {resp.status_code} for {symbol}: {resp.text[:300]}")

        payload = resp.json()
        chart = (payload or {}).get("chart") or {}
        if chart.get("error"):
            return Response(type=ResponseType.TOOL, success=False, message=f"yahoo: {chart['error']}")
        results = chart.get("result") or []
        if not results:
            return Response(type=ResponseType.TOOL, success=False, message=f"yahoo: no data for {symbol}.")

        result = results[0]
        timestamps: List[int] = result.get("timestamp") or []
        quote = ((result.get("indicators") or {}).get("quote") or [{}])[0]
        adjclose = (((result.get("indicators") or {}).get("adjclose") or [{}])[0]).get("adjclose") or []

        records: List[Dict[str, Any]] = []
        for i, ts in enumerate(timestamps):
            records.append({
                "date": datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d"),
                "open": _at(quote.get("open"), i),
                "high": _at(quote.get("high"), i),
                "low": _at(quote.get("low"), i),
                "close": _at(quote.get("close"), i),
                "adj_close": _at(adjclose, i),
                "volume": _at(quote.get("volume"), i),
            })

        return Response(
            type=ResponseType.TOOL, success=True,
            message=f"Fetched {len(records)} {interval} candles for {symbol} ({range}).",
            data={"symbol": symbol, "range": range, "interval": interval,
                  "records": records, "count": len(records)},
        )


def _at(series, index):
    """Safe indexed access — Yahoo pads gaps with nulls / short arrays."""
    if isinstance(series, list) and index < len(series):
        return series[index]
    return None
