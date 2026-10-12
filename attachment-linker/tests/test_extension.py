import json
import unittest
import urllib.parse

from extension import (
    RossumApi,
    RossumApiError,
    _field_value,
    _resource_id,
    process_payload,
)


BASE = "https://tenant.rossum.app"
PARENT = f"{BASE}/api/v1/annotations/10"
CHILD_1 = f"{BASE}/api/v1/annotations/11"
CHILD_2 = f"{BASE}/api/v1/annotations/12"
EMAIL = f"{BASE}/api/v1/emails/20"
DOCUMENT = f"{BASE}/api/v1/documents/30"


def datapoint(schema_id, value, normalized_value=None):
    content = {"value": value}
    if normalized_value is not None:
        content["normalized_value"] = normalized_value
    return {"category": "datapoint", "schema_id": schema_id, "content": content}


def payload(document_type="Invoice"):
    return {
        "event": "annotation_content",
        "action": "initialize",
        "base_url": BASE,
        "rossum_authorization_token": "short-lived-token",
        "annotation": {
            "id": 10,
            "url": PARENT,
            "document": DOCUMENT,
            "content": [
                {
                    "category": "section",
                    "schema_id": "basic_info",
                    "children": [
                        datapoint("document_sub_type", document_type),
                    ],
                }
            ],
        },
        "document": {"url": DOCUMENT, "email": EMAIL},
        "settings": {},
    }


class FakeApi:
    def __init__(self, base_url, token, *, email_annotations=None, relations=None):
        self.base_url = base_url
        self.token = token
        self.email_annotations = email_annotations or []
        self.relations = relations or []
        self.posts = []
        self.patches = []
        self.gets = []

    def get(self, url):
        self.gets.append(url)
        if url == EMAIL:
            return {"url": EMAIL, "annotations": self.email_annotations}
        if url == DOCUMENT:
            return {"url": DOCUMENT, "email": EMAIL}
        raise AssertionError(f"Unexpected GET {url}")

    def iter_results(self, url):
        self.gets.append(url)
        yield from self.relations

    def post(self, path, body):
        self.posts.append((path, body))
        return {"url": f"{BASE}/api/v1/relations/99", **body}

    def patch(self, url, body):
        self.patches.append((url, body))
        return {"url": url, **body}


