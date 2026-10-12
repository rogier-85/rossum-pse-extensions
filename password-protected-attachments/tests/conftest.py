from __future__ import annotations

from io import BytesIO

import pytest
from pypdf import PdfReader, PdfWriter


@pytest.fixture
def make_pdf():
    def _make(password: str | None = None, pages: int = 1) -> bytes:
        writer = PdfWriter()
        for _ in range(pages):
            writer.add_blank_page(width=612, height=792)
        if password is not None:
            writer.encrypt(password)
        output = BytesIO()
        writer.write(output)
        return output.getvalue()

    return _make


def assert_unlocked_pdf(content: bytes, pages: int) -> None:
    reader = PdfReader(BytesIO(content))
    assert not reader.is_encrypted
    assert len(reader.pages) == pages
