"""Original debt inspection and two-sided partial-payment confirmation."""

from .common import AccessDenied, Conflict, DomainError, identifier, money, now, text
from .ledger_service import LedgerService


class SettlementService:
    """Reserve pending payments so concurrent requests cannot overpay a debt."""

    def __init__(self, repository):
        self.repo = repository
        self.ledgers = LedgerService(repository)

    def list_debts(self, actor, ledger_id):
        """Former members see only obligations involving themselves."""
        member = self.ledgers.member(actor, ledger_id, active=False)
        sql = (
            "SELECT d.*, e.title, e.ledger_id, "
            "d.amount - d.paid AS remaining, "
            "COALESCE((SELECT SUM(p.amount) FROM payments p "
            "WHERE p.debt_id = d.id AND p.status = 'PENDING'), 0) AS reserved "
            "FROM debts d JOIN expenses e ON e.id = d.expense_id "
            "WHERE e.ledger_id = ? AND d.active = 1"
        )
        parameters = [ledger_id]
        if member["status"] != "ACTIVE":
            sql += " AND (d.debtor = ? OR d.creditor = ?)"
            parameters.extend([actor, actor])
        return self.repo.all(sql + " ORDER BY d.id", parameters)

    def _debt(self, actor, debt_id):
        debt = self.repo.one(
            "SELECT d.*, e.ledger_id FROM debts d JOIN expenses e "
            "ON e.id = d.expense_id WHERE d.id = ?",
            (debt_id,),
        )
        member = self.ledgers.member(actor, debt["ledger_id"], active=False)
        if member["status"] != "ACTIVE" and actor not in (debt["debtor"], debt["creditor"]):
            raise AccessDenied("只能处理与自己相关的结算")
        if member["role"] == "READER":
            raise AccessDenied("只读成员不能付款或确认收款")
        return debt

    def pay(self, actor, debt_id, amount, request_id):
        """Mark a payment without reducing the debt until the creditor confirms."""
        amount = money(amount)
        payload = [debt_id, amount]
        with self.repo.transaction():
            debt = self._debt(actor, debt_id)
            if debt["debtor"] != actor:
                raise AccessDenied("仅债务人可以标记付款")
            previous = self.repo.replay(actor, request_id, "pay", payload)
            if previous:
                return previous
            if not debt["active"]:
                raise Conflict("此债务已失效")
            pending = self.repo.one(
                "SELECT COALESCE(SUM(amount), 0) AS total FROM payments "
                "WHERE debt_id = ? AND status = 'PENDING'",
                (debt_id,),
            )["total"]
            if amount > debt["amount"] - debt["paid"] - pending:
                raise DomainError("付款超过可支付余额（含待确认占用）")
            payment_id = identifier()
            self.repo.execute(
                "INSERT INTO payments VALUES (?, ?, ?, ?, 'PENDING', ?, NULL)",
                (payment_id, debt_id, actor, amount, now()),
            )
            self.repo.log(debt["ledger_id"], actor, "PAY", payment_id)
            self.repo.remember(actor, request_id, "pay", payload, payment_id)
            return payment_id

    def confirm(self, actor, payment_id, accept, reason, request_id):
        """Confirm exactly once or reject and release the payment reservation."""
        reason = text(reason, "确认意见")
        if not isinstance(accept, bool):
            raise DomainError("收款决定必须是布尔值")
        payload = [payment_id, accept, reason]
        with self.repo.transaction():
            payment = self.repo.one("SELECT * FROM payments WHERE id = ?", (payment_id,))
            debt = self._debt(actor, payment["debt_id"])
            if debt["creditor"] != actor:
                raise AccessDenied("仅收款人可以确认或拒绝收款")
            previous = self.repo.replay(actor, request_id, "confirm", payload)
            if previous:
                return previous
            if payment["status"] != "PENDING" or not debt["active"]:
                raise Conflict("付款已处理或债务已失效")
            status = "CONFIRMED" if accept else "REJECTED"
            if accept:
                if debt["paid"] + payment["amount"] > debt["amount"]:
                    raise Conflict("付款超过剩余债务")
                self.repo.execute(
                    "UPDATE debts SET paid = paid + ? WHERE id = ?",
                    (payment["amount"], debt["id"]),
                )
            self.repo.execute(
                "UPDATE payments SET status = ?, confirmed_at = ? WHERE id = ?",
                (status, now(), payment_id),
            )
            self.repo.log(debt["ledger_id"], actor, status, payment_id, reason)
            self.repo.remember(actor, request_id, "confirm", payload, payment_id)
            return payment_id
