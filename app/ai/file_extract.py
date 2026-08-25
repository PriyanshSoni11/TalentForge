import io

from pypdf import PdfReader
from docx import Document


def extract_text_from_resume(file_bytes, filename):
    lower = filename.lower()
    if lower.endswith(".pdf"):
        reader = PdfReader(io.BytesIO(file_bytes))
        return "\n".join(page.extract_text() or "" for page in reader.pages)
    if lower.endswith(".docx"):
        doc = Document(io.BytesIO(file_bytes))
        return "\n".join(p.text for p in doc.paragraphs)
    raise ValueError("Unsupported resume format — only .pdf and .docx are accepted")