class ExtensionTests(unittest.TestCase):
    def make_factory(self, **kwargs):
        holder = {}

        def factory(base_url, token):
            holder["api"] = FakeApi(base_url, token, **kwargs)
            return holder["api"]

        return factory, holder

    def test_reads_normalized_nested_field_value(self):
        content = [{"children": [datapoint("invoice_id", " raw ", " normalized ")]}]
        self.assertEqual(_field_value(content, "invoice_id"), "normalized")

    def test_extracts_numeric_annotation_id_from_url(self):
        self.assertEqual(
            _resource_id(
                "https://gartner-demo.rossum.app/api/v1/annotations/58390424",
                "annotation",
            ),
            58390424,
        )

    def test_non_invoice_does_nothing_without_api_access(self):
        factory, holder = self.make_factory()
        result = process_payload(payload(document_type="Credit note"), factory)
        self.assertEqual(result, {"messages": [], "operations": []})
        self.assertNotIn("api", holder)

    def test_invoice_creates_attachment_relation_to_other_email_annotations(self):
        factory, holder = self.make_factory(
            email_annotations=[PARENT, CHILD_1, CHILD_1, CHILD_2]
        )
        result = process_payload(payload(), factory)
        api = holder["api"]
        self.assertEqual(len(api.posts), 1)
        self.assertEqual(
            api.posts[0],
            (
                "/api/v1/relations",
                {
                    "type": "attachment",
                    "key": None,
                    "parent": PARENT,
                    "annotations": [CHILD_1, CHILD_2],
                },
            ),
        )
        self.assertIn("created", result["messages"][0]["content"])
        relation_lookup = next(
            url for url in api.gets if "/api/v1/relations?" in url
        )
        self.assertIn("annotation=10", relation_lookup)
        self.assertNotIn(urllib.parse.quote(PARENT, safe=""), relation_lookup)

    def test_existing_relation_is_unchanged_on_retry(self):
        relation = {
            "url": f"{BASE}/api/v1/relations/99",
            "type": "attachment",
            "key": None,
            "parent": PARENT,
            "annotations": [CHILD_1],
        }
        factory, holder = self.make_factory(
            email_annotations=[PARENT, CHILD_1], relations=[relation]
        )
        process_payload(payload(), factory)
        self.assertEqual(holder["api"].posts, [])
        self.assertEqual(holder["api"].patches, [])

    def test_old_custom_relation_does_not_block_attachment_relation(self):
        old_custom_relation = {
            "url": f"{BASE}/api/v1/relations/98",
            "type": "custom",
            "name": "Invoice INV-42",
            "parent": PARENT,
            "annotations": [CHILD_1],
        }
        factory, holder = self.make_factory(
            email_annotations=[PARENT, CHILD_1], relations=[old_custom_relation]
        )
        process_payload(payload(), factory)
        self.assertEqual(len(holder["api"].posts), 1)
        self.assertEqual(holder["api"].posts[0][1]["type"], "attachment")
        self.assertNotIn("name", holder["api"].posts[0][1])

    def test_sideloaded_relation_avoids_relation_list_request(self):
        relation = {
            "url": f"{BASE}/api/v1/relations/99",
            "type": "attachment",
            "key": None,
            "parent": PARENT,
            "annotations": [CHILD_1],
        }
        request = payload()
        request["relations"] = [relation]
        factory, holder = self.make_factory(email_annotations=[PARENT, CHILD_1])
        process_payload(request, factory)
        relation_list_gets = [
            url for url in holder["api"].gets if "/api/v1/relations?" in url
        ]
        self.assertEqual(relation_list_gets, [])

    def test_existing_relation_is_extended_when_email_has_new_annotation(self):
        relation_url = f"{BASE}/api/v1/relations/99"
        relation = {
            "url": relation_url,
            "type": "attachment",
            "key": None,
            "parent": PARENT,
            "annotations": [CHILD_1],
        }
        factory, holder = self.make_factory(
            email_annotations=[PARENT, CHILD_1, CHILD_2], relations=[relation]
        )
        process_payload(payload(), factory)
        self.assertEqual(
            holder["api"].patches,
            [(relation_url, {"annotations": [CHILD_1, CHILD_2]})],
        )

    def test_document_without_email_returns_warning(self):
        class NoEmailApi(FakeApi):
            def get(self, url):
                if url == DOCUMENT:
                    return {"url": DOCUMENT, "email": None}
                return super().get(url)

        holder = {}

        def factory(base_url, token):
            holder["api"] = NoEmailApi(base_url, token)
            return holder["api"]

        request = payload()
        request["document"].pop("email")
        result = process_payload(request, factory)
        self.assertIn("not linked to an email", result["messages"][0]["content"])
        self.assertEqual(holder["api"].posts, [])

    def test_custom_document_type_field_setting(self):
        request = payload()
        request["annotation"]["content"][0]["children"] = [
            datapoint("kind", "Invoice"),
        ]
        request["settings"] = {
            "document_sub_type_field": "kind",
        }
        factory, holder = self.make_factory(email_annotations=[PARENT, CHILD_1])
        process_payload(request, factory)
        self.assertEqual(holder["api"].posts[0][1]["type"], "attachment")

    def test_child_relation_sideload_avoids_relation_list_request(self):
        relation = {
            "url": f"{BASE}/api/v1/relations/99",
            "type": "attachment",
            "key": None,
            "parent": PARENT,
            "annotations": [CHILD_1],
        }
        request = payload()
        request["child_relations"] = [relation]
        factory, holder = self.make_factory(email_annotations=[PARENT, CHILD_1])
        process_payload(request, factory)
        relation_list_gets = [
            url for url in holder["api"].gets if "/api/v1/relations?" in url
        ]
        self.assertEqual(relation_list_gets, [])


class FakeResponse:
    def __init__(self, value):
        self.value = value

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return None

    def read(self):
        return json.dumps(self.value).encode()


class RossumApiTests(unittest.TestCase):
    def test_rejects_cross_tenant_url_before_opening(self):
        opened = []
        api = RossumApi(BASE, "token", opener=lambda request, timeout: opened.append(request))
        with self.assertRaises(RossumApiError):
            api.get("https://attacker.example/api/v1/emails/1")
        self.assertEqual(opened, [])

    def test_follows_opaque_pagination_url(self):
        next_url = f"{BASE}/api/v1/relations?cursor=opaque"
        responses = iter(
            [
                {"results": [{"id": 1}], "pagination": {"next": next_url}},
                {"results": [{"id": 2}], "pagination": {"next": None}},
            ]
        )
        opened = []

        def opener(request, timeout):
            opened.append(request.full_url)
            return FakeResponse(next(responses))

        api = RossumApi(BASE, "token", opener=opener)
        results = list(api.iter_results(f"{BASE}/api/v1/relations"))
        self.assertEqual(results, [{"id": 1}, {"id": 2}])
        self.assertEqual(opened[1], next_url)


if __name__ == "__main__":
    unittest.main()
