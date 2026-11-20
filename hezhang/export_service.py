"""Authorized CSV report export and per-member settlement statements."""

import csv
from pathlib import Path

from .common import AccessDenied, DomainError, currency
from .ledger_service import LedgerService
from .query_service import QueryService
from .settlement_service import SettlementService


class ExportService:
    """Export data through existing access checks into a new UTF-8 CSV file."""

    def __init__(self, repository):
        self.repo = repository
        self.queries = QueryService(repository)
        self.ledgers = LedgerService(repository)
        self.settlements = SettlementService(repository)

    @staticmethod
    def _cell(value):
        """Prevent user-entered text from becoming a spreadsheet formula."""
        if value is None:
            return ""
        if isinstance(value, str) and value.lstrip().startswith(("=", "+", "-", "@")):
            return "'" + value
        return value

    @classmethod
    def _write(cls, destination, columns, rows):
        path = Path(destination)
        if not path.parent.is_dir():
            raise DomainError("导出目录不存在")
        try:
            with path.open("x", encoding="utf-8-sig", newline="") as handle:
                writer = csv.writer(handle)
                writer.writerow([label for _, label in columns])
                for row in rows:
                    writer.writerow([cls._cell(row.get(key)) for key, _ in columns])
        except FileExistsError as exc:
            raise DomainError("导出文件已存在，禁止覆盖") from exc
        return {"path": str(path.resolve()), "rows": len(rows)}

    def expenses(self, actor, ledger_id, destination, **filters):
        """Export the same authorized, combined filters as interactive search."""
        with self.repo.transaction():
            rows = self.queries.expenses(actor, ledger_id, **filters)
            categories = {
                row["id"]: row["name"] for row in self.queries.categories(actor, ledger_id)
            }
            members = {
                row["user_id"]: row["username"] for row in self.queries.members(actor, ledger_id)
            }
            for row in rows:
                row["category"] = categories[row["category_id"]]
                row["submitter_name"] = members[row["submitter"]]
                row["amount_yuan"] = currency(row["amount"])
        columns = [
            ("id", "账单ID"),
            ("title", "标题"),
            ("kind", "收支类型"),
            ("category", "类别"),
            ("amount_yuan", "金额（元）"),
            ("submitter_name", "提交人"),
            ("occurred_at", "消费时间"),
            ("status", "状态"),
            ("version", "版本"),
            ("parent_id", "原账单ID"),
            ("adjustment_id", "调整ID"),
            ("reasons", "审核原因"),
        ]
        return self._write(destination, columns, rows)

    def debts(self, actor, ledger_id, destination):
        """Export original obligations without netting opposing directions."""
        rows = self.settlements.list_debts(actor, ledger_id)
        for row in rows:
            for field in ("amount", "paid", "remaining", "reserved"):
                row[field] = currency(row[field])
        columns = [
            ("id", "债务ID"),
            ("expense_id", "来源账单ID"),
            ("title", "来源标题"),
            ("debtor", "付款成员ID"),
            ("creditor", "收款成员ID"),
            ("amount", "原始金额（元）"),
            ("paid", "已确认金额（元）"),
            ("remaining", "剩余金额（元）"),
            ("reserved", "待确认金额（元）"),
            ("adjustment_id", "调整ID"),
        ]
        return self._write(destination, columns, rows)

    def statement(self, actor, ledger_id, user_id=None):
        """Show personal original obligations, including former members' own data."""
        user_id = user_id or actor
        member = self.ledgers.member(actor, ledger_id, active=False)
        if user_id != actor:
            if member["status"] != "ACTIVE" or member["role"] != "ADMIN":
                raise AccessDenied("只有管理员可以查询其他成员的结算对账单")
            self.ledgers.member(user_id, ledger_id, active=False)
        with self.repo.transaction():
            debts = self.settlements.list_debts(actor, ledger_id)
            outgoing = [row for row in debts if row["debtor"] == user_id]
            incoming = [row for row in debts if row["creditor"] == user_id]
            payments = self.queries.payments(actor, ledger_id)
            if user_id != actor:
                payments = [row for row in payments if user_id in (row["debtor"], row["creditor"])]
            payable = sum(row["remaining"] for row in outgoing)
            receivable = sum(row["remaining"] for row in incoming)
            reserved_out = sum(row["reserved"] for row in outgoing)
            reserved_in = sum(row["reserved"] for row in incoming)
            return {
                "ledger_id": ledger_id,
                "user_id": user_id,
                "payable": payable,
                "receivable": receivable,
                "outgoing_pending_confirmation": reserved_out,
                "incoming_pending_confirmation": reserved_in,
                "available_to_pay": payable - reserved_out,
                "outgoing": outgoing,
                "incoming": incoming,
                "payments": payments,
            }

    def audit(self, actor, ledger_id, destination, **filters):
        """Export an administrator's object/time-filtered audit trail."""
        rows = self.queries.logs(actor, ledger_id, **filters)
        columns = [
            ("id", "日志ID"),
            ("ledger_id", "账本ID"),
            ("actor", "操作者ID"),
            ("action", "动作"),
            ("object_id", "对象ID"),
            ("reason", "原因"),
            ("created_at", "时间"),
        ]
        return self._write(destination, columns, rows)
