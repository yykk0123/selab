"""Ledger lifecycle, membership and configurable category budgets."""

from .common import AccessDenied, Conflict, DomainError, identifier, money, now, text


class LedgerService:
    """Authorize every operation using current persistent membership."""

    def __init__(self, repository):
        self.repo = repository

    def member(self, actor, ledger_id, roles=None, active=True):
        """Return membership, refusing both unrelated and disallowed users."""
        rows = self.repo.all(
            "SELECT * FROM members WHERE ledger_id = ? AND user_id = ?",
            (ledger_id, actor),
        )
        if not rows:
            raise AccessDenied("无权访问此账本")
        member = rows[0]
        if active and member["status"] != "ACTIVE":
            raise AccessDenied("成员关系已终止，仅可处理自己的结算")
        if roles and member["role"] not in roles:
            raise AccessDenied("当前角色无权执行此操作")
        return member

    def writable(self, actor, ledger_id):
        """Require a writer in an open ledger."""
        self.member(actor, ledger_id, ("ADMIN", "MEMBER"))
        ledger = self.repo.one("SELECT * FROM ledgers WHERE id = ?", (ledger_id,))
        if ledger["status"] != "OPEN":
            raise Conflict("账本已关闭")
        return ledger

    def create(self, actor, name, request_id):
        """Create a ledger and its initial administrator in one transaction."""
        name = text(name, "账本名称")
        with self.repo.transaction():
            self.repo.one("SELECT id FROM users WHERE id = ?", (actor,))
            previous = self.repo.replay(actor, request_id, "create_ledger", name)
            if previous:
                return previous
            ledger_id = identifier()
            self.repo.execute(
                "INSERT INTO ledgers VALUES (?, ?, 'OPEN', ?)",
                (ledger_id, name, now()),
            )
            self.repo.execute(
                "INSERT INTO members VALUES (?, ?, 'ADMIN', 'ACTIVE', ?)",
                (ledger_id, actor, now()),
            )
            self.repo.log(ledger_id, actor, "CREATE_LEDGER", ledger_id)
            self.repo.remember(actor, request_id, "create_ledger", name, ledger_id)
            return ledger_id

    def list_ledgers(self, actor):
        """List current memberships and limited-access historical memberships."""
        return self.repo.all(
            "SELECT l.*, m.role, m.status AS membership_status FROM ledgers l "
            "JOIN members m ON m.ledger_id = l.id WHERE m.user_id = ? "
            "ORDER BY l.created_at, l.id",
            (actor,),
        )

    def invite(self, actor, ledger_id, user_id, role, request_id):
        """Add an existing account; repeat invitations are explicitly rejected.

        Reusing the same request key returns its result. Reinvitation with a
        different key is intentionally not a role change or reactivation.
        """
        if role not in ("MEMBER", "READER"):
            raise DomainError("邀请角色必须为 MEMBER 或 READER")
        payload = [ledger_id, user_id, role]
        with self.repo.transaction():
            self.member(actor, ledger_id, ("ADMIN",))
            previous = self.repo.replay(actor, request_id, "invite", payload)
            if previous:
                return previous
            self.writable(actor, ledger_id)
            self.repo.one("SELECT id FROM users WHERE id = ?", (user_id,))
            exists = self.repo.all(
                "SELECT 1 FROM members WHERE ledger_id = ? AND user_id = ?",
                (ledger_id, user_id),
            )
            if exists:
                raise Conflict("用户已有成员记录；暂不支持重复邀请或恢复")
            self.repo.execute(
                "INSERT INTO members VALUES (?, ?, ?, 'ACTIVE', ?)",
                (ledger_id, user_id, role, now()),
            )
            self.repo.log(ledger_id, actor, "INVITE", user_id, role)
            self.repo.remember(actor, request_id, "invite", payload, user_id)
            return user_id

    def set_role(self, actor, ledger_id, user_id, role, request_id):
        """Change permissions, preserving at least one active administrator."""
        if role not in ("ADMIN", "MEMBER", "READER"):
            raise DomainError("无效角色")
        payload = [ledger_id, user_id, role]
        with self.repo.transaction():
            self.member(actor, ledger_id, ("ADMIN",))
            previous = self.repo.replay(actor, request_id, "role", payload)
            if previous:
                return previous
            self.writable(actor, ledger_id)
            member = self.member(user_id, ledger_id)
            if member["role"] == "ADMIN" and role != "ADMIN":
                self._preserve_admin(ledger_id)
            self.repo.execute(
                "UPDATE members SET role = ? WHERE ledger_id = ? AND user_id = ?",
                (role, ledger_id, user_id),
            )
            self.repo.log(ledger_id, actor, "SET_ROLE", user_id, role)
            self.repo.remember(actor, request_id, "role", payload, user_id)
            return user_id

    def _preserve_admin(self, ledger_id):
        count = self.repo.one(
            "SELECT COUNT(*) AS n FROM members WHERE ledger_id = ? "
            "AND role = 'ADMIN' AND status = 'ACTIVE'",
            (ledger_id,),
        )["n"]
        if count < 2:
            raise Conflict("请先指定另一位管理员，不能终止唯一管理员权限")

    def change_status(self, actor, ledger_id, status, reason, request_id):
        """Close or reopen without changing debts or historical records."""
        if status not in ("OPEN", "CLOSED"):
            raise DomainError("账本状态必须为 OPEN 或 CLOSED")
        reason = text(reason, "变更原因")
        payload = [ledger_id, status, reason]
        with self.repo.transaction():
            self.member(actor, ledger_id, ("ADMIN",))
            previous = self.repo.replay(actor, request_id, "ledger_status", payload)
            if previous:
                return previous
            ledger = self.repo.one("SELECT * FROM ledgers WHERE id = ?", (ledger_id,))
            if ledger["status"] == status:
                raise Conflict("账本已经处于目标状态")
            self.repo.execute("UPDATE ledgers SET status = ? WHERE id = ?", (status, ledger_id))
            self.repo.log(ledger_id, actor, status, ledger_id, reason)
            self.repo.remember(actor, request_id, "ledger_status", payload, ledger_id)
            return ledger_id

    def remove(self, actor, ledger_id, user_id, reason, request_id):
        """Remove a member while retaining their debts and settlement access."""
        reason = text(reason, "移除原因")
        payload = [ledger_id, user_id, reason]
        with self.repo.transaction():
            self.member(actor, ledger_id, ("ADMIN",))
            previous = self.repo.replay(actor, request_id, "remove", payload)
            if previous:
                return previous
            member = self.member(user_id, ledger_id)
            if member["role"] == "ADMIN":
                self._preserve_admin(ledger_id)
            self.repo.execute(
                "UPDATE members SET status = 'REMOVED' WHERE ledger_id = ? AND user_id = ?",
                (ledger_id, user_id),
            )
            self.repo.log(ledger_id, actor, "REMOVE_MEMBER", user_id, reason)
            self.repo.remember(actor, request_id, "remove", payload, user_id)
            return user_id

    def leave(self, actor, ledger_id, request_id):
        """Leave only after submitted pending records and all debts are cleared."""
        with self.repo.transaction():
            self.member(actor, ledger_id, active=False)
            previous = self.repo.replay(actor, request_id, "leave", ledger_id)
            if previous:
                return previous
            member = self.member(actor, ledger_id)
            if member["role"] == "ADMIN":
                self._preserve_admin(ledger_id)
            pending = self.repo.one(
                "SELECT COUNT(*) AS n FROM expenses WHERE ledger_id = ? "
                "AND submitter = ? AND status = 'PENDING'",
                (ledger_id, actor),
            )["n"]
            adjustments = self.repo.one(
                "SELECT COUNT(*) AS n FROM adjustments a JOIN expenses e "
                "ON e.id = a.expense_id WHERE e.ledger_id = ? "
                "AND a.requester = ? AND a.status = 'PENDING'",
                (ledger_id, actor),
            )["n"]
            debts = self.repo.one(
                "SELECT COUNT(*) AS n FROM debts d JOIN expenses e "
                "ON e.id = d.expense_id WHERE e.ledger_id = ? "
                "AND d.active = 1 AND d.paid < d.amount "
                "AND (d.debtor = ? OR d.creditor = ?)",
                (ledger_id, actor, actor),
            )["n"]
            if pending or adjustments or debts:
                raise Conflict("存在待审核记录或未结清债务")
            self.repo.execute(
                "UPDATE members SET status = 'LEFT' WHERE ledger_id = ? AND user_id = ?",
                (ledger_id, actor),
            )
            self.repo.log(ledger_id, actor, "LEAVE", actor)
            self.repo.remember(actor, request_id, "leave", ledger_id, actor)
            return actor

    def category(self, actor, ledger_id, name, budget, threshold, request_id):
        """Create/update a category using whole-ledger cumulative budgets.

        Budget period is an explicit implementation choice pending user review.
        None represents an unset budget or disabled large-amount threshold.
        """
        name = text(name, "类别名称", 60)
        budget = None if budget is None else money(budget, True)
        threshold = None if threshold is None else money(threshold)
        payload = [ledger_id, name, budget, threshold]
        with self.repo.transaction():
            self.member(actor, ledger_id, ("ADMIN",))
            previous = self.repo.replay(actor, request_id, "category", payload)
            if previous:
                return previous
            self.writable(actor, ledger_id)
            rows = self.repo.all(
                "SELECT id FROM categories WHERE ledger_id = ? AND name = ?",
                (ledger_id, name),
            )
            category_id = rows[0]["id"] if rows else identifier()
            self.repo.execute(
                "INSERT INTO categories VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(ledger_id, name) DO UPDATE SET "
                "budget = excluded.budget, threshold = excluded.threshold",
                (category_id, ledger_id, name, budget, threshold),
            )
            self.repo.log(ledger_id, actor, "SET_CATEGORY", category_id)
            self.repo.remember(actor, request_id, "category", payload, category_id)
            return category_id
