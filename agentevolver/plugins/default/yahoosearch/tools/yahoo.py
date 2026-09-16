"""Yahoo! Finance."""

from typing import Any, List, Optional

from agentevolver.response.types import Response
from agentevolver.plugins.types import PluginTool


class YahoosearchYahooTool(PluginTool):
    """Yahoo! Finance."""

    name: str = 'yahoo'
    display_name: str = 'Yahoo! Finance'
    description: str = 'Yahoo! Finance'

    output = {"method": "text", 'symbol': 'text', 'result': 'object'}


    def _render(self, data):
        return f"Yahoo {data['method']} for {data['symbol']}."

    async def __call__(self, symbol: str = "", method: str = "news", **kwargs) -> Response:
        sym = str(symbol or "").strip().upper()
        if not sym:
            return self._fail("yahoo: 'symbol' is required.")
        try:
            import yfinance as yf
            ticker = yf.Ticker(sym)
            data = getattr(ticker, method, None)
            if callable(data):
                data = data()
            result = data if isinstance(data, (list, dict)) else str(data)
        except Exception as exc:  # noqa: BLE001
            return self._fail(f"yahoo: {type(exc).__name__}: {exc}")
        return self._ok(method=method, symbol=sym, result=result)
