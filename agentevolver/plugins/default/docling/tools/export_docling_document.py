"""Export DoclingDocument — from the Langflow `docling` bundle (ported)."""

from typing import Any, List, Optional

from agentevolver.registry import PLUGIN
from agentevolver.response.types import Response
from agentevolver.plugins.types import BundlePlugin


@PLUGIN.register_module(force=True)
class DoclingExportDoclingDocumentPlugin(BundlePlugin):
    name: str = "docling.export_docling_document"
    display_name: str = 'Export DoclingDocument'
    description: str = 'Export DoclingDocument to markdown, html or other formats.'
    kind: str = "tool"
    bundle: str = "docling"
    bundle_label: str = 'Docling'
    category: str = "files"
    source: str = "langflow/bundles/docling"
    status: str = "complete"

    async def __call__(self, source: str = "", export_format: str = "markdown", **kwargs) -> Response:
        src = str(source or "").strip()
        if not src:
            return self._fail("docling.export: 'source' is required.")
        try:
            from docling.document_converter import DocumentConverter
            doc = DocumentConverter().convert(src).document
            out = doc.export_to_markdown() if export_format == "markdown" else doc.export_to_dict()
        except Exception as exc:  # noqa: BLE001
            return self._fail(f"docling.export: {type(exc).__name__}: {exc}")
        return self._ok(f"Exported document as {export_format}.", content=out, format=export_format)
