"""Interactive desktop terminal client using authenticated service calls."""

import argparse
import json
import shlex
import sqlite3
from getpass import getpass

from .auth import AuthService
from .backup import BackupService
from .common import DomainError, identifier
from .database import Repository
from .expense_service import ExpenseService
from .export_service import ExportService
from .ledger_service import LedgerService
from .query_service import QueryService
from .settlement_service import SettlementService

HELP = """
合账：金额输入使用元，JSON 输出中的金额使用整数分。
register 用户名 [邮箱]           注册（交互输入密码）
login 用户名                    登录（交互输入密码）
logout                          注销
ledgers                         我的账本
create 名称                     新建账本
use 账本ID                      选择账本
members                         查看成员与用户ID
invite 用户ID MEMBER|READER      邀请已有账号
role 用户ID ADMIN|MEMBER|READER   修改角色
remove 用户ID 原因              强制移除
leave                           主动退出
status OPEN|CLOSED 原因          重开/关闭账本
category 名称 预算|- 大额阈值|-   设置分类（- 为未设置）
categories                      查看分类ID
submit                          交互输入账单 JSON
expenses [JSON筛选条件]          组合查询
show 账单ID                     账单详情、版本与审核记录
review 账单ID yes|no 版本 意见    审核
void 账单ID 版本 原因            作废/撤回
amend 账单ID 版本 原因           输入新账单 JSON 更正
adjustment 调整ID yes|no 意见     审核结算后调整
debts                           原始债务和待确认占用
pay 债务ID 金额                 标记付款
confirm 付款ID yes|no 意见        确认/拒绝收款
payments                        查看与自己相关的付款
adjustments                     查看待处理调整
stats                           分类、成员统计
statement [成员ID]              个人结算对账单
export expenses|debts|audit 路径 导出 CSV（不覆盖已有文件）
logs                            管理员操作日志
help                            显示帮助
quit                            退出

写操作自动生成请求ID并显示。重试：retry 请求ID 原命令
例如 retry abc123 pay 债务ID 12.50；相同键改变参数会被拒绝。
submit/amend 的 JSON 示例见 README.md。
"""


