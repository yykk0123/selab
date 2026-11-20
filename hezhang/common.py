"""Shared validation, money arithmetic, identifiers and domain errors."""

import json
import uuid
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation


class DomainError(ValueError):
    """A rejected business operation that can be displayed to the user."""


class AccessDenied(DomainError):
    """The current identity does not have permission for this operation."""


class Conflict(DomainError):
    """The current state differs from the expected state."""


def identifier():
    """Return an opaque globally unique identifier."""
    return uuid.uuid4().hex


def now():
    """Return a sortable UTC timestamp with microsecond precision."""
    return datetime.now(UTC).isoformat()


def timestamp(value):
    """Validate an explicit timezone-aware timestamp and normalize it to UTC."""
    try:
        parsed = datetime.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise DomainError("时间必须是 ISO 8601 格式") from exc
    if parsed.tzinfo is None:
        raise DomainError("时间必须包含时区，例如 +08:00")
    return parsed.astimezone(UTC).isoformat()


def text(value, label, maximum=200):
    """Validate a required short textual value."""
    if not isinstance(value, str) or not value.strip():
        raise DomainError(f"{label}不能为空")
    result = value.strip()
    if len(result) > maximum:
        raise DomainError(f"{label}不能超过 {maximum} 个字符")
    return result


def money(value, allow_zero=False):
    """Convert decimal currency into integer cents without rounding input.

    SQLite integer arithmetic is used for all stored monetary values. The
    technical ceiling is a storage protection limit, not a business threshold.
    """
    if isinstance(value, bool):
        raise DomainError("金额必须是数字")
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise DomainError("金额必须是数字") from exc
    if not number.is_finite():
        raise DomainError("金额必须是有限数字")
    if number < 0 or (number == 0 and not allow_zero):
        raise DomainError("金额必须大于零")
    if number.as_tuple().exponent < -2:
        raise DomainError("金额最多两位小数")
    if number > Decimal("999999999.99"):
        raise DomainError("金额超过当前实现的存储保护上限")
    return int(number * 100)


def currency(cents):
    """Format signed integer cents for display or JSON export."""
    return f"{Decimal(cents) / 100:.2f}"


def encode(value):
    """Serialize deterministic JSON for audit and request comparison."""
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def split_amount(total, participants, mode="equal", values=None):
    """Allocate cents deterministically, assigning residual cents by input order.

    In ratio mode values must be percentages totaling exactly 100. Custom
    mode accepts decimal currency amounts. All modes preserve the exact sum.
    """
    if not participants or len(set(participants)) != len(participants):
        raise DomainError("分摊成员不能为空或重复")
    if mode == "equal":
        base, residual = divmod(total, len(participants))
        return {person: base + (index < residual) for index, person in enumerate(participants)}
    if not isinstance(values, dict) or set(values) != set(participants):
        raise DomainError("必须为每位分摊成员指定数值")
    if mode == "custom":
        result = {person: money(values[person], True) for person in participants}
        if sum(result.values()) != total:
            raise DomainError("自定义分摊合计必须等于总额")
        return result
    if mode != "ratio":
        raise DomainError("分摊方式必须是 equal、ratio 或 custom")
    try:
        ratios = [Decimal(str(values[person])) for person in participants]
    except InvalidOperation as exc:
        raise DomainError("比例必须是数字") from exc
    if any(not ratio.is_finite() or ratio < 0 for ratio in ratios):
        raise DomainError("比例必须为非负有限数")
    if sum(ratios) != 100:
        raise DomainError("比例之和必须为 100")
    amounts = [int(total * ratio / 100) for ratio in ratios]
    residual = total - sum(amounts)
    for index in range(residual):
        amounts[index] += 1
    return dict(zip(participants, amounts, strict=True))
