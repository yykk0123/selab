"""Authorized search, reporting, expense details and immutable audit views."""

import json

from .common import DomainError, money, timestamp
from .expense_service import ExpenseService
from .ledger_service import LedgerService
from .settlement_service import SettlementService


class QueryService:
    """Read services use one SQLite snapshot for internally consistent reports."""

    def __init__(self, repository):
        self.repo = repository
        self.ledgers = LedgerService(repository)

    def members(self, actor, ledger_id):
        """Return public user information, never password material."""
        self.ledgers.member(actor, ledger_id)
        return self.repo.all(
            "SELECT m.*, u.username, u.email FROM members m "
            "JOIN users u ON u.id = m.user_id WHERE ledger_id = ? "
            "ORDER BY m.joined_at, m.user_id",
            (ledger_id,),
        )

    def categories(self, actor, ledger_id):
        """Return configured budgets and review thresholds in cents."""
        self.ledgers.member(actor, ledger_id)
        return self.repo.all(
            "SELECT * FROM categories WHERE ledger_id = ? ORDER BY name", (ledger_id,)
        )

    def expenses(
        self,
        actor,
        ledger_id,
        category_id=None,
        user_id=None,
        start=None,
        end=None,
        minimum=None,
        maximum=None,
        status=None,
    ):
        """Combine inclusive category, participant, time and amount filters."""
        self.ledgers.member(actor, ledger_id)
        start = timestamp(start) if start else None
        end = timestamp(end) if end else None
        minimum = money(minimum, True) if minimum is not None else None
        maximum = money(maximum, True) if maximum is not None else None
        if start and end and start > end:
            raise DomainError("开始时间不能晚于结束时间")
        if minimum is not None and maximum is not None and minimum > maximum:
            raise DomainError("金额下限不能大于上限")
        sql = "SELECT e.* FROM expenses e WHERE e.ledger_id = ?"
        parameters = [ledger_id]
        filters = (
            ("e.category_id = ?", category_id),
            ("e.occurred_at >= ?", start),
            ("e.occurred_at <= ?", end),
            ("e.amount >= ?", minimum),
            ("e.amount <= ?", maximum),
            ("e.status = ?", status),
        )
        for clause, value in filters:
            if value is not None:
                sql += " AND " + clause
                parameters.append(value)
        if user_id:
            sql += (
                " AND (e.submitter = ? OR EXISTS (SELECT 1 FROM shares s "
                "WHERE s.expense_id = e.id AND s.user_id = ?) OR EXISTS "
                "(SELECT 1 FROM contributions c WHERE c.expense_id = e.id "
                "AND c.user_id = ?))"
            )
            parameters.extend([user_id, user_id, user_id])
        return self.repo.all(sql + " ORDER BY e.occurred_at, e.id", parameters)

    def detail(self, actor, expense_id):
        """Include preserved versions, review history and associated adjustments."""
        expense = ExpenseService(self.repo).get_data(expense_id)
        self.ledgers.member(actor, expense["ledger_id"])
        expense["reasons"] = json.loads(expense["reasons"])
        for key, table in (
            ("reviews", "approvals"),
            ("history", "history"),
            ("adjustments", "adjustments"),
        ):
            expense[key] = self.repo.all(
                f"SELECT * FROM {table} WHERE expense_id = ? ORDER BY created_at",
                (expense_id,),
            )
        return expense

    def logs(self, actor, ledger_id, object_id=None, start=None, end=None):
        """Expose audit records to administrators with optional object/time filters."""
        self.ledgers.member(actor, ledger_id, ("ADMIN",))
        sql = "SELECT * FROM logs WHERE ledger_id = ?"
        parameters = [ledger_id]
        start = timestamp(start) if start else None
        end = timestamp(end) if end else None
        if start and end and start > end:
            raise DomainError("开始时间不能晚于结束时间")
        for clause, value in (
            ("object_id = ?", object_id),
            ("created_at >= ?", start),
            ("created_at <= ?", end),
        ):
            if value is not None:
                sql += " AND " + clause
                parameters.append(value)
        return self.repo.all(sql + " ORDER BY created_at, id", parameters)

    def statistics(self, actor, ledger_id):
        """Count only current approved versions; display pending totals separately."""
        with self.repo.transaction():
            self.ledgers.member(actor, ledger_id)
            categories = self.repo.all(
                "SELECT c.id, c.name, c.budget, "
                "COALESCE(SUM(CASE WHEN e.status = 'APPROVED' AND e.kind = 'EXPENSE' "
                "THEN e.amount ELSE 0 END), 0) AS expense, "
                "COALESCE(SUM(CASE WHEN e.status = 'APPROVED' AND e.kind = 'INCOME' "
                "THEN e.amount ELSE 0 END), 0) AS income "
                "FROM categories c LEFT JOIN expenses e ON e.category_id = c.id "
                "WHERE c.ledger_id = ? GROUP BY c.id ORDER BY c.name",
                (ledger_id,),
            )
            members = self.members(actor, ledger_id)
            for member in members:
                person = member["user_id"]
                for table, field in (("contributions", "contributed"), ("shares", "borne")):
                    for kind, suffix in (("EXPENSE", ""), ("INCOME", "_income")):
                        member[field + suffix] = self.repo.one(
                            f"SELECT COALESCE(SUM(t.amount), 0) AS total FROM {table} t "
                            "JOIN expenses e ON e.id = t.expense_id "
                            "WHERE e.ledger_id = ? AND t.user_id = ? "
                            "AND e.status = 'APPROVED' AND e.kind = ?",
                            (ledger_id, person, kind),
                        )["total"]
                member["receivable"] = 0
                member["payable"] = 0
            by_user = {member["user_id"]: member for member in members}
            debts = SettlementService(self.repo).list_debts(actor, ledger_id)
            for debt in debts:
                by_user[debt["debtor"]]["payable"] += debt["remaining"]
                by_user[debt["creditor"]]["receivable"] += debt["remaining"]
            states = self.repo.all(
                "SELECT status, COUNT(*) AS count, SUM(amount) AS amount "
                "FROM expenses WHERE ledger_id = ? GROUP BY status",
                (ledger_id,),
            )
            return {
                "categories": categories,
                "members": members,
                "states": states,
                "expense": sum(category["expense"] for category in categories),
                "income": sum(category["income"] for category in categories),
            }

    def payments(self, actor, ledger_id):
        """Show personal payments; administrators may inspect all ledger payments."""
        member = self.ledgers.member(actor, ledger_id, active=False)
        sql = (
            "SELECT p.*, d.debtor, d.creditor, d.expense_id FROM payments p "
            "JOIN debts d ON d.id = p.debt_id JOIN expenses e ON e.id = d.expense_id "
            "WHERE e.ledger_id = ?"
        )
        parameters = [ledger_id]
        if member["status"] != "ACTIVE" or member["role"] != "ADMIN":
            sql += " AND (d.debtor = ? OR d.creditor = ?)"
            parameters.extend([actor, actor])
        return self.repo.all(sql + " ORDER BY p.created_at", parameters)

    def adjustments(self, actor, ledger_id):
        """Provide the review queue with reason, normalized replacement and source."""
        self.ledgers.member(actor, ledger_id, ("ADMIN",))
        rows = self.repo.all(
            "SELECT a.*, e.title FROM adjustments a JOIN expenses e "
            "ON e.id = a.expense_id WHERE e.ledger_id = ? ORDER BY a.created_at",
            (ledger_id,),
        )
        for row in rows:
            row["replacement"] = json.loads(row["replacement"])
        return rows
