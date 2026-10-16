"""Generate small, redistributable fixtures without real course materials."""
from pathlib import Path

from pypdf import PdfWriter
from pypdf.generic import DictionaryObject, NameObject, DecodedStreamObject


def make_pdf(path: Path, text: str):
    """Write one PDF page with extractable Helvetica text."""
    path.parent.mkdir(parents=True, exist_ok=True)
    writer = PdfWriter()
    try:
        page = writer.add_blank_page(width=300, height=160)
        font = DictionaryObject({
            NameObject('/Type'): NameObject('/Font'),
            NameObject('/Subtype'): NameObject('/Type1'),
            NameObject('/BaseFont'): NameObject('/Helvetica'),
        })
        page[NameObject('/Resources')] = DictionaryObject({
            NameObject('/Font'): DictionaryObject({NameObject('/F1'): font})
        })
        escaped = text.replace('\\', '\\\\').replace('(', '\\(').replace(')', '\\)')
        content = DecodedStreamObject()
        content.set_data(f'BT /F1 12 Tf 20 80 Td ({escaped}) Tj ET'.encode('ascii'))
        page[NameObject('/Contents')] = content
        writer.write(str(path))
    finally:
        writer.close()
