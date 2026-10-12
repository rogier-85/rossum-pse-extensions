import json
import unittest
import urllib.error
from unittest.mock import MagicMock, patch

import wht_certificate_linker as linker


BASE_URL = "https://example.rossum.app"
CURRENT_URL = f"{BASE_URL}/api/v1/annotations/100"
MATCH_URL = f"{BASE_URL}/api/v1/annotations/200"
QUEUE_URL = f"{BASE_URL}/api/v1/queues/20"
RELATION_URL = f"{BASE_URL}/api/v1/relations/30"


def datapoint(schema_id, value, datapoint_id):
    return {
        "id": datapoint_id,
        "schema_id": schema_id,
        "category": "datapoint",
        "content": {"value": value},
    }


def current_content(supplier="SUP-1", coa="COA-1", rate="old"):
    return [
        {
            "id": 1,
            "schema_id": "invoice_section",
            "category": "section",
            "children": [
                datapoint("supplier_match", supplier, 11),
                datapoint("coa_match", coa, 12),
                datapoint("wht_rate", rate, 13),
            ],
        }
    ]


def source_content(first_rate="10", second_rate="99"):
    rows = []
    if first_rate is not None:
        rows.append(
            {
                "id": 31,
                "schema_id": "item",
                "category": "tuple",
                "children": [datapoint("item_wht_rate", first_rate, 32)],
            }
        )
    if second_rate is not None:
        rows.append(
            {
                "id": 33,
                "schema_id": "item",
                "category": "tuple",
                "children": [datapoint("item_wht_rate", second_rate, 34)],
            }
        )
    return [
        {
            "id": 30,
            "schema_id": "items",
            "category": "multivalue",
            "children": rows,
        }
    ]


def payload(content=None, **overrides):
    value = {
        "event": "annotation_content",
        "action": "started",
        "base_url": BASE_URL,
        "rossum_authorization_token": "temporary-token",
        "settings": {"target_queue_url": QUEUE_URL},
        "annotation": {"id": 100, "url": CURRENT_URL, "content": content or current_content()},
    }
    value.update(overrides)
    return value


def relation_list(relations):
    return {"pagination": {"next": None}, "results": relations}


class FakeClient:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []
        self.origin = ("https", "example.rossum.app")

    def request(self, method, path, body=None, headers=None):
        self.calls.append((method, path, body, headers or {}))
        if not self.responses:
            raise AssertionError(f"Unexpected request: {method} {path}")
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        if isinstance(response, tuple):
            return response
        return response, {}


