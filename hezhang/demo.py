"""Reproducible simulated-user walkthrough against a fresh persistent database."""

import argparse
import json
from pathlib import Path

from .auth import AuthService
from .backup import BackupService
from .database import Repository
from .expense_service import ExpenseService
from .ledger_service import LedgerService
from .query_service import QueryService
from .settlement_service import SettlementService


def walkthrough(repository):
    """Create two fictional users, submit a bill, and confirm a partial payment."""
    auth = AuthService(repository)
    ledgers = LedgerService(repository)
    expenses = ExpenseService(repository)
    settlements = SettlementService(repository)
    queries = QueryService(repository)
    admin = auth.register("林晓", "demo-admin-2026")
    member = auth.register("陈晨", "demo-member-2026")
    admin_token = auth.login("林晓", "demo-admin-2026")
    member_token = auth.login("陈晨", "demo-member-2026")
    admin = auth.authenticate(admin_token)
    member = auth.authenticate(member_token)
    ledger_id = ledgers.create(admin, "周末旅行演示", "demo-create")
    ledgers.invite(admin, ledger_id, member, "MEMBER", "demo-invite")
    category_id = ledgers.category(admin, ledger_id, "餐饮", "1000", "500", "demo-category")
    data = {
        "title": "两人午餐",
        "category_id": category_id,
        "amount": "100",
        "contributions": {admin: "100"},
        "participants": [admin, member],
    }
    expense_id = expenses.submit(member, ledger_id, data, "demo-submit")
    retried_id = expenses.submit(member, ledger_id, data, "demo-submit")
    if expense_id != retried_id:
        raise RuntimeError("幂等检查失败")
    debt = settlements.list_debts(member, ledger_id)[0]
    payment_id = settlements.pay(member, debt["id"], "20", "demo-pay")
    before_confirmation = settlements.list_debts(member, ledger_id)[0]
    settlements.confirm(admin, payment_id, True, "已收到20元", "demo-confirm")
    after_confirmation = settlements.list_debts(member, ledger_id)[0]
    expensive_data = dict(
        data, title="需要人工审核的聚餐", amount="600", contributions={admin: "600"}
    )
    pending_id = expenses.submit(member, ledger_id, expensive_data, "demo-large")
    pending = queries.detail(admin, pending_id)
    expenses.review(admin, pending_id, False, "演示驳回", 1, "demo-reject")
    stats = queries.statistics(admin, ledger_id)
    expected = (5000, 3000, 10000)
    actual = (before_confirmation["remaining"], after_confirmation["remaining"], stats["expense"])
    if actual != expected:
        raise RuntimeError(f"演示金额核对失败：{actual}")
    auth.logout(admin_token)
    auth.logout(member_token)
    return {
        "ledger_id": ledger_id,
        "expense_id": expense_id,
        "repeated_request_same_expense": expense_id == retried_id,
        "pending_review_reasons": pending["reasons"],
        "before_confirmation_cents": before_confirmation["remaining"],
        "after_confirmation_cents": after_confirmation["remaining"],
        "approved_expense_total_cents": stats["expense"],
        "audit_log_count": len(queries.logs(admin, ledger_id)),
    }


def main():
    """Refuse to change an existing database when running the demo."""
    parser = argparse.ArgumentParser(description="合账模拟用户演示（仅限新数据库）")
    parser.add_argument("--db", required=True, help="不存在的演示数据库路径")
    parser.add_argument("--backup", help="可选的新备份文件路径")
    args = parser.parse_args()
    if Path(args.db).exists():
        parser.error("演示数据库已存在，请使用新的路径")
    with Repository(args.db) as repository:
        result = walkthrough(repository)
        if args.backup:
            result["backup_verified_counts"] = BackupService.create(repository, args.backup)
        print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
