"""Expense validation, approval and version-preserving amendments."""

import json
from datetime import datetime

from .common import (
    Conflict,
    DomainError,
    encode,
    identifier,
    money,
    now,
    split_amount,
    text,
    timestamp,
)
from .ledger_service import LedgerService


def allocation_pairs(contributions, shares):
    """Allocate each participant's share proportionally across actual payers.

    This keeps original per-expense relationships instead of netting debts
    between expenses. Largest fractional remainders receive residual cents;
    ties follow contributor identifier order for reproducibility.
    """
    total = sum(contributions.values())
    result = {}
    for debtor, share in sorted(shares.items()):
        amounts = {creditor: share * amount // total for creditor, amount in contributions.items()}
        residual = share - sum(amounts.values())
        order = sorted(
            contributions,
            key=lambda person: (-(share * contributions[person] % total), person),
        )
        for creditor in order[:residual]:
            amounts[creditor] += 1
        for creditor, amount in amounts.items():
            if amount and creditor != debtor:
                result[(debtor, creditor)] = amount
    return result


class ExpenseService:
    """Coordinate validation, review and debt generation in one transaction."""

    def __init__(self, repository):
        self.repo = repository
        self.ledgers = LedgerService(repository)

    def validate(self, ledger_id, data):
        """Build a normalized expense payload without trusting client totals."""
        if not isinstance(data, dict):
            raise DomainError("账单必须是 JSON 对象")
        amount = money(data.get("amount"))
        title = text(data.get("title"), "账单标题")
        category = self.repo.one(
            "SELECT * FROM categories WHERE id = ? AND ledger_id = ?",
            (data.get("category_id"), ledger_id),
        )
        kind = data.get("kind", "EXPENSE")
        if kind not in ("EXPENSE", "INCOME"):
            raise DomainError("收支类型必须是 EXPENSE 或 INCOME")
        occurred_at = timestamp(data.get("occurred_at", now()))
        raw_contributions = data.get("contributions")
        if not isinstance(raw_contributions, dict) or not raw_contributions:
            raise DomainError("必须指定垫付人及金额")
        contributions = {person: money(value, True) for person, value in raw_contributions.items()}
        if sum(contributions.values()) != amount:
            raise DomainError("垫付合计必须等于账单总额")
        participants = data.get("participants")
        if not isinstance(participants, list):
            raise DomainError("参与人必须是列表")
        if any(not isinstance(person, str) for person in participants):
            raise DomainError("参与人标识必须是字符串")
        shares = split_amount(amount, participants, data.get("mode", "equal"), data.get("values"))
        for person in set(contributions) | set(shares):
            self.ledgers.member(person, ledger_id)
        return {
            "amount": amount,
            "title": title,
            "category_id": category["id"],
            "kind": kind,
            "occurred_at": occurred_at,
            "contributions": contributions,
            "shares": shares,
        }

    def reasons(self, ledger_id, data, created_at, exclude=None):
        """Collect all review reasons with exact inclusive duplicate boundaries."""
        category = self.repo.one("SELECT * FROM categories WHERE id = ?", (data["category_id"],))
        reasons = []
        if data["kind"] == "EXPENSE":
            used = self.repo.one(
                "SELECT COALESCE(SUM(amount), 0) AS total FROM expenses "
                "WHERE category_id = ? AND status = 'APPROVED' "
                "AND kind = 'EXPENSE' AND id != ?",
                (category["id"], exclude or ""),
            )["total"]
            if category["budget"] is None:
                reasons.append("NO_BUDGET: 请管理员设置分类预算")
            elif used + data["amount"] > category["budget"]:
                reasons.append("OVER_BUDGET")
        if category["threshold"] is not None:
            if data["amount"] > category["threshold"]:
                reasons.append("LARGE_AMOUNT")
        recent = self.repo.all(
            "SELECT id, amount, created_at FROM expenses WHERE ledger_id = ? "
            "AND category_id = ? AND kind = ? AND id != ? "
            "AND status IN ('PENDING', 'APPROVED')",
            (ledger_id, category["id"], data["kind"], exclude or ""),
        )
        current = datetime.fromisoformat(created_at)
        for other in recent:
            elapsed = abs((current - datetime.fromisoformat(other["created_at"])).total_seconds())
            if elapsed <= 300 and abs(data["amount"] - other["amount"]) * 10 <= other["amount"]:
                reasons.append("POTENTIAL_DUPLICATE")
                break
        return reasons

    def _save(
        self, actor, ledger_id, data, reasons, parent_id=None, adjustment_id=None, generate=True
    ):
        expense_id = identifier()
        status = "PENDING" if reasons else "APPROVED"
        self.repo.execute(
            "INSERT INTO expenses (id, ledger_id, submitter, category_id, amount, "
            "kind, title, occurred_at, created_at, status, reasons, parent_id, "
            "adjustment_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                expense_id,
                ledger_id,
                actor,
                data["category_id"],
                data["amount"],
                data["kind"],
                data["title"],
                data["occurred_at"],
                now(),
                status,
                encode(reasons),
                parent_id,
                adjustment_id,
            ),
        )
        for table, key in (("contributions", "contributions"), ("shares", "shares")):
            for person, amount in data[key].items():
                self.repo.execute(
                    f"INSERT INTO {table} VALUES (?, ?, ?)",
                    (expense_id, person, amount),
                )
        if status == "APPROVED":
            self._approval(expense_id, None, "APPROVED", "AUTO")
            if generate:
                self.generate_debts(expense_id, data)
        return expense_id

    def submit(self, actor, ledger_id, data, request_id):
        """Idempotently submit one expense and atomically reserve valid budget."""
        payload = [ledger_id, data]
        with self.repo.transaction():
            self.ledgers.member(actor, ledger_id, ("ADMIN", "MEMBER"))
            previous = self.repo.replay(actor, request_id, "submit", payload)
            if previous:
                return previous
            self.ledgers.writable(actor, ledger_id)
            normalized = self.validate(ledger_id, data)
            reasons = self.reasons(ledger_id, normalized, now())
            expense_id = self._save(actor, ledger_id, normalized, reasons)
            self.repo.log(ledger_id, actor, "SUBMIT", expense_id)
            self.repo.remember(actor, request_id, "submit", payload, expense_id)
            return expense_id

    def _approval(self, expense_id, actor, decision, reason):
        self.repo.execute(
            "INSERT INTO approvals VALUES (?, ?, ?, ?, ?, ?)",
            (identifier(), expense_id, actor, decision, reason, now()),
        )

    def get_data(self, expense_id):
        """Load integer monetary allocations for internal use."""
        expense = self.repo.one("SELECT * FROM expenses WHERE id = ?", (expense_id,))
        for table in ("contributions", "shares"):
            expense[table] = {
                row["user_id"]: row["amount"]
                for row in self.repo.all(
                    f"SELECT * FROM {table} WHERE expense_id = ?", (expense_id,)
                )
            }
        return expense

    def generate_debts(self, expense_id, data):
        """Generate traceable original debts; income reverses payment direction."""
        pairs = allocation_pairs(data["contributions"], data["shares"])
        for (debtor, creditor), amount in pairs.items():
            if data["kind"] == "INCOME":
                debtor, creditor = creditor, debtor
            self.repo.execute(
                "INSERT INTO debts (id, expense_id, debtor, creditor, amount) "
                "VALUES (?, ?, ?, ?, ?)",
                (identifier(), expense_id, debtor, creditor, amount),
            )

    def review(self, actor, expense_id, approve, reason, version, request_id):
        """Only the first decision on the expected pending version can succeed."""
        reason = text(reason, "审核意见")
        if not isinstance(approve, bool):
            raise DomainError("审核决定必须是布尔值")
        payload = [expense_id, approve, reason, version]
        with self.repo.transaction():
            expense = self.get_data(expense_id)
            ledger_id = expense["ledger_id"]
            self.ledgers.member(actor, ledger_id, ("ADMIN",))
            previous = self.repo.replay(actor, request_id, "review", payload)
            if previous:
                return previous
            self._expected(expense, "PENDING", version)
            status = "APPROVED" if approve else "REJECTED"
            self.repo.execute(
                "UPDATE expenses SET status = ?, version = version + 1 WHERE id = ?",
                (status, expense_id),
            )
            self._approval(expense_id, actor, status, reason)
            if approve:
                self.generate_debts(expense_id, expense)
            self.repo.log(ledger_id, actor, status, expense_id, reason)
            self.repo.remember(actor, request_id, "review", payload, expense_id)
            return expense_id

    @staticmethod
    def _expected(expense, status, version):
        if expense["status"] != status or expense["version"] != version:
            raise Conflict("账单状态或版本已变化，请刷新后重试")

    def _own(self, actor, expense):
        member = self.ledgers.member(actor, expense["ledger_id"], ("ADMIN", "MEMBER"))
        if member["role"] != "ADMIN" and expense["submitter"] != actor:
            raise DomainError("只能修改自己提交的账单")

    def _archive(self, expense):
        self.repo.execute(
            "INSERT INTO history VALUES (?, ?, ?, ?)",
            (identifier(), expense["id"], encode(expense), now()),
        )

    def _has_payment(self, expense_id):
        return (
            self.repo.one(
                "WITH RECURSIVE lineage(id, parent_id) AS ("
                "SELECT id, parent_id FROM expenses WHERE id = ? UNION ALL "
                "SELECT e.id, e.parent_id FROM expenses e JOIN lineage l ON e.id = l.parent_id) "
                "SELECT COUNT(*) AS n FROM payments p JOIN debts d ON d.id = p.debt_id "
                "WHERE d.expense_id IN (SELECT id FROM lineage) "
                "AND p.status IN ('PENDING', 'CONFIRMED')",
                (expense_id,),
            )["n"]
            > 0
        )

    def void(self, actor, expense_id, reason, version, request_id):
        """Void an unpaid approved expense or withdraw one's pending expense."""
        reason = text(reason, "作废原因")
        payload = [expense_id, reason, version]
        with self.repo.transaction():
            expense = self.get_data(expense_id)
            self._own(actor, expense)
            previous = self.repo.replay(actor, request_id, "void", payload)
            if previous:
                return previous
            if expense["status"] not in ("APPROVED", "PENDING"):
                raise Conflict("当前状态不允许作废")
            if expense["version"] != version or self._has_payment(expense_id):
                raise Conflict("版本已变化或已有付款，必须使用调整流程")
            self._archive(expense)
            self.repo.execute(
                "UPDATE expenses SET status = 'VOID', version = version + 1 WHERE id = ?",
                (expense_id,),
            )
            self.repo.execute("UPDATE debts SET active = 0 WHERE expense_id = ?", (expense_id,))
            self.repo.log(expense["ledger_id"], actor, "VOID", expense_id, reason)
            self.repo.remember(actor, request_id, "void", payload, expense_id)
            return expense_id

    def amend(self, actor, expense_id, data, reason, version, request_id):
        """Replace unpaid records; settled corrections require explicit review."""
        reason = text(reason, "更正原因")
        payload = [expense_id, data, reason, version]
        with self.repo.transaction():
            expense = self.get_data(expense_id)
            self._own(actor, expense)
            previous = self.repo.replay(actor, request_id, "amend", payload)
            if previous:
                return previous
            self._expected(expense, "APPROVED", version)
            ledger_id = expense["ledger_id"]
            normalized = self.validate(ledger_id, data)
            existing = self.repo.all(
                "SELECT id FROM adjustments WHERE expense_id = ? AND status = 'PENDING'",
                (expense_id,),
            )
            if existing:
                raise Conflict("此账单已有待审核的更正申请")
            if self._has_payment(expense_id):
                result_id = identifier()
                self.repo.execute(
                    "INSERT INTO adjustments VALUES (?, ?, ?, ?, ?, 'PENDING', ?, NULL)",
                    (result_id, expense_id, actor, reason, encode(normalized), now()),
                )
            else:
                self._archive(expense)
                self.repo.execute(
                    "UPDATE expenses SET status = 'CORRECTED', version = version + 1 WHERE id = ?",
                    (expense_id,),
                )
                self.repo.execute("UPDATE debts SET active = 0 WHERE expense_id = ?", (expense_id,))
                reasons = self.reasons(ledger_id, normalized, now(), expense_id)
                result_id = self._save(actor, ledger_id, normalized, reasons, expense_id)
            self.repo.log(ledger_id, actor, "AMEND", expense_id, reason)
            self.repo.remember(actor, request_id, "amend", payload, result_id)
            return result_id

    def review_adjustment(self, actor, adjustment_id, approve, reason, request_id):
        """Append debt deltas while preserving every historical payment.

        Original obligations remain; decreases become reverse obligations.
        Both directions can coexist because this system never nets transfers.
        """
        reason = text(reason, "调整审核意见")
        if not isinstance(approve, bool):
            raise DomainError("审核决定必须是布尔值")
        payload = [adjustment_id, approve, reason]
        with self.repo.transaction():
            adjustment = self.repo.one("SELECT * FROM adjustments WHERE id = ?", (adjustment_id,))
            expense = self.get_data(adjustment["expense_id"])
            ledger_id = expense["ledger_id"]
            self.ledgers.member(actor, ledger_id, ("ADMIN",))
            previous = self.repo.replay(actor, request_id, "review_adjustment", payload)
            if previous:
                return previous
            if adjustment["status"] != "PENDING":
                raise Conflict("调整已处理")
            status = "APPROVED" if approve else "REJECTED"
            if approve:
                if expense["status"] != "APPROVED":
                    raise Conflict("原账单状态已变化")
                replacement = json.loads(adjustment["replacement"])
                old_pairs = allocation_pairs(expense["contributions"], expense["shares"])
                new_pairs = allocation_pairs(replacement["contributions"], replacement["shares"])
                if expense["kind"] != replacement["kind"]:
                    raise DomainError("已结算更正不可改变收支方向")
                for pair in old_pairs.keys() | new_pairs.keys():
                    delta = new_pairs.get(pair, 0) - old_pairs.get(pair, 0)
                    if not delta:
                        continue
                    debtor, creditor = pair
                    if expense["kind"] == "INCOME":
                        debtor, creditor = creditor, debtor
                    if delta < 0:
                        debtor, creditor = creditor, debtor
                    self.repo.execute(
                        "INSERT INTO debts (id, expense_id, debtor, creditor, amount, "
                        "adjustment_id) VALUES (?, ?, ?, ?, ?, ?)",
                        (identifier(), expense["id"], debtor, creditor, abs(delta), adjustment_id),
                    )
                self._archive(expense)
                self.repo.execute(
                    "UPDATE expenses SET status = 'CORRECTED', version = version + 1 WHERE id = ?",
                    (expense["id"],),
                )
                self._save(
                    adjustment["requester"],
                    ledger_id,
                    replacement,
                    [],
                    expense["id"],
                    adjustment_id,
                    generate=False,
                )
            self.repo.execute(
                "UPDATE adjustments SET status = ?, reviewed_at = ? WHERE id = ?",
                (status, now(), adjustment_id),
            )
            self.repo.log(ledger_id, actor, "REVIEW_ADJUSTMENT", adjustment_id, reason)
            self.repo.remember(actor, request_id, "review_adjustment", payload, adjustment_id)
            return adjustment_id
