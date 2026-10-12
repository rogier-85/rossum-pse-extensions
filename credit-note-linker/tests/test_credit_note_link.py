import unittest
from unittest.mock import patch

import credit_note_link as extension


def datapoint(schema_id, value, datapoint_id=1):
    return {
        "category": "datapoint",
        "schema_id": schema_id,
        "id": datapoint_id,
        "content": {"value": value},
    }


def payload(document_type="credit_note", original_number="INV-42"):
    return {
        "base_url": "https://example.rossum.app",
        "rossum_authorization_token": "secret",
        "settings": {},
        "annotation": {
            "id": 200,
            "url": "https://example.rossum.app/api/v1/annotations/200",
            "queue": "https://example.rossum.app/api/v1/queues/10",
            "content": [
                {"category": "section", "children": [
                    datapoint("document_type", document_type, 11),
                    datapoint("original_document_id", original_number, 12),
                ]}
            ],
        },
    }


INVOICE = {"id": 100, "url": "https://example.rossum.app/api/v1/annotations/100"}


class HandlerTests(unittest.TestCase):
    @patch.object(extension, "_api_request")
    def test_non_credit_note_is_ignored(self, api):
        result = extension.rossum_hook_request_handler(payload("tax_invoice"))
        self.assertEqual(result, {"messages": [], "operations": []})
        api.assert_not_called()

    @patch.object(extension, "_api_request")
    def test_creates_relation_for_unique_exact_match(self, api):
        api.side_effect = [
            {"results": [INVOICE]},
            {"results": []},
            {"id": 9},
        ]
        result = extension.rossum_hook_request_handler(payload())
        self.assertIn("was created", result["messages"][0]["content"])
        search_body = api.call_args_list[0].args[3]
        clauses = search_body["query"]["$and"]
        self.assertIn({"field.document_id.string": {"$eq": "INV-42"}}, clauses)
        relation_body = api.call_args_list[2].args[3]
        self.assertEqual(relation_body["parent"], INVOICE["url"])
        self.assertEqual(relation_body["annotations"], [payload()["annotation"]["url"]])

    @patch.object(extension, "_api_request")
    def test_retry_is_idempotent_when_relation_exists(self, api):
        credit_url = payload()["annotation"]["url"]
        api.side_effect = [
            {"results": [INVOICE]},
            {"results": [{"id": 9, "key": None, "annotations": [credit_url]}]},
        ]
        result = extension.rossum_hook_request_handler(payload())
        self.assertIn("already exists", result["messages"][0]["content"])
        self.assertEqual(api.call_count, 2)

    @patch.object(extension, "_api_request")
    def test_adds_to_existing_parent_relation(self, api):
        api.side_effect = [
            {"results": [INVOICE]},
            {"results": [{"id": 9, "key": None, "annotations": ["old"]}]},
            {},
        ]
        extension.rossum_hook_request_handler(payload())
        self.assertEqual(api.call_args_list[2].args[1:3], ("PATCH", "/api/v1/relations/9"))
        self.assertEqual(api.call_args_list[2].args[3], {"annotations": ["old", payload()["annotation"]["url"]]})

    @patch.object(extension, "_api_request")
    def test_ambiguous_invoice_number_does_not_link(self, api):
        api.return_value = {"results": [INVOICE, {"id": 101, "url": "other"}]}
        result = extension.rossum_hook_request_handler(payload())
        self.assertIn("Found 2 invoices", result["messages"][0]["content"])
        self.assertEqual(api.call_count, 1)

    @patch.object(extension, "_api_request")
    def test_missing_original_number_does_not_search(self, api):
        result = extension.rossum_hook_request_handler(payload(original_number="  "))
        self.assertIn("no original invoice number", result["messages"][0]["content"])
        api.assert_not_called()


if __name__ == "__main__":
    unittest.main()

