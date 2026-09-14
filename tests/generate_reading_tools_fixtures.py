"""Deterministic self-authored PDFs for reading tools, CC0-1.0 content.
Embedded DejaVu Sans comes from Debian fonts-dejavu-core (Bitstream Vera license).
"""

from pathlib import Path
import hashlib
import json
from reportlab.pdfgen import canvas
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from PIL import Image, ImageDraw
from reportlab.lib.utils import ImageReader
from pypdf import PdfReader, PdfWriter


def generate(destination):
    root = Path(destination)
    root.mkdir(parents=True, exist_ok=True)
    font = Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf")
    pdfmetrics.registerFont(TTFont("ReadingTools", str(font)))
    path = root / "long-reading.pdf"
    c = canvas.Canvas(str(path), pagesize=(612, 792), invariant=1)
    c.setTitle("iPaper self-authored 300-page reading tools fixture")
    for page in range(1, 301):
        c.setFont("ReadingTools", 16)
        c.drawString(48, 740, f"iPaper reading tools - page {page}")
        if page in (1, 150, 299):
            c.bookmarkPage(f"page-{page}")
            c.addOutlineEntry(
                {1: "Introduction", 150: "Methods", 299: "Late experimental results"}[
                    page
                ],
                f"page-{page}",
                level=0,
            )
        c.setFont("ReadingTools", 12)
        lines = [
            "Self-authored synthetic test content. No production research.",
            "Search is independent of rendered pages. The navigation preserves position.",
        ]
        if page == 299:
            lines += [
                "UniqueEndEvidence739: a controlled result on the penultimate page.",
                "The ofﬁce experiment uses a ligature and soft\u00adhyphen.",
                "A wrapped paragraph continues",
                "onto the next line with exact text mapping.",
            ]
        for index, line in enumerate(lines):
            c.drawString(48, 695 - index * 26, line)
        c.setFont("ReadingTools", 10)
        c.drawString(48, 40, f"Synthetic fixture | {page} / 300")
        c.showPage()
    c.save()
    writer = PdfWriter()
    writer.clone_document_from_reader(PdfReader(path))
    writer.pages[298].rotate(90)
    with path.open("wb") as target:
        writer.write(target)
    c = canvas.Canvas(str(root / "no-outline.pdf"), pagesize=(612, 792), invariant=1)
    c.setFont("ReadingTools", 15)
    c.drawString(48, 720, "Text exists; this PDF deliberately has no outline.")
    c.save()
    image = Image.new("RGB", (1000, 1300), "white")
    draw = ImageDraw.Draw(image)
    draw.rectangle((70, 80, 900, 500), outline="black", width=5)
    draw.text((100, 150), "SCANNED IMAGE: NO PDF TEXT LAYER", fill="black")
    c = canvas.Canvas(str(root / "scanned.pdf"), pagesize=(612, 792), invariant=1)
    c.drawImage(ImageReader(image), 0, 0, width=612, height=792)
    c.save()
    manifest = {
        "contentLicense": "CC0-1.0",
        "author": "iPaper test suite",
        "font": {
            "name": "DejaVu Sans",
            "source": "Debian fonts-dejavu-core /usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "license": "Bitstream Vera / DejaVu license",
            "sha256": hashlib.sha256(font.read_bytes()).hexdigest(),
        },
        "files": [],
    }
    for file in sorted(root.glob("*.pdf")):
        manifest["files"].append(
            {
                "name": file.name,
                "pages": len(PdfReader(file).pages),
                "sha256": hashlib.sha256(file.read_bytes()).hexdigest(),
            }
        )
    (root / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")


if __name__ == "__main__":
    generate(Path(__file__).parent / "fixtures/reading-tools")
