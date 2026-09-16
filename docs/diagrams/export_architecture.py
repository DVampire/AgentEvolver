"""Convert the PDF exported from each PowerPoint deck to portable static vectors.

Requires PyMuPDF 1.26.7. Outlined glyphs preserve CJK on readers without the font.
The editable text stays in the .pptx source. No raster illustration is embedded.
"""
import argparse
from pathlib import Path
from xml.etree import ElementTree as ET

import pymupdf


def export(pdf_dir: Path):
    out = Path(__file__).resolve().parents[1] / "assets"
    for stem in ("arch", "arch_zh"):
        with pymupdf.open(pdf_dir / f"{stem}.pdf") as document:
            if len(document) != 1:
                raise ValueError(f"{stem}: expected one architecture slide")
            page = document[0]
            svg = page.get_svg_image(text_as_path=True)
            root = ET.fromstring(svg)
            if any(el.tag.rsplit("}", 1)[-1] in {"image", "script", "foreignObject"} for el in root.iter()):
                raise ValueError(f"{stem}: expected a static vector export")
            title = "AgentEvolver 系统架构" if stem.endswith("zh") else "AgentEvolver system architecture"
            svg = svg.replace('>', f'><title>{title}</title>', 1)
            (out / f"{stem}.svg").write_text(svg, encoding="utf-8")
            page.get_pixmap(matrix=pymupdf.Matrix(1.5, 1.5), alpha=False).save(out / f"{stem}.png")
            print(f"{stem}: editable deck → PDF → static SVG + PNG")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pdf_dir", type=Path)
    export(parser.parse_args().pdf_dir)
