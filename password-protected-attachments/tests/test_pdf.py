from __future__ import annotations

import pytest
from pypdf.errors import WrongPasswordError

from app.pdf import decrypt_pdf
from tests.conftest import assert_unlocked_pdf


def test_unencrypted_pdf_returns_none(make_pdf) -> None:
    assert decrypt_pdf(make_pdf(pages=2), ["unused"]) is None


def test_decrypts_pdf_and_retains_pages(make_pdf) -> None:
    unlocked = decrypt_pdf(make_pdf("Case Sensitive", pages=3), ["wrong", "Case Sensitive"])
    assert unlocked is not None
    assert_unlocked_pdf(unlocked, 3)


def test_accepts_empty_password(make_pdf) -> None:
    unlocked = decrypt_pdf(make_pdf("", pages=1), [])
    assert unlocked is not None
    assert_unlocked_pdf(unlocked, 1)


def test_wrong_password_raises(make_pdf) -> None:
    with pytest.raises(WrongPasswordError):
        decrypt_pdf(make_pdf("right"), ["wrong"])
