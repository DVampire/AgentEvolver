"""Git."""

from typing import Any, Optional

from agentevolver.response.types import Response
from agentevolver.plugins.types import PluginTool


class GitTool(PluginTool):
    """Git."""

    name: str = 'git'
    display_name: str = 'Git'
    description: str = ''

    output = {'records': 'list', 'count': 'any'}

    def _render(self, data):
        return f"Loaded {data['count']} files from the repo."

    async def __call__(self, clone_url: str = "", repo_path: str = "", branch: str = "main", file_filter: str = "", **kwargs) -> Response:
        import tempfile
        if not clone_url and not repo_path:
            return self._fail("git: 'clone_url' or 'repo_path' is required.")
        try:
            from langchain_community.document_loaders.git import GitLoader
            path = repo_path or tempfile.mkdtemp()
            ff = None
            if file_filter:
                pats = [p.strip() for p in file_filter.split(",") if p.strip()]
                from fnmatch import fnmatch
                ff = lambda fp: any(fnmatch(fp, p) for p in pats)  # noqa: E731
            loader = GitLoader(repo_path=path, clone_url=clone_url or None, branch=branch, file_filter=ff)
            docs = loader.load()
        except Exception as exc:  # noqa: BLE001
            return self._fail(f"git: {type(exc).__name__}: {exc}")
        records = [{"content": d.page_content, "metadata": d.metadata} for d in docs]
        return self._ok(records=records, count=len(records))