class HandlerTests(unittest.TestCase):
    def run_handler(self, responses, test_payload=None):
        fake = FakeClient(responses)
        with patch.object(linker, "RossumApiClient", return_value=fake):
            result = linker.rossum_hook_request_handler(test_payload or payload())
        self.assertEqual(fake.responses, [])
        return result, fake.calls

    def test_unique_match_creates_relation_and_overwrites_rate(self):
        result, calls = self.run_handler(
            [
                {"results": [{"url": MATCH_URL, "created_at": "2026-01-02"}]},
                {"content": source_content("10", "99")},
                relation_list([]),
                {"id": 30},
            ]
        )

        self.assertEqual(
            result,
            {
                "messages": [],
                "operations": [
                    {
                        "op": "replace",
                        "id": 13,
                        "value": {"content": {"value": "10"}},
                    }
                ],
            },
        )
        self.assertEqual(
            calls[0][0:2],
            ("POST", "/annotations/search?ordering=-created_at&page_size=1"),
        )
        search_terms = calls[0][2]["query"]["$and"]
        self.assertIn({"queue": {"$eq": QUEUE_URL}}, search_terms)
        self.assertIn({"field.supplier_match.string": {"$eq": "SUP-1"}}, search_terms)
        self.assertIn({"field.coa_match.string": {"$eq": "COA-1"}}, search_terms)
        self.assertFalse(any("status" in term for term in search_terms))
        self.assertEqual(calls[-1][0:2], ("POST", "/relations"))
        self.assertEqual(
            calls[-1][2],
            {
                "type": "attachment",
                "key": None,
                "parent": CURRENT_URL,
                "annotations": [MATCH_URL],
            },
        )

    def test_zero_rate_is_preserved_and_later_rows_are_ignored(self):
        result, _ = self.run_handler(
            [
                {"results": [{"url": MATCH_URL}]},
                {"content": source_content(0, "99")},
                relation_list([]),
                {"id": 30},
            ]
        )
        self.assertEqual(result["operations"][0]["value"]["content"]["value"], "0")

    def test_existing_identical_relation_is_noop(self):
        relation = {
            "id": 30,
            "url": RELATION_URL,
            "type": "attachment",
            "parent": CURRENT_URL,
            "annotations": [MATCH_URL],
        }
        result, calls = self.run_handler(
            [
                {"results": [{"url": MATCH_URL}]},
                {"content": source_content()},
                relation_list([relation]),
            ]
        )
        self.assertEqual(result["operations"][0]["id"], 13)
        self.assertEqual(len(calls), 3)

    def test_existing_relation_appends_with_etag(self):
        other_url = f"{BASE_URL}/api/v1/annotations/201"
        relation = {"id": 30, "url": RELATION_URL, "annotations": [other_url]}
        result, calls = self.run_handler(
            [
                {"results": [{"url": MATCH_URL}]},
                {"content": source_content()},
                relation_list([relation]),
                (relation, {"ETag": '"v1"'}),
                {**relation, "annotations": [other_url, MATCH_URL]},
            ]
        )
        self.assertEqual(result["messages"], [])
        self.assertEqual(calls[-1][0:2], ("PATCH", RELATION_URL))
        self.assertEqual(calls[-1][2], {"annotations": [other_url, MATCH_URL]})
        self.assertEqual(calls[-1][3], {"If-Match": '"v1"'})

    def test_etag_conflict_refetches_once(self):
        relation = {"id": 30, "url": RELATION_URL, "annotations": []}
        result, calls = self.run_handler(
            [
                {"results": [{"url": MATCH_URL}]},
                {"content": source_content()},
                relation_list([relation]),
                (relation, {"ETag": '"v1"'}),
                linker.RossumApiError("conflict", 412),
                ({**relation, "annotations": [MATCH_URL]}, {"ETag": '"v2"'}),
            ]
        )
        self.assertEqual(result["messages"], [])
        self.assertEqual([call[0] for call in calls].count("PATCH"), 1)

    def test_create_conflict_relists_and_accepts_concurrent_relation(self):
        relation = {"id": 30, "url": RELATION_URL, "annotations": [MATCH_URL]}
        result, calls = self.run_handler(
            [
                {"results": [{"url": MATCH_URL}]},
                {"content": source_content()},
                relation_list([]),
                linker.RossumApiError("conflict", 409),
                relation_list([relation]),
            ]
        )
        self.assertEqual(result["messages"], [])
        self.assertEqual([call[0] for call in calls].count("POST"), 2)

    def test_api_failure_returns_visible_error_without_operation(self):
        result, _ = self.run_handler([linker.RossumApiError("request failed", 401)])
        self.assertEqual(result["operations"], [])
        self.assertEqual(result["messages"][0]["type"], "error")
        self.assertIn("HTTP 401", result["messages"][0]["content"])

    def test_relation_write_failure_returns_error_without_operation(self):
        result, _ = self.run_handler(
            [
                {"results": [{"url": MATCH_URL}]},
                {"content": source_content()},
                relation_list([]),
                linker.RossumApiError("request failed", 403),
            ]
        )
        self.assertEqual(result["operations"], [])
        self.assertEqual(result["messages"][0]["type"], "error")
        self.assertIn("HTTP 403", result["messages"][0]["content"])

    def test_malformed_search_response_returns_error(self):
        result, _ = self.run_handler([{"unexpected": []}])
        self.assertEqual(result["operations"], [])
        self.assertEqual(result["messages"][0]["type"], "error")

    def test_no_match_is_silent(self):
        result, _ = self.run_handler([{"results": []}])
        self.assertEqual(result, {"messages": [], "operations": []})

    def test_empty_table_is_silent(self):
        result, _ = self.run_handler(
            [{"results": [{"url": MATCH_URL}]}, {"content": source_content(None, None)}]
        )
        self.assertEqual(result, {"messages": [], "operations": []})

    def test_missing_rate_is_silent(self):
        source = [
            {
                "schema_id": "items",
                "category": "multivalue",
                "children": [{"schema_id": "item", "category": "tuple", "children": []}],
            }
        ]
        result, _ = self.run_handler(
            [{"results": [{"url": MATCH_URL}]}, {"content": source}]
        )
        self.assertEqual(result, {"messages": [], "operations": []})

    def test_missing_current_fields_are_silent_without_api_calls(self):
        scenarios = [
            current_content(supplier=" "),
            current_content(coa=""),
            [datapoint("supplier_match", "SUP-1", 11), datapoint("coa_match", "COA-1", 12)],
        ]
        for content in scenarios:
            with self.subTest(content=content):
                result, calls = self.run_handler([], payload(content))
                self.assertEqual(result, {"messages": [], "operations": []})
                self.assertEqual(calls, [])

    def test_missing_configuration_returns_visible_error(self):
        bad_payload = payload()
        bad_payload["settings"] = {}
        result, calls = self.run_handler([], bad_payload)
        self.assertEqual(calls, [])
        self.assertEqual(result["operations"], [])
        self.assertIn("target_queue_url", result["messages"][0]["content"])

    def test_missing_token_returns_visible_error(self):
        bad_payload = payload()
        bad_payload["rossum_authorization_token"] = None
        result, calls = self.run_handler([], bad_payload)
        self.assertEqual(calls, [])
        self.assertEqual(result["operations"], [])
        self.assertIn("rossum_authorization_token", result["messages"][0]["content"])

    def test_wrong_event_is_silent(self):
        result, calls = self.run_handler([], payload(event="annotation_status"))
        self.assertEqual(calls, [])
        self.assertEqual(result, {"messages": [], "operations": []})


class ApiClientTests(unittest.TestCase):
    def test_malformed_json_is_wrapped(self):
        response = MagicMock()
        response.read.return_value = b"not-json"
        response.headers.items.return_value = []
        response.__enter__.return_value = response
        response.__exit__.return_value = False
        client = linker.RossumApiClient(BASE_URL, "token")
        with patch("urllib.request.urlopen", return_value=response):
            with self.assertRaisesRegex(linker.RossumApiError, "malformed JSON"):
                client.request("GET", "/queues")

    def test_http_error_is_sanitized(self):
        error = urllib.error.HTTPError(
            f"{BASE_URL}/api/v1/queues", 403, "Forbidden", {}, None
        )
        client = linker.RossumApiClient(BASE_URL, "token")
        with patch("urllib.request.urlopen", side_effect=error):
            with self.assertRaises(linker.RossumApiError) as captured:
                client.request("GET", "/queues")
        self.assertEqual(captured.exception.status, 403)
        self.assertNotIn("Forbidden", str(captured.exception))

    def test_cross_host_pagination_url_is_rejected(self):
        client = linker.RossumApiClient(BASE_URL, "token")
        with self.assertRaisesRegex(linker.RossumApiError, "another host"):
            client.request("GET", "https://attacker.invalid/api/v1/relations")


if __name__ == "__main__":
    unittest.main()
