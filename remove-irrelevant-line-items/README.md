# Remove irrelevant trailing line items

This Rossum Python 3.12 serverless function removes line-item rows that appear
after the invoice's real line items. It is intended for PDFs in which pages from
attachments are included in the same document and are incorrectly extracted as
additional rows.

The extension reads rows from the top of `line_items`, adds each
`item_amount_total`, and stops at the first two-decimal cumulative total equal to
any configured target header field. It keeps that leading group and removes
every row after it.

For example, with an amount due of `30.00`:

| Row | `item_amount_total` | Running total | Result |
| ---: | ---: | ---: | --- |
| 1 | 10.00 | 10.00 | Keep |
| 2 | 20.00 | 30.00 | Keep; target reached |
| 3 | 999.00 | — | Remove |

If the target or a line-item amount is missing or invalid before the target is
reached, or no running total matches exactly, the function leaves the complete
table unchanged and returns no message. Rows after a match are irrelevant and
are not inspected. Negative rows such as discounts are included in the running
total.

## Files

- `remove_irrelevant_line_items.py` — paste-ready serverless function source.
- `hook.example.json` — example Hook API request body.
- `tests/` — local unit and handler tests using a small TxScript test double.

## Install in Rossum

The schema attached to the target queue must contain these exact IDs:

- multivalue: `line_items`
- line-item number field: `item_amount_total`
- one or more header number fields configured as targets (by default,
  `amount_due`)

In Rossum's Extensions UI:

1. Create a **Serverless Function** extension.
2. Select runtime **Python 3.12**.
3. Paste the complete contents of `remove_irrelevant_line_items.py` into the
   code editor.
4. Select only the `annotation_content.initialize` event.
5. In the extension settings, set **Target amount fields** to the schema IDs to
   check, for example `amount_due` and `subtotal`.
6. Connect the extension to the required queues and activate it.
7. Test it against a representative annotation before enabling it in
   production.

The setting is stored as `settings.target_field_ids`. When it is absent, the
function remains backward compatible and checks `amount_due`. Missing or empty
fields in the configured list are skipped. If none of the configured fields has
a usable value, the table is left unchanged. When multiple targets have usable
values, the earliest matching line-item prefix wins.

The function requires no secrets, external credentials, sideloaded objects, or
network access. If creating the hook through the API, replace the queue URL and
the `config.code` placeholder in `hook.example.json`, then send that object as
the body of `POST /api/v1/hooks`.

## Local tests

Python 3.12 or newer is required.

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install -e '.[test]'
.venv/bin/python -m pytest
```

The local tests validate the selection algorithm and the remove operations that
TxScript will produce when the filtered table is returned to Rossum.

## Operational behavior

- The extension runs once, immediately after extraction.
- It considers only a contiguous prefix of the extracted rows; it does not try
  arbitrary combinations.
- It compares the running total with every usable field listed in
  `target_field_ids`.
- Each value is rounded to two decimal places with conventional half-up
  rounding before comparison.
- The first matching prefix wins, including when later rows contain zero.
- A cleaned annotation is safe to process again: no further rows are removed.

Rossum references:

- [Serverless function extensions](https://elis.rossum.ai/api/docs/openapi/guides/extensions/)
- [Rossum Transaction Scripts](https://elis.rossum.ai/api/docs/openapi/guides/rossum-transaction-scripts/)