class Console:
    """Keep a session token in memory and resolve it before each command."""

    def __init__(self, repository):
        self.repo = repository
        self.auth = AuthService(repository)
        self.ledgers = LedgerService(repository)
        self.expenses = ExpenseService(repository)
        self.queries = QueryService(repository)
        self.exports = ExportService(repository)
        self.settlements = SettlementService(repository)
        self.token = None
        self.ledger_id = None

    @staticmethod
    def _decision(value):
        if value not in ("yes", "no"):
            raise DomainError("决定必须为 yes 或 no")
        return value == "yes"

    def dispatch(self, line):
        """Execute one parsed command; no SQL or arbitrary evaluation is exposed."""
        parts = shlex.split(line)
        if not parts:
            return None
        request_id = identifier()
        if parts[0] == "retry":
            if len(parts) < 3:
                raise DomainError("用法：retry 请求ID 原命令")
            request_id = parts[1]
            parts = parts[2:]
        command, *args = parts
        if command == "help":
            print(HELP)
            return None
        if command == "register":
            return self.auth.register(
                args[0], getpass("密码（至少8位）："), args[1] if len(args) > 1 else ""
            )
        if command == "login":
            self.token = self.auth.login(args[0], getpass("密码："))
            return "登录成功"
        actor = self.auth.authenticate(self.token)
        if command == "logout":
            self.auth.logout(self.token)
            self.token = None
            self.ledger_id = None
            return "已注销"
        if command == "ledgers":
            return self.ledgers.list_ledgers(actor)
        if command == "create":
            print("请求ID：", request_id)
            self.ledger_id = self.ledgers.create(actor, " ".join(args), request_id)
            return self.ledger_id
        if command == "use":
            self.ledgers.member(actor, args[0], active=False)
            self.ledger_id = args[0]
            return "已选择账本"
        if not self.ledger_id:
            raise DomainError("请先 use 账本ID，或 create 名称")
        return self._ledger_command(actor, command, args, request_id)

    def _ledger_command(self, actor, command, args, request_id):
        ledger_id = self.ledger_id
        readers = {
            "members": self.queries.members,
            "categories": self.queries.categories,
            "stats": self.queries.statistics,
            "logs": self.queries.logs,
            "debts": self.settlements.list_debts,
        }
        if command in readers:
            return readers[command](actor, ledger_id)
        if command == "expenses":
            filters = json.loads(" ".join(args)) if args else {}
            return self.queries.expenses(actor, ledger_id, **filters)
        if command == "show":
            return self.queries.detail(actor, args[0])
        if command == "statement":
            return self.exports.statement(actor, ledger_id, args[0] if args else None)
        if command == "export":
            if args[0] not in ("expenses", "debts", "audit"):
                raise DomainError("导出类型必须为 expenses、debts 或 audit")
            exporter = getattr(self.exports, args[0])
            return exporter(actor, ledger_id, args[1])
        if command == "payments":
            return self.queries.payments(actor, ledger_id)
        if command == "adjustments":
            return self.queries.adjustments(actor, ledger_id)
        print("请求ID：", request_id)
        if command == "invite":
            return self.ledgers.invite(actor, ledger_id, args[0], args[1], request_id)
        if command == "role":
            return self.ledgers.set_role(actor, ledger_id, args[0], args[1], request_id)
        if command == "remove":
            return self.ledgers.remove(actor, ledger_id, args[0], " ".join(args[1:]), request_id)
        if command == "leave":
            return self.ledgers.leave(actor, ledger_id, request_id)
        if command == "status":
            return self.ledgers.change_status(
                actor, ledger_id, args[0], " ".join(args[1:]), request_id
            )
        if command == "category":
            budget = None if args[1] == "-" else args[1]
            threshold = None if args[2] == "-" else args[2]
            return self.ledgers.category(actor, ledger_id, args[0], budget, threshold, request_id)
        if command == "submit":
            data = json.loads(input("账单 JSON："))
            return self.expenses.submit(actor, ledger_id, data, request_id)
        if command == "review":
            return self.expenses.review(
                actor,
                args[0],
                self._decision(args[1]),
                " ".join(args[3:]),
                int(args[2]),
                request_id,
            )
        if command == "void":
            return self.expenses.void(actor, args[0], " ".join(args[2:]), int(args[1]), request_id)
        if command == "amend":
            data = json.loads(input("替换账单 JSON："))
            return self.expenses.amend(
                actor, args[0], data, " ".join(args[2:]), int(args[1]), request_id
            )
        if command == "adjustment":
            return self.expenses.review_adjustment(
                actor, args[0], self._decision(args[1]), " ".join(args[2:]), request_id
            )
        if command == "pay":
            return self.settlements.pay(actor, args[0], args[1], request_id)
        if command == "confirm":
            return self.settlements.confirm(
                actor, args[0], self._decision(args[1]), " ".join(args[2:]), request_id
            )
        raise DomainError("未知命令，输入 help 查看帮助")

    def run(self):
        """Continue after validation failures and terminate cleanly on EOF."""
        print("合账 · 多人共享记账系统；输入 help 查看命令")
        while True:
            try:
                line = input("合账> ").strip()
                if line in ("quit", "exit"):
                    return
                result = self.dispatch(line)
                if result is not None:
                    print(json.dumps(result, ensure_ascii=False, indent=2))
            except (EOFError, KeyboardInterrupt):
                print("\n已退出")
                return
            except (DomainError, IndexError, TypeError, ValueError) as exc:
                print(f"操作失败：{exc}；输入 help 查看参数格式")
            except sqlite3.Error as exc:
                print(f"数据库操作失败，事务已回滚：{exc}")


def main():
    """Launch terminal UI or an explicit local administrator backup command."""
    parser = argparse.ArgumentParser(description="合账多人共享记账系统")
    parser.add_argument("--db", default="hezhang.db", help="SQLite 数据库路径")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--backup", metavar="FILE", help="备份到新的本地文件")
    group.add_argument("--restore", metavar="FILE", help="从备份恢复到新的 --db 路径")
    args = parser.parse_args()
    try:
        if args.restore:
            print(json.dumps(BackupService.restore(args.restore, args.db)))
            return
        with Repository(args.db) as repository:
            if args.backup:
                print(json.dumps(BackupService.create(repository, args.backup)))
            else:
                Console(repository).run()
    except (DomainError, OSError, sqlite3.Error) as exc:
        parser.exit(1, f"操作失败：{exc}\n")
