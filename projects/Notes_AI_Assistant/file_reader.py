import os


def extract_text(file_path: str) -> str:
    ext = os.path.splitext(file_path)[1].lower()

    if ext == '.pdf':
        return _read_pdf(file_path)
    elif ext == '.docx':
        return _read_docx(file_path)
    elif ext in ('.txt', '.md', '.csv', '.rtf'):
        return _read_text(file_path)
    else:
        raise ValueError(f"Unsupported file type: {ext}")


def _read_pdf(path: str) -> str:
    from pypdf import PdfReader
    reader = PdfReader(path)
    pages = []
    for page in reader.pages:
        text = page.extract_text()
        if text:
            pages.append(text.strip())
    return '\n\n'.join(pages)


def _read_docx(path: str) -> str:
    from docx import Document
    doc = Document(path)
    paras = [p.text for p in doc.paragraphs if p.text.strip()]
    return '\n'.join(paras)


def _read_text(path: str) -> str:
    with open(path, 'r', encoding='utf-8', errors='replace') as f:
        return f.read()
