"""Chunk DoclingDocument — from the Langflow `docling` bundle (ported)."""

from typing import Any, List, Optional

from agentevolver.registry import PLUGIN
from agentevolver.response.types import Response
from agentevolver.plugins.types import BundlePlugin


@PLUGIN.register_module(force=True)
class DoclingChunkDoclingDocumentPlugin(BundlePlugin):
    name: str = "docling.chunk_docling_document"
    display_name: str = 'Chunk DoclingDocument'
    description: str = 'Use DoclingDocument chunkers to split the document into chunks.'
    kind: str = "tool"
    bundle: str = "docling"
    bundle_label: str = 'Docling'
    category: str = "files"
    source: str = "langflow/bundles/docling"
    status: str = "complete"

    async def __call__(self, source: str = "", max_tokens: int = 512, **kwargs) -> Response:
        src = str(source or "").strip()
        if not src:
            return self._fail("docling.chunk: 'source' is required.")
        try:
            from docling.document_converter import DocumentConverter
            from docling.chunking import HybridChunker
            doc = DocumentConverter().convert(src).document
            chunks = list(HybridChunker(max_tokens=int(max_tokens)).chunk(doc))
        except Exception as exc:  # noqa: BLE001
            return self._fail(f"docling.chunk: {type(exc).__name__}: {exc}")
        records = [{"content": getattr(c, "text", str(c))} for c in chunks]
        return self._ok(f"Chunked into {len(records)} pieces.", records=records, count=len(records))
