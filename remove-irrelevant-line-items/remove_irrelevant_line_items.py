"""Rossum serverless function that removes trailing, irrelevant line items.

The file is intentionally self-contained so its complete contents can be pasted
into a Rossum Python 3.12 serverless function.
"""

from collections.abc import Iterable
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

_CENT = Decimal("0.01")
_DEFAULT_TARGET_FIELD_IDS = ("amount_due",)


def _as_money(value: object) -> Decimal | None:
    """Convert a Rossum number value to a two-decimal monetary value."""
    if value is None or isinstance(value, bool):
        return None

    try:
        amount = Decimal(str(value)).quantize(_CENT, rounding=ROUND_HALF_UP)
    except (InvalidOperation, TypeError, ValueError):
        return None

    if not amount.is_finite():
        return None
    return amount


def find_matching_prefix_length(
    item_amounts: Iterable[object], amount_due: object
) -> int | None:
    """Return the first prefix length whose two-decimal sum equals amount_due.

    ``None`` means that the input cannot be evaluated safely or that no exact
    prefix match exists. Negative values are intentionally included, so the
    function does not stop merely because an intermediate sum exceeds the
    target.
    """
    return find_matching_prefix_length_for_targets(item_amounts, (amount_due,))


def find_matching_prefix_length_for_targets(
    item_amounts: Iterable[object], target_amounts: Iterable[object]
) -> int | None:
    """Return the first prefix length matching any valid target amount."""
    targets = {
        normalized
        for value in target_amounts
        if (normalized := _as_money(value)) is not None
    }
    if not targets:
        return None

    running_total = Decimal("0.00")
    for index, item_amount in enumerate(item_amounts):
        normalized_amount = _as_money(item_amount)
        if normalized_amount is None:
            return None

        running_total += normalized_amount
        if running_total in targets:
            return index + 1

    return None


def _configured_target_field_ids(payload: dict) -> tuple[str, ...]:
    """Read target schema IDs, retaining the old behavior when unset."""
    settings = payload.get("settings")
    if settings is None:
        return _DEFAULT_TARGET_FIELD_IDS
    if not isinstance(settings, dict):
        return ()

    if "target_field_ids" not in settings:
        return _DEFAULT_TARGET_FIELD_IDS

    configured = settings["target_field_ids"]
    if not isinstance(configured, list):
        return ()

    # Preserve configuration order for predictable field access while removing
    # duplicates. Matching itself is based on the earliest line-item prefix.
    field_ids = []
    for field_id in configured:
        if (
            isinstance(field_id, str)
            and field_id.strip()
            and field_id not in field_ids
        ):
            field_ids.append(field_id)
    return tuple(field_ids)


def rossum_hook_request_handler(payload: dict) -> dict:
    """Handle a Rossum ``annotation_content.initialize`` event."""
    # txscript is provided by Rossum's Python 3.12 serverless runtime. Keeping
    # this import inside the handler lets the pure matching logic run locally.
    from txscript import TxScript, is_empty

    transaction = TxScript.from_payload(payload)

    try:
        rows = list(transaction.field.line_items)
    except (AttributeError, TypeError):
        return transaction.hook_response()

    if not rows:
        return transaction.hook_response()

    target_amounts = []
    for field_id in _configured_target_field_ids(payload):
        try:
            target_amount = getattr(transaction.field, field_id)
        except (AttributeError, TypeError):
            continue
        if not is_empty(target_amount):
            target_amounts.append(target_amount)

    if not target_amounts:
        return transaction.hook_response()

    def item_amounts():
        for row in rows:
            try:
                item_amount_total = row.item_amount_total
            except AttributeError:
                yield None
                return

            if is_empty(item_amount_total):
                yield None
                return
            yield item_amount_total

    keep_count = find_matching_prefix_length_for_targets(
        item_amounts(), target_amounts
    )
    if keep_count is None or keep_count == len(rows):
        return transaction.hook_response()

    transaction.field.line_items = rows[:keep_count]
    return transaction.hook_response()
