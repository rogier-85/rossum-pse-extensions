"""PDF encryption inspection and decryption."""

from __future__ import annotations

from io import BytesIO

from pypdf import PdfReader, PdfWriter
from pypdf.errors import FileNotDecryptedError, PdfReadError, WrongPasswordError


class InvalidPdfError(Exception):
    """Raised when an attachment declared as PDF cannot be parsed."""


def decrypt_pdf(content: bytes, candidates: list[str]) -> bytes | None:
    """Return an unlocked copy, None if unencrypted, or raise WrongPasswordError."""
    try:
        probe = PdfReader(BytesIO(content), strict=False)
    except (PdfReadError, ValueError) as exc:
        raise InvalidPdfError("The attachment is not a readable PDF") from exc

    if not probe.is_encrypted:
        return None

    # An encrypted PDF may intentionally allow opening with an empty password.
    passwords = [""] + [candidate for candidate in candidates if candidate]
    for password in passwords:
        try:
            reader = PdfReader(BytesIO(content), strict=False, password=password)
            if reader.is_encrypted and not reader.decrypt(password):
                continue
            writer = PdfWriter()
            writer.clone_document_from_reader(reader)
            output = BytesIO()
            writer.write(output)
            return output.getvalue()
        except (WrongPasswordError, FileNotDecryptedError):
            continue
        except (PdfReadError, ValueError) as exc:
            raise InvalidPdfError("The encrypted attachment could not be read") from exc

    raise WrongPasswordError("No email-body password unlocked the PDF")
