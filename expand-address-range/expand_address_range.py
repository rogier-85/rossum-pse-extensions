"""Paste-ready Rossum Python 3.12 function for expanding address ranges."""

import re
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation, localcontext


def split_amount(value: object, count: int) -> list[str] | None:
    """Split a monetary total, allocating the rounding difference to the last row."""
    try:
        total = Decimal(str(value))
        if not total.is_finite():
            return None
        with localcontext() as context:
            context.prec = max(
                28,
                len(total.as_tuple().digits) + abs(total.adjusted()) + len(str(count)) + 4,
            )
            share = (total / count).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            last = total - share * (count - 1)
            last_value = (
                format(last, ".2f")
                if last == last.quantize(Decimal("0.01"))
                else format(last, "f")
            )
            return [format(share, "f")] * (count - 1) + [last_value]
    except (InvalidOperation, ValueError):
        return None


def parse_range(value: object) -> range | None:
    """Parse an inclusive, nonnegative integer range, or return None."""
    if not isinstance(value, str):
        return None
    match = re.fullmatch(r"\s*([0-9]+)\s*-\s*([0-9]+)\s*", value)
    if not match:
        return None
    start, end = map(int, match.groups())
    return range(start, end + 1) if start <= end else None


def _find_table(nodes: list[dict]) -> dict | None:
    for node in nodes:
        if node.get("schema_id") == "line_items":
            return node
        children = node.get("children", [])
        if isinstance(children, list):
            found = _find_table(children)
            if found is not None:
                return found
    return None


def rossum_hook_request_handler(payload: dict) -> dict:
    """Replace ranged line items on annotation_content.initialize."""
    from txscript import TxScript

    # Snapshot raw values before TxScript processes the content. Using the raw
    # strings also preserves number formatting and invalid/empty cell values.
    table = _find_table(payload["annotation"]["content"])
    output = []
    changed = False
    if table is not None:
        for row in table.get("children", []):
            values = {
                cell["schema_id"]: cell.get("content", {}).get("value", "")
                for cell in row.get("children", [])
            }
            numbers = parse_range(values.get("item_address_range"))
            if numbers is None:
                output.append(values)
            else:
                changed = True
                amounts = split_amount(values.get("item_amount_total"), len(numbers))
                for index, number in enumerate(numbers):
                    expanded = {**values, "item_house_number": str(number)}
                    if amounts is not None:
                        expanded["item_amount_total"] = amounts[index]
                    output.append(expanded)

    transaction = TxScript.from_payload(payload)
    if changed:
        # Adds append in Rossum. Rebuild in order, with no copied content IDs.
        del transaction.field.line_items[:]
        transaction.field.line_items = output
    return transaction.hook_response()
