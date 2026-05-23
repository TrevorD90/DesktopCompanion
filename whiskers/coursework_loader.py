"""Parse uploaded coursework into a list of Problem objects.

Supported sources:
- PDF (pypdf): one Problem per detected numbered question if regex matches,
  else one Problem per page.
- Image (PNG/JPG/JPEG): one Problem per image, with raw bytes attached for
  Claude vision. No local OCR.
- Plain text: one Problem per detected numbered question, else one Problem
  for the whole blob.
"""

import os
import re
import uuid
from dataclasses import dataclass, field
from typing import List, Optional


# Match "1.", "1)", "Problem 1", "Question 1:" at the start of a line.
_PROBLEM_HEAD_RE = re.compile(
    r'^\s*(?:(?:problem|question|q)\s*)?(\d+)\s*[.):]\s+',
    re.IGNORECASE,
)


@dataclass
class Problem:
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])
    page: int = 1
    text: str = ''
    image_path: Optional[str] = None  # absolute path to image file
    image_media_type: Optional[str] = None  # e.g. "image/png"

    @property
    def has_image(self) -> bool:
        return self.image_path is not None

    def preview(self, max_chars=120) -> str:
        if self.text:
            return (self.text[:max_chars] + '…') if len(self.text) > max_chars else self.text
        if self.has_image:
            return f'(image — {os.path.basename(self.image_path)})'
        return '(empty)'

    def to_dict(self):
        return {
            'id': self.id,
            'page': self.page,
            'preview': self.preview(),
            'has_image': self.has_image,
        }


def _split_text_into_problems(text: str, page: int = 1) -> List[Problem]:
    """Find lines that start with a problem number; split the text there.

    Falls back to a single Problem if no headers are found.
    """
    lines = text.splitlines()
    heads = []  # list of (line_index, problem_number)
    for i, line in enumerate(lines):
        m = _PROBLEM_HEAD_RE.match(line)
        if m:
            heads.append((i, int(m.group(1))))

    if not heads:
        body = text.strip()
        if not body:
            return []
        return [Problem(page=page, text=body)]

    problems = []
    for idx, (line_idx, _num) in enumerate(heads):
        end = heads[idx + 1][0] if idx + 1 < len(heads) else len(lines)
        body = '\n'.join(lines[line_idx:end]).strip()
        if body:
            problems.append(Problem(page=page, text=body))
    return problems


def load_from_text(text: str) -> List[Problem]:
    return _split_text_into_problems(text, page=1)


def load_from_pdf(pdf_path: str) -> List[Problem]:
    from pypdf import PdfReader

    reader = PdfReader(pdf_path)
    problems: List[Problem] = []
    for page_idx, page in enumerate(reader.pages, start=1):
        page_text = page.extract_text() or ''
        page_problems = _split_text_into_problems(page_text, page=page_idx)
        if not page_problems and page_text.strip():
            # Whole-page fallback already handled inside _split_text_into_problems,
            # but defensive: don't drop pages with whitespace-only extraction.
            continue
        problems.extend(page_problems)

    if not problems:
        # PDF had no extractable text on any page. Surface that to the caller.
        raise ValueError('PDF contained no extractable text. Try uploading a photo instead.')
    return problems


def load_from_image(image_path: str) -> List[Problem]:
    """Single Problem per image. The model reads the image via vision API."""
    ext = os.path.splitext(image_path)[1].lower()
    media_type = {
        '.png': 'image/png',
        '.jpg': 'image/jpeg',
        '.jpeg': 'image/jpeg',
        '.gif': 'image/gif',
        '.webp': 'image/webp',
    }.get(ext)
    if not media_type:
        raise ValueError(f'Unsupported image type: {ext}')

    return [Problem(page=1, text='', image_path=image_path, image_media_type=media_type)]


def load_from_upload(file_path: str) -> List[Problem]:
    """Dispatch on extension."""
    ext = os.path.splitext(file_path)[1].lower()
    if ext == '.pdf':
        return load_from_pdf(file_path)
    if ext in ('.png', '.jpg', '.jpeg', '.gif', '.webp'):
        return load_from_image(file_path)
    if ext in ('.txt', '.md'):
        with open(file_path, 'r', encoding='utf-8') as f:
            return load_from_text(f.read())
    raise ValueError(f'Unsupported file type: {ext}')
