"""Shared authenticated application facade for native and optional web clients."""

from .auth import AuthService
from .common import DomainError, identifier
from .expense_service import ExpenseService
from .ledger_service import LedgerService
from .query_service import QueryService
from .settlement_service import SettlementService


class Application:
    """Resolve session identities before dispatching domain operations."""

    def __init__(self, repository):
        self.repo = repository
        self.auth = AuthService(repository)
        self.ledgers = LedgerService(repository)
        self.expenses = ExpenseService(repository)
        self.queries = QueryService(repository)
        self.settlements = SettlementService(repository)

    def actor(self, token):
        return self.auth.authenticate(token)

    def state(self, token, ledger_id=None):
        """Load authorized page data without exposing credential storage."""
        actor = self.actor(token)
        user = self.repo.one("SELECT id, username FROM users WHERE id = ?", (actor,))
        ledgers = self.ledgers.list_ledgers(actor)
        result = {"user": user, "ledgers": ledgers}
        if not ledgers:
            return result
        ledger_id = ledger_id or ledgers[0]["id"]
        member = self.ledgers.member(actor, ledger_id, active=False)
        result["ledger"] = next(item for item in ledgers if item["id"] == ledger_id)
        result["membership"] = member
        result["debts"] = self.settlements.list_debts(actor, ledger_id)
        result["payments"] = self.queries.payments(actor, ledger_id)
        participants = {actor}
        for debt in result["debts"]:
            participants.update((debt["debtor"], debt["creditor"]))
        result["participant_names"] = {
            person: self.repo.one("SELECT username FROM users WHERE id = ?", (person,))["username"]
            for person in participants
        }
        if member["status"] != "ACTIVE":
            return result
        result["members"] = self.queries.members(actor, ledger_id)
        result["categories"] = self.queries.categories(actor, ledger_id)
        result["expenses"] = self.queries.expenses(actor, ledger_id)
        result["statistics"] = self.queries.statistics(actor, ledger_id)
        if member["role"] == "ADMIN":
            result["logs"] = self.queries.logs(actor, ledger_id)
            result["adjustments"] = self.queries.adjustments(actor, ledger_id)
        return result

    def mutate(self, token, operation, data):
        """Map bounded UI actions to authorized services, preserving request IDs."""
        if operation == "register":
            return self.auth.register(data["username"], data["password"], data.get("email", ""))
        if operation == "login":
            return self.auth.login(data["username"], data["password"])
        actor = self.actor(token)
        request_id = data.get("request_id") or identifier()
        ledger_id = data.get("ledger_id")
        if operation == "logout":
            self.auth.logout(token)
            return None
        if operation == "create":
            return self.ledgers.create(actor, data["name"], request_id)
        if operation == "submit":
            return self.expenses.submit(actor, ledger_id, data["expense"], request_id)
        if operation == "review":
            return self.expenses.review(
                actor, data["id"], data["approve"], data["reason"], data["version"], request_id
            )
        if operation == "void":
            return self.expenses.void(
                actor, data["id"], data["reason"], data["version"], request_id
            )
        if operation == "amend":
            return self.expenses.amend(
                actor, data["id"], data["expense"], data["reason"], data["version"], request_id
            )
        if operation == "adjustment":
            return self.expenses.review_adjustment(
                actor, data["id"], data["approve"], data["reason"], request_id
            )
        if operation == "pay":
            return self.settlements.pay(actor, data["id"], data["amount"], request_id)
        if operation == "confirm":
            return self.settlements.confirm(
                actor, data["id"], data["accept"], data["reason"], request_id
            )
        if operation == "invite":
            user = self.repo.one("SELECT id FROM users WHERE username = ?", (data["username"],))
            return self.ledgers.invite(actor, ledger_id, user["id"], data["role"], request_id)
        if operation == "role":
            return self.ledgers.set_role(actor, ledger_id, data["id"], data["role"], request_id)
        if operation == "remove":
            return self.ledgers.remove(actor, ledger_id, data["id"], data["reason"], request_id)
        if operation == "leave":
            return self.ledgers.leave(actor, ledger_id, request_id)
        if operation == "status":
            return self.ledgers.change_status(
                actor, ledger_id, data["status"], data["reason"], request_id
            )
        if operation == "category":
            return self.ledgers.category(
                actor,
                ledger_id,
                data["name"],
                data.get("budget") or None,
                data.get("threshold") or None,
                request_id,
            )
        raise DomainError("未知操作")


def seed_demo(repository):
    """Seed the four fictional users and three expenses from the design example."""
    if repository.all("SELECT id FROM users LIMIT 1"):
        return
    app = Application(repository)
    users = [app.auth.register(name, "demo12345") for name in ("林晓", "陈晨", "周宁", "许禾")]
    admin = users[0]
    ledger_id = app.ledgers.create(admin, "秋日旅行账本", "demo-ledger")
    for index, user in enumerate(users[1:]):
        app.ledgers.invite(admin, ledger_id, user, "MEMBER", f"demo-invite-{index}")
    for index, (name, amount, budget, title) in enumerate(
        (
            ("餐饮", "600", "1000", "旅行晚餐"),
            ("住宿", "1200", "2000", "周末住宿"),
            ("交通", "600", "800", "打车费用"),
        )
    ):
        category_id = app.ledgers.category(
            admin, ledger_id, name, budget, "2000", f"demo-cat-{index}"
        )
        app.expenses.submit(
            users[index],
            ledger_id,
            {
                "title": title,
                "category_id": category_id,
                "amount": amount,
                "contributions": {users[index]: amount},
                "participants": users,
            },
            f"demo-expense-{index}",
        )
    category_id = app.queries.categories(admin, ledger_id)[0]["id"]
    app.expenses.submit(
        users[1],
        ledger_id,
        {
            "title": "城市餐厅晚餐",
            "category_id": category_id,
            "amount": "680",
            "contributions": {users[1]: "680"},
            "participants": users,
        },
        "demo-pending",
    )
    debt = next(
        row
        for row in app.settlements.list_debts(admin, ledger_id)
        if row["debtor"] == users[1] and row["creditor"] == admin
    )
    app.settlements.pay(users[1], debt["id"], "150", "demo-payment")
