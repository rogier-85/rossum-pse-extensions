from __future__ import annotations

import asyncio

import pytest

from app.models import EmailReceivedPayload
from app.service import AttachmentProcessingError, process_email, unlocked_filename
from tests.conftest import assert_unlocked_pdf


class FakeRossumClient:
    def __init__(self, documents: dict[str, bytes], existing: dict[str, str] | None = None):
        self.documents = documents
        self.existing = existing or {}
        self.created: list[tuple[str, str, bytes]] = []

    async def download_document(self, document_url: str) -> bytes:
        return self.documents[document_url]

    async def find_unlocked_child(self, parent_url: str) -> str | None:
        return self.existing.get(parent_url)

    async def create_unlocked_child(
        self, parent_url: str, filename: str, content: bytes
    ) -> str:
        self.created.append((parent_url, filename, content))
        return f"https://tenant.rossum.app/api/v1/documents/new-{len(self.created)}"


def payload(files: list[dict], plain: str = "", html: str = "") -> EmailReceivedPayload:
    return EmailReceivedPayload.model_validate(
        {
            "event": "email",
            "action": "received",
            "base_url": "https://tenant.rossum.app/api",
            "rossum_authorization_token": "temporary",
            "files": files,
            "body": {"body_text_plain": plain, "body_text_html": html},
        }
    )


def pdf_file(identifier: str, name: str = "invoice.pdf") -> dict:
    return {
        "id": identifier,
        "filename": name,
        "mime_type": "application/pdf",
        "document": f"https://tenant.rossum.app/api/v1/documents/{identifier}",
    }


def test_unlocks_protected_and_passes_other_attachments(make_pdf) -> None:
    protected = pdf_file("1", "folder\\Invoice.PDF")
    plain = pdf_file("2", "plain.pdf")
    image = {
        "id": "3",
        "filename": "logo.png",
        "mime_type": "image/png",
        "document": "https://tenant.rossum.app/api/v1/documents/3",
    }
    client = FakeRossumClient(
        {
            protected["document"]: make_pdf("Alpha", pages=2),
            plain["document"]: make_pdf(pages=1),
        }
    )

    result = asyncio.run(process_email(payload([protected, plain, image], "Password: Alpha"), client))

    assert result.files == [
        {"id": "2", "values": []},
        {"id": "3", "values": []},
    ]
    assert result.additional_files == [
        {
            "document": "https://tenant.rossum.app/api/v1/documents/new-1",
            "import_document": True,
            "values": [],
        }
    ]
    assert client.created[0][0] == protected["document"]
    assert client.created[0][1] == "Invoice_unlocked.pdf"
    assert_unlocked_pdf(client.created[0][2], 2)


def test_multiple_protected_pdfs_can_use_different_passwords(make_pdf) -> None:
    first, second = pdf_file("1"), pdf_file("2")
    client = FakeRossumClient(
        {
            first["document"]: make_pdf("one"),
            second["document"]: make_pdf("two"),
        }
    )
    result = asyncio.run(
        process_email(payload([first, second], "Password: one\nPIN: two"), client)
    )
    assert len(result.additional_files) == 2
    assert len(client.created) == 2


def test_bad_password_causes_no_partial_writes(make_pdf) -> None:
    first, second = pdf_file("1"), pdf_file("2")
    client = FakeRossumClient(
        {
            first["document"]: make_pdf("known"),
            second["document"]: make_pdf("missing"),
        }
    )
    with pytest.raises(AttachmentProcessingError):
        asyncio.run(process_email(payload([first, second], "Password: known"), client))
    assert client.created == []


def test_unprotected_only_performs_no_writes(make_pdf) -> None:
    attachment = pdf_file("1")
    client = FakeRossumClient({attachment["document"]: make_pdf()})
    result = asyncio.run(process_email(payload([attachment]), client))
    assert result.files == [{"id": "1", "values": []}]
    assert result.additional_files == []
    assert client.created == []


def test_retry_reuses_existing_child(make_pdf) -> None:
    attachment = pdf_file("1")
    existing_url = "https://tenant.rossum.app/api/v1/documents/99"
    client = FakeRossumClient(
        {attachment["document"]: make_pdf("secret")},
        {attachment["document"]: existing_url},
    )
    result = asyncio.run(process_email(payload([attachment], "Password: secret"), client))
    assert result.additional_files[0]["document"] == existing_url
    assert client.created == []


def test_unlocked_filename_removes_paths() -> None:
    assert unlocked_filename("../../invoice.pdf") == "invoice_unlocked.pdf"
    assert unlocked_filename("C:\\mail\\bill.PDF") == "bill_unlocked.pdf"
