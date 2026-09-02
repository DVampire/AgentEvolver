from __future__ import annotations
from typing import Any, Optional, Dict, List
import json
import aiohttp
from urllib.parse import quote
from pydantic import ConfigDict, Field
from dotenv import load_dotenv
load_dotenv()

from agentevolver.tool.default.web.search.types import SearchItem
from agentevolver.tool.types import Tool
from agentevolver.response.types import Response, ResponseType
from agentevolver.logger import logger
from agentevolver.registry import TOOL
from agentevolver.utils import hvac_client


_DESCRIPTION = "A search engine powered by Jina AI."

_GUIDANCE = """
- Useful for when you need to answer questions about current events.
- Input should be a search query.
"""

_EXAMPLES = [
    '{"name": "jina_search_tool", "args": {"query": "latest space missions 2026", "num_results": 5}}',
]


@TOOL.register_module(force=True)
class JinaSearch(Tool):
    """Tool that queries the Jina AI search engine.

    Example usages:
    .. code-block:: python
        # basic usage
        tool = JinaSearch()
    """
    model_config = ConfigDict(arbitrary_types_allowed=True, extra="allow")

    name: str = "jina_search_tool"
    description: str = _DESCRIPTION
    guidance: str = _GUIDANCE
    examples: List[str] = _EXAMPLES
    metadata: Dict[str, Any] = Field(default={}, description="The metadata of the tool")
    api_key: str = Field(default="", description="Jina AI API key")

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.api_key = hvac_client.get("JINA_API_KEY") or ""

    async def _search_jina(
        self,
        query: str,
        num_results: int = 10,
        country: Optional[str] = None,
        lang: Optional[str] = None,
        filter_year: Optional[int] = None,
    ) -> List[SearchItem]:
        """
        Perform a Jina AI search using the provided parameters.
        Returns a list of SearchItem objects.

        `country` and `lang` reach the API as the `X-Site-Locale` headers it reads;
        `filter_year` has no API parameter, so it is folded into the query the way a
        person would type it. All three were previously accepted and dropped between
        `__call__` and here — the tool told the model it could narrow a search and then
        ran the unnarrowed one, which is worse than not offering the parameter: the model
        reads the results as having been filtered.
        """
        results = []

        headers = {"Accept": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        # No API parameter for a year, so it goes where a person would put it. Jina
        # searches the web; the web's own convention for this is a term in the query.
        effective = f"{query} {filter_year}" if filter_year else query
        url = f"https://s.jina.ai/{quote(effective)}"
        params = {"count": num_results}
        if country:
            headers["X-Site-Country"] = country
        if lang:
            headers["X-Site-Locale"] = lang

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    url,
                    headers=headers,
                    params=params,
                    timeout=aiohttp.ClientTimeout(total=30),
                ) as resp:
                    resp.raise_for_status()
                    response_data = await resp.json()
        except aiohttp.ClientResponseError as e:
            logger.error(f"Jina search HTTP error: {e.status} {e.message}")
            return results
        except Exception as e:
            logger.error(f"Jina search API call failed: {e}")
            return results

        if response_data is None:
            logger.warning("Jina search returned None response")
            return results

        logger.debug(f"Jina search response type: {type(response_data)}")

        # Response format: {"code": 200, "data": [...]}
        raw_items = None
        if isinstance(response_data, dict):
            raw_items = response_data.get("data") or response_data.get("results") or []
        elif isinstance(response_data, list):
            raw_items = response_data

        if not raw_items:
            logger.warning(f"Jina search returned no results. Response: {str(response_data)}")
            return results

        for item in raw_items:
            if item is None:
                continue

            if isinstance(item, dict):
                title = item.get("title", "") or ""
                url_val = item.get("url", "") or ""
                description = item.get("description", "") or item.get("content", "") or ""
                date = item.get("date", None)
            else:
                title = getattr(item, "title", None) or ""
                url_val = getattr(item, "url", None) or ""
                description = getattr(item, "description", None) or getattr(item, "content", None) or ""
                date = getattr(item, "date", None)

            if url_val:
                results.append(SearchItem(
                    title=title,
                    url=url_val,
                    description=description,
                    date=date,
                ))

        return results

    async def __call__(
        self,
        query: str,
        image: Optional[str] = None,
        num_results: Optional[int] = 5,
        country: Optional[str] = "us",
        lang: Optional[str] = "en",
        filter_year: Optional[int] = None,
        **kwargs,
    ) -> Response:
        """
        Jina AI search tool.

        Args:
            query (str): The query to search for.
            image (Optional[str]): Ignored by Jina's web search; accepted so every
                search tool takes the same arguments.
            num_results (Optional[int]): The number of search results to return.
            country (Optional[str]): The country to search in.
            lang (Optional[str]): The language to search in.
            filter_year (Optional[int]): The year to filter results by.
        """
        try:
            search_items = await self._search_jina(
                query, num_results=num_results,
                country=country, lang=lang, filter_year=filter_year)

            results_json = json.dumps([{
                "title": item.title,
                "url": item.url,
                "description": item.description or "",
                "date": item.date or "",
            } for item in search_items], ensure_ascii=False, indent=4)

            message = f"Jina search results for query: {query}\n\n{results_json}"

            return Response(type=ResponseType.TOOL, 
                success=True,
                message=message,
                data={
                    "query": query,
                    "num_results": len(search_items),
                    "search_items": search_items,
                    "engine": "jina",
                }
            )

        except Exception as e:
            logger.error(f"Error in Jina search: {e}")
            return Response(type=ResponseType.TOOL, success=False, message=f"Error in Jina search: {str(e)}")
