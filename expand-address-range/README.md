# Expand address ranges

This paste-ready Rossum Python 3.12 serverless function expands each `line_items`
row with an `item_address_range` into one row per house number, including both
endpoints. For example, `15-30` replaces one row with 16 rows whose
`item_house_number` values are `15` through `30`.

`item_amount_total` is divided by the number of generated rows using decimal
arithmetic. Each share is rounded to two decimal places (half up); the last row
receives the original total minus all preceding shares. For example, `10.00`
across three rows becomes `3.33`, `3.33`, `3.34`. Negative amounts follow the same
rule. If the source has more than two decimal places, the last row retains the
precision needed to preserve its exact total. Missing, empty, invalid, or
non-finite amounts are copied unchanged rather than replaced with invented values.

Every other column value is copied, including the original range, custom fields,
empty values, quantities, and prices. Existing house numbers are
overwritten. Rows remain in their original order, with each expansion ascending.
Leading zeros in range endpoints become ordinary integers (`015-016` → `15`, `16`).
Nonnegative integer ranges allow whitespace around the numbers and hyphen.
Missing, empty, malformed, and reversed ranges leave that row unchanged.

When any row expands, the function rebuilds the table using Rossum table
assignment so appended rows appear in the correct order. All rows then receive
fresh content IDs; cell values are preserved, while extraction positions,
confidence, and validation metadata are not copied. If no row expands, no
operations are returned. Only `item_amount_total` is allocated; other financial
fields are copied unchanged.

## Install

1. Ensure the queue schema contains the `line_items` multivalue table and the
   `item_address_range` and `item_house_number` columns. Use a string field for
   the range; the house-number field may be string or number.
2. Create a **Serverless Function** extension with runtime **Python 3.12**.
3. Paste the complete contents of `expand_address_range.py` into its editor.
4. Select **only** `annotation_content.initialize` and enable schema sideloading
   (`sideload: ["schemas"]`). No settings, secrets, or network access are needed.
5. Connect the required queues and test with a representative annotation.

`hook.example.json` provides the equivalent Hook API body. Replace its queue
URL and code placeholder before using it.

The range remains on every generated row. Running initialization again on
already-expanded content expands it again; do not configure this extension for
user updates or confirmation events.

## Local tests

```sh
python3.12 -m venv .venv
.venv/bin/python -m pip install -e '.[test]'
.venv/bin/python -m pytest
```

Tests exercise actual `txscript` 1.2.0 responses, including remove/add operations,
ordering, custom and empty values, range parsing, and absent tables. Deployment
and testing against a live Rossum tenant are separate steps.

Reference: [Rossum Transaction Scripts](https://elis.rossum.ai/api/docs/openapi/guides/rossum-transaction-scripts/).
