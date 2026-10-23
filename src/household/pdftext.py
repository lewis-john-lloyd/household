"""Text from a PDF with its layout kept, so columns stay at stable positions."""

import io

MAX_PAGES = 5  # every supported layout fits; long documents are identified from their first pages


def extract(data, max_pages=MAX_PAGES):
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(data))
    pages = reader.pages[:max_pages]
    return "\n".join(page.extract_text(extraction_mode="layout") for page in pages)
