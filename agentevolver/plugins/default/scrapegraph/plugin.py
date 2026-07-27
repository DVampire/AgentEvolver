"""ScrapeGraph plugin."""

from agentevolver.plugins.types import Plugin
from agentevolver.registry import PLUGIN

from .tools.markdownify_api import ScrapegraphMarkdownifyApiTool
from .tools.search_api import ScrapegraphSearchApiTool
from .tools.smart_scraper_api import ScrapegraphSmartScraperApiTool


@PLUGIN.register_module(force=True)
class ScrapegraphPlugin(Plugin):
    """ScrapeGraph tools."""

    tools = (
        ScrapegraphMarkdownifyApiTool,
        ScrapegraphSearchApiTool,
        ScrapegraphSmartScraperApiTool,
    )

    name: str = 'scrapegraph'
    display_name: str = 'ScrapeGraph'
    description: str = 'ScrapeGraph tools.'
    category: str = 'data'
    type: str = 'tool'
