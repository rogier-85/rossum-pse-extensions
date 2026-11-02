from copy import deepcopy
from decimal import Decimal

import pytest

from expand_address_range import parse_range, rossum_hook_request_handler


COLUMNS = {
    "item_address_range": "string",
    "item_house_number": "string",
    "item_description": "string",
    "item_amount_total": "number",
    "custom_empty": "string",
    "custom_code": "string",
}


def make_payload(rows, *, table_present=True, house_type="string"):
    columns = {**COLUMNS, "item_house_number": house_type}
    schema = [{"id": "section", "category": "section", "children": []}]
    content = [{"id": 1, "schema_id": "section", "category": "section", "children": []}]
    if table_present:
        schema[0]["children"].append({
            "id": "line_items", "category": "multivalue",
            "children": {"id": "item", "category": "tuple", "children": [
                {"id": key, "category": "datapoint", "type": kind}
                for key, kind in columns.items()
            ]},
        })
        content[0]["children"].append({
            "id": 2, "schema_id": "line_items", "category": "multivalue",
            "children": [
                {"id": 100 + i * 10, "schema_id": "item", "category": "tuple", "children": [
                    {"id": 101 + i * 10 + j, "schema_id": key, "category": "datapoint",
                     "content": {"value": row.get(key, ""), "normalized_value": ""}}
                    for j, key in enumerate(columns) if key in row
                ]}
                for i, row in enumerate(rows)
            ],
        })
    return {"event": "annotation_content", "action": "initialize",
            "schemas": [{"content": schema}], "annotation": {"content": content}}


def added_rows(response):
    return [
        {cell["schema_id"]: cell["content"]["value"] for cell in op["value"]}
        for op in response["operations"] if op["op"] == "add"
    ]


@pytest.mark.parametrize("value", [None, "", " ", "abc", "30-15", "-1-3", "1.5-3", "1–3", "1-2-3", 12])
def test_invalid_range(value):
    assert parse_range(value) is None


@pytest.mark.parametrize("value,expected", [("15-30", list(range(15, 31))), (" 0 - 2 ", [0, 1, 2]), ("15-15", [15])])
def test_valid_range(value, expected):
    assert list(parse_range(value)) == expected


@pytest.mark.parametrize("house_type", ["string", "number"])
def test_expansion_with_real_txscript(house_type):
    source = {"item_address_range": "15-30", "item_house_number": "999",
              "item_description": "Service", "item_amount_total": "0012.50",
              "custom_empty": "", "custom_code": "ABC"}
    response = rossum_hook_request_handler(make_payload([source], house_type=house_type))
    rows = added_rows(response)
    assert len(rows) == 16
    assert rows == [
        {**source, "item_house_number": str(n), "item_amount_total": "0.80" if n == 30 else "0.78"}
        for n in range(15, 31)
    ]
    assert response["messages"] == []
    assert [op for op in response["operations"] if op["op"] == "remove"] == [{"op": "remove", "id": 100}]
    for op in response["operations"]:
        if op["op"] == "add":
            assert op["id"] == 2
            assert all("id" not in cell for cell in op["value"])
    rows[0]["item_description"] = "Changed"
    assert rows[1]["item_description"] == "Service"


def test_mixed_rows_keep_order_and_values():
    sources = [
        {"item_description": "before"},
        {"item_address_range": " 0 - 1 ", "item_description": "first"},
        {"item_address_range": "bad", "item_description": "middle"},
        {"item_address_range": "4-4", "item_description": "second"},
        {"item_address_range": "", "item_description": "after"},
    ]
    rows = added_rows(rossum_hook_request_handler(make_payload(sources)))
    assert [r["item_description"] for r in rows] == ["before", "first", "first", "middle", "second", "after"]
    assert [r["item_house_number"] for r in rows] == ["", "0", "1", "", "4", ""]
    assert rows[1]["item_address_range"] == " 0 - 1 "


@pytest.mark.parametrize("sources,table_present", [([], False), ([], True), ([{}], True), ([{"item_address_range": "3-1"}], True), ([{"item_address_range": ""}], True)])
def test_no_expansion_returns_no_operations(sources, table_present):
    response = rossum_hook_request_handler(make_payload(sources, table_present=table_present))
    assert response["operations"] == []


def test_repeat_initialization_expands_again():
    rows = added_rows(rossum_hook_request_handler(make_payload([{"item_address_range": "1-2"}])))
    response = rossum_hook_request_handler(make_payload(deepcopy(rows)))
    assert len(added_rows(response)) == 4


@pytest.mark.parametrize("total,expected", [
    ("10.00", ["3.33", "3.33", "3.34"]),
    ("10.01", ["3.34", "3.34", "3.33"]),
    ("12.00", ["4.00", "4.00", "4.00"]),
    ("-10.00", ["-3.33", "-3.33", "-3.34"]),
    ("0", ["0.00", "0.00", "0.00"]),
    ("0.01", ["0.00", "0.00", "0.01"]),
    ("1.005", ["0.34", "0.34", "0.325"]),
])
def test_amount_allocation_preserves_original_total(total, expected):
    rows = added_rows(rossum_hook_request_handler(make_payload([
        {"item_address_range": "1-3", "item_amount_total": total}
    ])))
    assert [row["item_amount_total"] for row in rows] == expected
    assert sum(Decimal(row["item_amount_total"]) for row in rows) == Decimal(total)


@pytest.mark.parametrize("total", ["", "invalid", "NaN", "Infinity"])
def test_invalid_amount_remains_unchanged(total):
    rows = added_rows(rossum_hook_request_handler(make_payload([
        {"item_address_range": "1-2", "item_amount_total": total}
    ])))
    assert [row["item_amount_total"] for row in rows] == [total, total]


def test_single_row_and_unranged_amounts():
    rows = added_rows(rossum_hook_request_handler(make_payload([
        {"item_address_range": "1-1", "item_amount_total": "10.01"},
        {"item_address_range": "", "item_amount_total": "0012.50"},
    ])))
    assert [row["item_amount_total"] for row in rows] == ["10.01", "0012.50"]
