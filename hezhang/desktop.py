"""Native Tk desktop windows; no HTTP server or external browser is required."""

import argparse
import json
import sqlite3
from datetime import datetime
from pathlib import Path

from .application import Application, seed_demo
from .common import DomainError, currency, identifier, money, now, split_amount
from .database import Repository

try:
    import tkinter as tk
    from tkinter import filedialog, font, messagebox, ttk
except ImportError:
    tk = None


GREEN = "#16745C"
DARK = "#173D33"
LIME = "#CBE9AF"
BG = "#F5F7F4"
INK = "#182E2A"
MUTED = "#75847F"
LABELS = {
    "ADMIN": "管理员",
    "MEMBER": "普通成员",
    "READER": "只读成员",
    "ACTIVE": "有效成员",
    "REMOVED": "已移除",
    "LEFT": "已退出",
    "OPEN": "开启中",
    "CLOSED": "已关闭",
    "PENDING": "待审核",
    "APPROVED": "已通过",
    "REJECTED": "已驳回",
    "VOID": "已作废",
    "CORRECTED": "已更正",
    "CONFIRMED": "已确认",
    "EXPENSE": "支出",
    "INCOME": "收入",
}
REASONS = {
    "OVER_BUDGET": "超出分类预算",
    "LARGE_AMOUNT": "大额支出",
    "POTENTIAL_DUPLICATE": "疑似重复",
}
NAVIGATION = [
    ("overview", "账本总览"),
    ("expenses", "账单明细"),
    ("new", "新增账单"),
    ("approval", "审核中心"),
    ("settlement", "债务结算"),
    ("stats", "统计报表"),
    ("members", "成员管理"),
    ("settings", "账本设置"),
]


def display_money(cents):
    """Format integer currency for labels and tables."""
    return "¥ " + currency(cents)


def display_time(value):
    """Display persisted UTC timestamps in the desktop user's timezone."""
    return datetime.fromisoformat(value).astimezone().strftime("%Y-%m-%d %H:%M")


def review_reasons(value):
    """Show every approval trigger in readable Chinese."""
    if isinstance(value, str):
        value = json.loads(value)
    return "、".join(REASONS.get(reason, reason) for reason in value) or "无需人工审核"


class Desktop:
    """Present existing authorized services in independent native windows."""

    def __init__(self, root, repository):
        self.root = root
        self.repo = repository
        self.service = Application(repository)
        self.token = None
        self.data = None
        self.ledger_id = None
        self.page = "overview"
        self.current_detail = None
        self.editing = None
        self.status = tk.StringVar(value="就绪 · 数据保存在本机")
        self.root.title("合账 · 多人共享记账系统")
        self.root.geometry("1440x950")
        self.root.minsize(1080, 720)
        self.root.configure(bg=BG)
        self.root.report_callback_exception = self.callback_error
        self.style()
        self.login()

    def style(self):
        """Use the experiment-two palette with readable native controls."""
        style = ttk.Style(self.root)
        style.theme_use("clam")
        self.root.option_add("*Font", ("Noto Sans CJK SC", 11))
        style.configure("TFrame", background=BG)
        style.configure("Card.TFrame", background="white")
        style.configure("TLabel", background=BG, foreground=INK, font=("Noto Sans CJK SC", 11))
        style.configure("Card.TLabel", background="white", foreground=INK)
        style.configure("Muted.TLabel", background=BG, foreground=MUTED)
        style.configure("Title.TLabel", font=("Noto Sans CJK SC", 23, "bold"))
        style.configure("Heading.TLabel", font=("Noto Sans CJK SC", 14, "bold"), background="white")
        style.configure(
            "Value.TLabel",
            font=("Noto Sans CJK SC", 25, "bold"),
            foreground=GREEN,
            background="white",
        )
        style.configure("TButton", padding=(14, 9), background="white", foreground=INK)
        style.configure("Primary.TButton", background=GREEN, foreground="white")
        style.map("Primary.TButton", background=[("active", DARK), ("disabled", "#A5B8AD")])
        style.configure("TEntry", padding=9, fieldbackground="#FBFCFA")
        style.configure("TCombobox", padding=7, fieldbackground="white")
        style.configure(
            "Treeview",
            rowheight=44,
            background="white",
            fieldbackground="white",
            foreground=INK,
            borderwidth=0,
            font=("Noto Sans CJK SC", 10),
        )
        style.configure(
            "Treeview.Heading",
            background="#F1F5F0",
            foreground=MUTED,
            padding=10,
            font=("Noto Sans CJK SC", 10),
        )
        style.map("Treeview", background=[("selected", "#D6EBD9")], foreground=[("selected", INK)])
        style.configure("TCheckbutton", background="white", foreground=INK)
        style.configure("TProgressbar", background=GREEN, troughcolor="#EDF1EA")

    def callback_error(self, _kind, error, _traceback):
        """Display expected validation failures instead of silent callback errors."""
        messagebox.showerror("操作失败", str(error), parent=self.root)
        self.status.set(str(error))

    @staticmethod
    def clear(widget):
        for child in widget.winfo_children():
            child.destroy()

    def button(self, parent, label, command, primary=False, side="left", enabled=True):
        control = ttk.Button(
            parent, text=label, command=command, style="Primary.TButton" if primary else "TButton"
        )
        control.pack(side=side, padx=(0, 10), pady=4)
        if not enabled:
            control.state(["disabled"])
        return control

    @staticmethod
    def entry(parent, label, value="", choices=None, secret=False):
        """Construct a labeled form field with a persistent StringVar."""
        wrapper = ttk.Frame(parent, style="Card.TFrame")
        wrapper.pack(fill="x", pady=7)
        ttk.Label(wrapper, text=label, style="Card.TLabel").pack(anchor="w", pady=(0, 5))
        variable = tk.StringVar(value=value)
        if choices is not None:
            widget = ttk.Combobox(wrapper, textvariable=variable, values=choices, state="readonly")
        else:
            widget = ttk.Entry(wrapper, textvariable=variable, show="•" if secret else "")
        widget.pack(fill="x")
        return variable

    def card(self, parent, title):
        frame = ttk.Frame(parent, style="Card.TFrame", padding=22)
        frame.pack(fill="x", pady=(0, 18))
        if title:
            ttk.Label(frame, text=title, style="Heading.TLabel").pack(anchor="w", pady=(0, 16))
        return frame

    def login(self, register=False):
        """Display login/registration without opening any browser or network port."""
        self.clear(self.root)
        container = tk.Frame(self.root, bg=BG)
        container.pack(fill="both", expand=True)
        art = tk.Frame(container, bg=DARK, width=500)
        art.pack(side="left", fill="both", expand=True)
        tk.Label(
            art, text="合  合账", bg=DARK, fg=LIME, font=("Noto Sans CJK SC", 30, "bold")
        ).pack(anchor="w", padx=55, pady=(130, 70))
        tk.Label(
            art,
            text="一起出发，\n账目有数。",
            justify="left",
            bg=DARK,
            fg="white",
            font=("Noto Sans CJK SC", 38, "bold"),
        ).pack(anchor="w", padx=55)
        tk.Label(
            art,
            text="记清每一笔共同开销\n让垫付、分摊与结算都清晰可追溯",
            justify="left",
            bg=DARK,
            fg="#BAD0C4",
            font=("Noto Sans CJK SC", 13),
        ).pack(anchor="w", padx=55, pady=28)
        panel = ttk.Frame(container, padding=55)
        panel.pack(side="left", fill="both", expand=True)
        form = self.card(panel, "创建你的账号" if register else "欢迎回到合账")
        username = self.entry(form, "用户名")
        password = self.entry(form, "密码（至少 8 位）" if register else "密码", secret=True)
        email = self.entry(form, "邮箱（可选）") if register else None
        actions = ttk.Frame(form, style="Card.TFrame")
        actions.pack(fill="x", pady=20)

        def submit():
            if register:
                self.service.auth.register(username.get(), password.get(), email.get())
                messagebox.showinfo("注册成功", "现在可以使用新账号登录。", parent=self.root)
                self.login()
            else:
                self.token = self.service.auth.login(username.get(), password.get())
                self.ledger_id = None
                self.page = "overview"
                self.refresh()

        self.button(actions, "注册账号" if register else "登录", submit, True)
        self.button(
            form, "返回登录" if register else "注册新账号", lambda: self.login(not register)
        )
        ttk.Label(
            panel,
            text="演示模式：林晓 / 陈晨 / 周宁 / 许禾\n密码均为 demo12345",
            style="Muted.TLabel",
        ).pack(anchor="w", pady=25)

    def writer(self):
        return self.active() and self.data["membership"]["role"] != "READER"

    def opened(self):
        return self.data.get("ledger", {}).get("status") == "OPEN"

    def person(self, user_id):
        for member in self.data.get("members", []):
            if member["user_id"] == user_id:
                return member["username"]
        return self.data.get("participant_names", {}).get(user_id, "历史成员")

    def category_name(self, category_id):
        return next(
            (item["name"] for item in self.data.get("categories", []) if item["id"] == category_id),
            "—",
        )

    def refresh(self):
        """Reload persisted service results after every successful write."""
        self.data = self.service.state(self.token, self.ledger_id)
        self.ledger_id = self.data.get("ledger", {}).get("id")
        if self.ledger_id and not self.active():
            self.page = "settlement"
        self.shell()

    def navigate(self, page):
        self.page = page
        self.editing = None
        self.shell()

    def shell(self):
        """Keep a fixed dark sidebar beside a scrollable page area."""
        self.clear(self.root)
        # Native point sizes grow with desktop scaling; reserve enough width for navigation.
        nav_font = font.Font(self.root, font=("Noto Sans CJK SC", 11))
        sidebar_width = max(226, nav_font.measure("08    账本设置") + 64)
        sidebar = tk.Frame(self.root, bg=DARK, width=sidebar_width)
        sidebar.pack(side="left", fill="y")
        sidebar.pack_propagate(False)
        brand = tk.Frame(sidebar, bg=DARK)
        brand.pack(fill="x", padx=20, pady=(28, 12))
        tk.Label(
            brand,
            text="合",
            bg=LIME,
            fg=DARK,
            font=("Noto Sans CJK SC", -24, "bold"),
            padx=7,
            pady=3,
        ).pack(side="left")
        tk.Label(
            brand,
            text="合账",
            bg=DARK,
            fg="white",
            font=("Noto Sans CJK SC", -26, "bold"),
            anchor="w",
        ).pack(side="left", padx=(12, 0), fill="x", expand=True)
        tk.Label(
            sidebar,
            text="让每一笔共同开销更清楚",
            bg=DARK,
            fg="#AFC7BB",
            font=("Noto Sans CJK SC", -13),
            anchor="w",
            justify="left",
            wraplength=sidebar_width - 40,
        ).pack(fill="x", padx=20, pady=(0, 25))
        brand.update_idletasks()
        sidebar.configure(width=max(sidebar_width, brand.winfo_reqwidth() + 40))
        ledgers = self.data["ledgers"]
        if ledgers:
            names = [f"{item['name']} · {item['id'][:6]}" for item in ledgers]
            picker = ttk.Combobox(sidebar, values=names, state="readonly", width=21)
            picker.pack(padx=16, pady=10)
            picker.current(
                next(index for index, item in enumerate(ledgers) if item["id"] == self.ledger_id)
            )

            def choose(_event):
                self.ledger_id = ledgers[picker.current()]["id"]
                self.page = "overview"
                self.refresh()

            picker.bind("<<ComboboxSelected>>", choose)
        for index, (page, label) in enumerate(NAVIGATION, 1):
            if not self.active() and self.ledger_id and page != "settlement":
                continue
            if page == "new" and not self.writer():
                continue
            if page in ("approval", "settings") and not self.admin():
                continue
            selected = page == self.page
            tk.Button(
                sidebar,
                text=f"{index:02}    {label}",
                anchor="w",
                relief="flat",
                bg=LIME if selected else DARK,
                fg=INK if selected else "#DCE9E1",
                activebackground=LIME,
                padx=16,
                pady=12,
                borderwidth=0,
                command=lambda target=page: self.navigate(target),
            ).pack(fill="x", padx=14, pady=4)
        profile = tk.Frame(sidebar, bg=DARK)
        profile.pack(side="bottom", fill="x", padx=20, pady=22)
        role = LABELS.get(self.data.get("membership", {}).get("role"), "用户")
        tk.Label(
            profile, text=f"{self.data['user']['username']} / {role}", bg=DARK, fg="white"
        ).pack(anchor="w")
        tk.Button(
            profile, text="退出登录", bg=DARK, fg="#BAD0C4", relief="flat", command=self.logout
        ).pack(anchor="w", pady=6)
        workspace = ttk.Frame(self.root)
        workspace.pack(side="left", fill="both", expand=True)
        top = ttk.Frame(workspace, padding=(28, 14), style="Card.TFrame")
        top.pack(fill="x")
        name = self.data.get("ledger", {}).get("name", "我的账本")
        ttk.Label(top, text=f"工作空间 / {name}", style="Card.TLabel").pack(side="left")
        self.button(top, "新建账本", self.create_ledger, side="right")
        self.button(top, "刷新", self.refresh, side="right")
        ttk.Label(workspace, textvariable=self.status, style="Muted.TLabel", padding=(28, 8)).pack(
            side="bottom", fill="x"
        )
        canvas = tk.Canvas(workspace, bg=BG, highlightthickness=0)
        scrollbar = ttk.Scrollbar(workspace, orient="vertical", command=canvas.yview)
        scrollbar.pack(side="right", fill="y")
        canvas.pack(fill="both", expand=True)
        canvas.configure(yscrollcommand=scrollbar.set)
        self.content = ttk.Frame(canvas, padding=28)
        window = canvas.create_window((0, 0), window=self.content, anchor="nw")
        self.content.bind(
            "<Configure>", lambda _event: canvas.configure(scrollregion=canvas.bbox("all"))
        )
        canvas.bind("<Configure>", lambda event: canvas.itemconfigure(window, width=event.width))
        if not self.ledger_id:
            self.heading("从一个共享账本开始", "创建账本，或让管理员用你的用户名邀请你。")
            self.button(self.content, "创建账本", self.create_ledger, True)
        else:
            getattr(self, "show_" + self.page)()

    def logout(self):
        self.service.auth.logout(self.token)
        self.token = None
        self.ledger_id = None
        self.login()

    def modal(self, title, fields, submit, note=""):
        """Keep a failed form and request key intact so users can safely retry."""
        window = tk.Toplevel(self.root)
        window.title(title)
        window.geometry("510x600")
        window.transient(self.root)
        window.grab_set()
        frame = ttk.Frame(window, padding=25, style="Card.TFrame")
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text=title, style="Heading.TLabel").pack(anchor="w", pady=(0, 16))
        if note:
            ttk.Label(frame, text=note, wraplength=440, style="Card.TLabel").pack(
                anchor="w", pady=8
            )
        variables = {}
        for name, label, value, choices in fields:
            variables[name] = self.entry(frame, label, value, choices)
        actions = ttk.Frame(frame, style="Card.TFrame")
        actions.pack(side="bottom", fill="x", pady=15)
        request_id = identifier()

        def save():
            try:
                submit({key: value.get() for key, value in variables.items()}, request_id)
            except (DomainError, ValueError, sqlite3.Error) as exc:
                messagebox.showerror("操作失败", str(exc), parent=window)
                return
            window.destroy()
            self.status.set("操作成功 · 已保存到本机数据库")
            self.refresh()

        self.button(actions, "确认", save, True, side="right")
        self.button(actions, "取消", window.destroy, side="right")

    def mutate(self, operation, values, request_id):
        return self.service.mutate(
            self.token, operation, {**values, "ledger_id": self.ledger_id, "request_id": request_id}
        )

    def create_ledger(self):
        def create(values, request_id):
            self.ledger_id = self.mutate("create", values, request_id)
            self.page = "overview"

        self.modal("创建共享账本", [("name", "账本名称", "", None)], create)

    def expense_rows(self, expenses):
        return [
            (
                row["id"],
                (
                    row["title"],
                    self.category_name(row["category_id"]),
                    self.person(row["submitter"]),
                    display_money(row["amount"]),
                    LABELS[row["status"]],
                    display_time(row["occurred_at"]),
                ),
            )
            for row in expenses
        ]

    def view_detail(self, tree):
        expense_id = self.selected(tree)
        self.current_detail = self.service.queries.detail(
            self.service.actor(self.token), expense_id
        )
        self.page = "detail"
        self.shell()

    def expense_table(self, parent, expenses):
        tree = self.table(
            parent,
            ["账单", "类别", "提交人", "金额", "状态", "消费时间"],
            self.expense_rows(expenses),
        )
        tree.bind("<Double-1>", lambda _event: self.view_detail(tree))
        actions = ttk.Frame(parent, style="Card.TFrame")
        actions.pack(fill="x", pady=(12, 0))
        self.button(actions, "查看选中账单", lambda: self.view_detail(tree))
        return tree

    def show_overview(self):
        self.heading("一起出发，账目有数", "当前账本累计 · 每一笔共同开销，都清晰可追溯")
        stats = self.data["statistics"]
        mine = next(row for row in stats["members"] if row["user_id"] == self.data["user"]["id"])
        pending = [row for row in self.data["expenses"] if row["status"] == "PENDING"]
        metrics = ttk.Frame(self.content)
        metrics.pack(fill="x", pady=(0, 18))
        values = [
            ("有效支出", display_money(stats["expense"])),
            ("我的垫付", display_money(mine["contributed"])),
            ("我的待收款", display_money(mine["receivable"])),
            ("待审核账单", f"{len(pending)} 笔"),
        ]
        for index, (label, value) in enumerate(values):
            frame = ttk.Frame(metrics, style="Card.TFrame", padding=18)
            frame.grid(row=0, column=index, padx=(0, 12), sticky="nsew")
            metrics.columnconfigure(index, weight=1)
            ttk.Label(frame, text=label, style="Card.TLabel").pack(anchor="w")
            ttk.Label(frame, text=value, style="Value.TLabel").pack(anchor="w", pady=(10, 0))
        if self.writer():
            actions = ttk.Frame(self.content)
            actions.pack(fill="x", pady=(0, 16))
            self.button(
                actions, "+ 新增账单", lambda: self.navigate("new"), True, enabled=self.opened()
            )
        recent = self.card(self.content, "最近账单")
        self.expense_table(recent, list(reversed(self.data["expenses"]))[:5])
        self.budgets(self.card(self.content, "预算概览"))
        tasks = self.card(self.content, "需要你处理")
        receipts = [
            row
            for row in self.data["payments"]
            if row["creditor"] == self.data["user"]["id"] and row["status"] == "PENDING"
        ]
        ttk.Label(
            tasks,
            text=f"{len(pending)} 笔待审核 · {len(receipts)} 笔待收款确认",
            style="Card.TLabel",
        ).pack(anchor="w")
        actions = ttk.Frame(tasks, style="Card.TFrame")
        actions.pack(fill="x", pady=10)
        if self.admin():
            self.button(actions, "去审核", lambda: self.navigate("approval"))
        self.button(actions, "查看结算", lambda: self.navigate("settlement"))

    def budgets(self, parent):
        for row in self.data["statistics"]["categories"]:
            amount = display_money(row["expense"])
            budget = display_money(row["budget"]) if row["budget"] is not None else "未设置"
            ttk.Label(
                parent, text=f"{row['name']}     {amount} / {budget}", style="Card.TLabel"
            ).pack(anchor="w", pady=(10, 5))
            ratio = min(100, row["expense"] / row["budget"] * 100) if row["budget"] else 0
            ttk.Progressbar(parent, value=ratio).pack(fill="x", pady=(0, 12))

    def show_expenses(self):
        self.heading("账单明细", "组合查询 · 双击账单查看详情、审核与更正历史")
        filters = self.card(self.content, "筛选条件")
        row = ttk.Frame(filters, style="Card.TFrame")
        row.pack(fill="x")
        variables = {}
        category_map = {
            "全部": None,
            **{item["name"]: item["id"] for item in self.data["categories"]},
        }
        member_map = {
            "全部": None,
            **{item["username"]: item["user_id"] for item in self.data["members"]},
        }
        status_map = {
            "全部": None,
            **{
                LABELS[key]: key for key in ("APPROVED", "PENDING", "REJECTED", "VOID", "CORRECTED")
            },
        }
        specifications = [
            ("category_id", "类别", category_map),
            ("user_id", "成员", member_map),
            ("status", "状态", status_map),
            ("minimum", "最低金额（元）", None),
            ("maximum", "最高金额（元）", None),
            ("start", "开始时间 ISO（可空）", None),
            ("end", "结束时间 ISO（可空）", None),
        ]
        for index, (name, label, mapping) in enumerate(specifications):
            group = ttk.Frame(row, style="Card.TFrame", padding=(0, 0, 14, 0))
            group.grid(row=index // 4, column=index % 4, sticky="ew")
            row.columnconfigure(index % 4, weight=1)
            variables[name] = self.entry(
                group, label, "全部" if mapping else "", list(mapping) if mapping else None
            )
        result = self.card(self.content, "全部账单")

        def search(reset=False):
            self.clear(result)
            options = {}
            for name, _label, mapping in specifications:
                value = variables[name].get()
                if reset:
                    value = "全部" if mapping else ""
                    variables[name].set(value)
                value = mapping.get(value) if mapping else value
                if value:
                    options[name] = value
            expenses = self.service.queries.expenses(
                self.service.actor(self.token), self.ledger_id, **options
            )
            self.expense_table(result, list(reversed(expenses)))

        buttons = ttk.Frame(filters, style="Card.TFrame")
        buttons.pack(fill="x", pady=10)
        self.button(buttons, "查询", search, True)
        self.button(buttons, "重置", lambda: search(True))
        self.button(
            buttons, "导出当前查询", lambda: self.export("expenses", variables, specifications)
        )
        search()

    def table(self, parent, columns, rows, height=6):
        """Native table with both scrollbars; row IDs remain stable domain IDs."""
        frame = ttk.Frame(parent, style="Card.TFrame")
        frame.pack(fill="x", expand=True)
        keys = [str(index) for index in range(len(columns))]
        tree = ttk.Treeview(frame, columns=keys, show="headings", height=min(9, max(3, height)))
        for key, label in zip(keys, columns, strict=True):
            tree.heading(key, text=label)
            tree.column(key, width=145, minwidth=85, stretch=True)
        vertical = ttk.Scrollbar(frame, orient="vertical", command=tree.yview)
        horizontal = ttk.Scrollbar(frame, orient="horizontal", command=tree.xview)
        tree.configure(yscrollcommand=vertical.set, xscrollcommand=horizontal.set)
        tree.grid(row=0, column=0, sticky="nsew")
        vertical.grid(row=0, column=1, sticky="ns")
        horizontal.grid(row=1, column=0, sticky="ew")
        frame.columnconfigure(0, weight=1)
        for row_id, values in rows:
            tree.insert("", "end", iid=row_id, values=values)
        return tree

    @staticmethod
    def selected(tree):
        selection = tree.selection()
        if not selection:
            raise DomainError("请先选中一条记录")
        return selection[0]

    def heading(self, title, subtitle):
        ttk.Label(self.content, text=title, style="Title.TLabel").pack(anchor="w")
        ttk.Label(self.content, text=subtitle, style="Muted.TLabel", wraplength=900).pack(
            anchor="w", pady=(8, 25)
        )

    def show_new(self):
        self.heading(
            "更正账单" if self.editing else "新增账单", "分别指定垫付人和承担人 · 三种分摊方式"
        )
        if not self.writer():
            raise DomainError("当前角色无记账权限")
        if not self.data["categories"]:
            ttk.Label(self.content, text="请先联系管理员在账本设置中添加类别。").pack(anchor="w")
            return
        source = self.editing
        categories = {item["name"]: item["id"] for item in self.data["categories"]}
        form = self.card(self.content, "01 消费信息")
        title = self.entry(form, "账单名称", source["title"] if source else "")
        amount = self.entry(form, "金额（元）", currency(source["amount"]) if source else "")
        category = self.entry(
            form,
            "消费类别",
            self.category_name(source["category_id"]) if source else next(iter(categories)),
            list(categories),
        )
        kind = self.entry(
            form, "收支类型", LABELS[source["kind"]] if source else "支出", ["支出", "收入"]
        )
        occurred = self.entry(
            form, "消费时间（含时区的 ISO 格式）", source["occurred_at"] if source else now()
        )
        people = [item for item in self.data["members"] if item["status"] == "ACTIVE"]
        allocations = self.card(self.content, "02 垫付与分摊")
        mode = self.entry(
            allocations,
            "分摊方式",
            "自定义金额" if source else "平均分摊",
            ["平均分摊", "按比例", "自定义金额"],
        )
        ttk.Label(
            allocations,
            text="每位成员一行：实际垫付金额 / 是否承担 / 比例或自定义金额。",
            style="Card.TLabel",
        ).pack(anchor="w", pady=10)
        rows = []
        for person in people:
            user_id = person["user_id"]
            frame = ttk.Frame(allocations, style="Card.TFrame")
            frame.pack(fill="x", pady=5)
            ttk.Label(frame, text=person["username"], width=12, style="Card.TLabel").pack(
                side="left"
            )
            paid = tk.StringVar(
                value=currency(source["contributions"].get(user_id, 0)) if source else ""
            )
            ttk.Entry(frame, textvariable=paid, width=15).pack(side="left", padx=10)
            participates = tk.BooleanVar(value=user_id in source["shares"] if source else True)
            ttk.Checkbutton(frame, text="参与承担", variable=participates).pack(
                side="left", padx=10
            )
            value = tk.StringVar(value=currency(source["shares"].get(user_id, 0)) if source else "")
            share_input = ttk.Entry(frame, textvariable=value, width=15)
            share_input.pack(side="left", padx=10)
            rows.append((user_id, paid, participates, value, share_input))
        preview = tk.StringVar(value="输入总额后可预览分摊；垫付合计必须等于账单金额。")
        ttk.Label(allocations, textvariable=preview, style="Card.TLabel", wraplength=900).pack(
            anchor="w", pady=18
        )
        reason = self.entry(allocations, "更正原因") if source else None
        modes = {"平均分摊": "equal", "按比例": "ratio", "自定义金额": "custom"}

        def update(*_args):
            selected = [user_id for user_id, _, checked, _, _ in rows if checked.get()]
            values = {
                user_id: value.get() for user_id, _, checked, value, _ in rows if checked.get()
            }
            for _, _, checked, _, widget in rows:
                widget.configure(
                    state="normal" if checked.get() and mode.get() != "平均分摊" else "disabled"
                )
            try:
                shares = split_amount(money(amount.get()), selected, modes[mode.get()], values)
                preview.set(
                    " / ".join(
                        f"{self.person(user_id)} {display_money(value)}"
                        for user_id, value in shares.items()
                    )
                )
            except DomainError as exc:
                preview.set(str(exc))

        amount.trace_add("write", update)
        mode.trace_add("write", update)
        for _, _, checked, value, _ in rows:
            checked.trace_add("write", update)
            value.trace_add("write", update)
        update()
        request_id = identifier()

        def save():
            expense = {
                "title": title.get(),
                "amount": amount.get(),
                "category_id": categories[category.get()],
                "kind": "INCOME" if kind.get() == "收入" else "EXPENSE",
                "occurred_at": occurred.get(),
                "contributions": {
                    user_id: paid.get()
                    for user_id, paid, _, _, _ in rows
                    if paid.get().strip() and paid.get().strip() not in ("0", "0.00")
                },
                "participants": [user_id for user_id, _, checked, _, _ in rows if checked.get()],
                "mode": modes[mode.get()],
                "values": {
                    user_id: value.get() for user_id, _, checked, value, _ in rows if checked.get()
                },
            }
            if source:
                self.mutate(
                    "amend",
                    {
                        "id": source["id"],
                        "version": source["version"],
                        "reason": reason.get(),
                        "expense": expense,
                    },
                    request_id,
                )
            else:
                self.mutate("submit", {"expense": expense}, request_id)
            self.editing = None
            self.page = "expenses"
            self.status.set("账单已保存，审核结果请在明细中查看")
            self.refresh()

        actions = ttk.Frame(allocations, style="Card.TFrame")
        actions.pack(fill="x", pady=10)
        self.button(
            actions,
            "提交更正" if source else "提交账单",
            save,
            True,
            enabled=bool(source) or self.opened(),
        )
        self.button(actions, "返回明细", lambda: self.navigate("expenses"))

    def show_approval(self):
        self.heading("审核中心", "查看全部触发原因，核实后再通过或驳回")
        pending = [row for row in self.data["expenses"] if row["status"] == "PENDING"]
        frame = self.card(self.content, "待审核账单")
        self.expense_table(frame, pending)
        adjustments = self.card(self.content, "结算后更正申请")
        rows = [
            (
                row["id"],
                (
                    row["title"],
                    self.person(row["requester"]),
                    row["reason"],
                    display_money(row["replacement"]["amount"]),
                    LABELS[row["status"]],
                ),
            )
            for row in self.data.get("adjustments", [])
        ]
        tree = self.table(adjustments, ["原账单", "申请人", "更正原因", "目标金额", "状态"], rows)
        actions = ttk.Frame(adjustments, style="Card.TFrame")
        actions.pack(fill="x", pady=12)
        for approve, label in ((True, "通过调整"), (False, "驳回调整")):
            self.button(
                actions,
                label,
                lambda decision=approve: self.reason_action(
                    "审核结算后更正", "adjustment", {"id": self.selected(tree), "approve": decision}
                ),
            )

    def reason_action(self, title, operation, values):
        self.modal(
            title,
            [("reason", "操作原因 / 意见", "", None)],
            lambda data, request_id: self.mutate(operation, {**values, **data}, request_id),
        )

    def show_settlement(self):
        self.heading("债务结算", "保留原始债务 · 收款人确认后才抵扣，可分次付款")
        if not self.active():
            ttk.Label(self.content, text="成员关系已终止，仅可处理与你相关的结算。").pack(
                anchor="w", pady=10
            )
        debts = self.card(self.content, "原始债务明细")
        rows = [
            (
                row["id"],
                (
                    row["title"],
                    self.person(row["debtor"]),
                    self.person(row["creditor"]),
                    display_money(row["amount"]),
                    display_money(row["paid"]),
                    display_money(row["remaining"]),
                    display_money(row["reserved"]),
                ),
            )
            for row in self.data["debts"]
        ]
        tree = self.table(
            debts, ["来源账单", "付款人", "收款人", "原金额", "已确认", "剩余", "待确认"], rows
        )
        actions = ttk.Frame(debts, style="Card.TFrame")
        actions.pack(fill="x", pady=12)

        def pay():
            debt_id = self.selected(tree)
            debt = next(row for row in self.data["debts"] if row["id"] == debt_id)
            if debt["debtor"] != self.data["user"]["id"]:
                raise DomainError("只能标记自己作为付款人的债务")
            self.modal(
                "标记付款",
                [
                    (
                        "amount",
                        "本次付款金额（元）",
                        currency(debt["remaining"] - debt["reserved"]),
                        None,
                    )
                ],
                lambda data, request_id: self.mutate("pay", {"id": debt_id, **data}, request_id),
            )

        self.button(
            actions, "标记付款", pay, True, enabled=self.data["membership"]["role"] != "READER"
        )
        payments = self.card(self.content, "付款及收款确认")
        rows = [
            (
                row["id"],
                (
                    self.person(row["debtor"]),
                    self.person(row["creditor"]),
                    display_money(row["amount"]),
                    display_time(row["created_at"]),
                    "待收款确认" if row["status"] == "PENDING" else LABELS[row["status"]],
                ),
            )
            for row in self.data["payments"]
        ]
        payment_tree = self.table(payments, ["付款人", "收款人", "金额", "标记时间", "状态"], rows)
        actions = ttk.Frame(payments, style="Card.TFrame")
        actions.pack(fill="x", pady=12)
        for accept, label in ((True, "确认收款"), (False, "未到账，拒绝确认")):
            self.button(
                actions,
                label,
                lambda decision=accept: self.reason_action(
                    "处理收款确认",
                    "confirm",
                    {"id": self.selected(payment_tree), "accept": decision},
                ),
                enabled=self.data["membership"]["role"] != "READER",
            )

    def active(self):
        return self.data.get("membership", {}).get("status") == "ACTIVE"

    def show_stats(self):
        self.heading("统计报表", "只统计当前已通过的有效版本，历史结算保持不变")
        stats = self.data["statistics"]
        summary = self.card(self.content, "收支总览")
        ttk.Label(
            summary,
            text=f"支出 {display_money(stats['expense'])}    收入 {display_money(stats['income'])}",
            style="Value.TLabel",
        ).pack(anchor="w")
        self.budgets(self.card(self.content, "分类支出与预算"))
        frame = self.card(self.content, "成员统计")
        rows = [
            (
                row["user_id"],
                (
                    row["username"],
                    display_money(row["contributed"]),
                    display_money(row["borne"]),
                    display_money(row["receivable"]),
                    display_money(row["payable"]),
                ),
            )
            for row in stats["members"]
        ]
        self.table(frame, ["成员", "垫付", "承担", "应收", "应付"], rows)

    def show_members(self):
        self.heading("成员管理", "角色权限实时生效，退出或移除不会删除历史记录")
        frame = self.card(self.content, "账本成员")
        rows = [
            (
                row["user_id"],
                (
                    row["username"],
                    LABELS[row["role"]],
                    LABELS[row["status"]],
                    display_time(row["joined_at"]),
                ),
            )
            for row in self.data["members"]
        ]
        tree = self.table(frame, ["成员", "角色", "状态", "加入时间"], rows)
        actions = ttk.Frame(frame, style="Card.TFrame")
        actions.pack(fill="x", pady=12)
        roles = {"管理员": "ADMIN", "普通成员": "MEMBER", "只读成员": "READER"}
        if self.admin():
            self.button(
                actions,
                "邀请成员",
                lambda: self.modal(
                    "邀请已有账号",
                    [
                        ("username", "对方已注册的用户名", "", None),
                        ("role", "角色", "普通成员", ["普通成员", "只读成员"]),
                    ],
                    lambda data, request_id: self.mutate(
                        "invite", {**data, "role": roles[data["role"]]}, request_id
                    ),
                ),
                True,
                enabled=self.opened(),
            )
            self.button(
                actions,
                "设置选中成员角色",
                lambda: self.modal(
                    "设置角色",
                    [("role", "角色", "普通成员", list(roles))],
                    lambda data, request_id: self.mutate(
                        "role", {"id": self.selected(tree), "role": roles[data["role"]]}, request_id
                    ),
                ),
                enabled=self.opened(),
            )
            self.button(
                actions,
                "移除选中成员",
                lambda: self.reason_action(
                    "移除成员（债务保留）", "remove", {"id": self.selected(tree)}
                ),
            )
        self.button(
            actions,
            "退出当前账本",
            lambda: self.modal(
                "退出账本", [], lambda _data, request_id: self.mutate("leave", {}, request_id)
            ),
        )

    def show_settings(self):
        self.heading("账本设置与日志", "管理分类预算、大额阈值和账本状态")
        frame = self.card(self.content, "分类预算")
        rows = [
            (
                row["id"],
                (
                    row["name"],
                    display_money(row["budget"]) if row["budget"] is not None else "未设置",
                    display_money(row["threshold"]) if row["threshold"] else "未设置",
                ),
            )
            for row in self.data["categories"]
        ]
        tree = self.table(frame, ["类别", "累计预算", "大额阈值"], rows)
        actions = ttk.Frame(frame, style="Card.TFrame")
        actions.pack(fill="x", pady=12)
        self.button(
            actions, "添加类别", lambda: self.category_dialog(), True, enabled=self.opened()
        )
        self.button(
            actions,
            "编辑选中类别",
            lambda: self.category_dialog(self.selected(tree)),
            enabled=self.opened(),
        )
        status = self.card(self.content, "账本状态")
        ttk.Label(
            status,
            text=f"当前：{LABELS[self.data['ledger']['status']]}。关闭后仍可审核与结算。",
            style="Card.TLabel",
        ).pack(anchor="w")
        self.button(
            status,
            "关闭账本" if self.opened() else "重新开启",
            lambda: self.reason_action(
                "变更账本状态", "status", {"status": "CLOSED" if self.opened() else "OPEN"}
            ),
        )
        logs = self.card(self.content, "关键操作日志")
        rows = [
            (
                row["id"],
                (
                    display_time(row["created_at"]),
                    self.person(row["actor"]),
                    row["action"],
                    row["object_id"][:12],
                    row["reason"],
                ),
            )
            for row in reversed(self.data.get("logs", []))
        ]
        self.table(logs, ["时间", "操作人", "动作", "对象", "原因"], rows)

    def admin(self):
        return self.active() and self.data["membership"]["role"] == "ADMIN"

    def category_dialog(self, category_id=None):
        category = next((row for row in self.data["categories"] if row["id"] == category_id), {})
        self.modal(
            "分类预算与阈值",
            [
                ("name", "类别名称（改名会新建类别）", category.get("name", ""), None),
                (
                    "budget",
                    "累计预算（元，留空为未设置）",
                    currency(category["budget"]) if category.get("budget") is not None else "",
                    None,
                ),
                (
                    "threshold",
                    "大额阈值（元，留空不启用）",
                    currency(category["threshold"]) if category.get("threshold") else "",
                    None,
                ),
            ],
            lambda data, request_id: self.mutate("category", data, request_id),
        )

    def show_detail(self):
        source = self.current_detail
        source = self.service.queries.detail(self.service.actor(self.token), source["id"])
        self.current_detail = source
        self.heading("账单详情与更正", "消费、分摊、审核与历史记录均可追溯")
        frame = self.card(self.content, source["title"])
        fields = [
            ("金额", display_money(source["amount"])),
            ("状态", LABELS[source["status"]]),
            ("类别", self.category_name(source["category_id"])),
            ("提交人", self.person(source["submitter"])),
            ("版本", str(source["version"])),
            ("审核原因", review_reasons(source["reasons"])),
            ("消费时间", display_time(source["occurred_at"])),
            ("账单 ID", source["id"]),
        ]
        for label, value in fields:
            ttk.Label(frame, text=f"{label}：{value}", style="Card.TLabel", wraplength=900).pack(
                anchor="w", pady=5
            )
        actions = ttk.Frame(frame, style="Card.TFrame")
        actions.pack(fill="x", pady=14)
        if self.admin() and source["status"] == "PENDING":
            for approve, label in ((True, "审核通过"), (False, "驳回账单")):
                self.button(
                    actions,
                    label,
                    lambda decision=approve: self.reason_action(
                        "审核账单",
                        "review",
                        {"id": source["id"], "version": source["version"], "approve": decision},
                    ),
                )
        own = self.writer() and (self.admin() or source["submitter"] == self.data["user"]["id"])
        if own and source["status"] == "APPROVED":

            def amend():
                self.editing = source
                self.page = "new"
                self.shell()

            self.button(actions, "修改 / 申请更正", amend)
        if own and source["status"] in ("APPROVED", "PENDING"):
            self.button(
                actions,
                "作废 / 撤回",
                lambda: self.reason_action(
                    "作废或撤回账单", "void", {"id": source["id"], "version": source["version"]}
                ),
            )
        self.button(actions, "返回明细", lambda: self.navigate("expenses"))
        for key, label in (("contributions", "垫付明细"), ("shares", "承担明细")):
            frame = self.card(self.content, label)
            self.table(
                frame,
                ["成员", "金额"],
                [
                    (user_id, (self.person(user_id), display_money(value)))
                    for user_id, value in source[key].items()
                ],
                3,
            )
        frame = self.card(self.content, "审核记录")
        rows = [
            (
                row["id"],
                (
                    display_time(row["created_at"]),
                    self.person(row["reviewer"]) if row["reviewer"] else "系统自动审核",
                    LABELS[row["decision"]],
                    row["reason"],
                ),
            )
            for row in source["reviews"]
        ]
        self.table(frame, ["时间", "审核人", "决定", "意见"], rows, 3)
        frame = self.card(self.content, "历史版本与调整")
        ttk.Label(
            frame,
            text=(
                f"保留 {len(source['history'])} 个历史快照，"
                f"{len(source['adjustments'])} 个调整申请。"
            ),
            style="Card.TLabel",
        ).pack(anchor="w")
        if source["history"]:
            snapshots = "\n\n".join(
                json.dumps(json.loads(row["snapshot"]), ensure_ascii=False, indent=2)
                for row in source["history"]
            )
            text = tk.Text(frame, height=8, wrap="word", bg="#FBFCFA", fg=INK, relief="flat")
            text.insert("1.0", snapshots)
            text.configure(state="disabled")
            text.pack(fill="x", pady=12)

    def export(self, kind, variables=None, specifications=None):
        """Save through existing authorized exporters, without overwriting files."""
        from .export_service import ExportService

        path = filedialog.asksaveasfilename(
            parent=self.root,
            title="导出 CSV（请选择新文件名）",
            defaultextension=".csv",
            filetypes=[("CSV", "*.csv")],
        )
        if not path:
            return
        filters = {}
        for name, _label, mapping in specifications or []:
            value = variables[name].get()
            value = mapping.get(value) if mapping else value
            if value:
                filters[name] = value
        exporter = getattr(ExportService(self.repo), kind)
        exporter(self.service.actor(self.token), self.ledger_id, path, **filters)
        self.status.set(f"已导出：{path}")


def main():
    """Default native desktop entry point with a clear missing-component message."""
    parser = argparse.ArgumentParser(description="合账 · 原生桌面应用（无需浏览器）")
    parser.add_argument("--db", help="本地 SQLite 数据库路径")
    parser.add_argument("--demo", action="store_true", help="空演示库填充实验二示例")
    args = parser.parse_args()
    if tk is None:
        parser.exit(
            1,
            "当前 Python 缺少 Tkinter。Ubuntu/Debian 请安装：sudo apt install python3-tk\n"
            "Windows/macOS 请使用包含 Tcl/Tk 的 Python 安装程序。\n",
        )
    try:
        root = tk.Tk()
    except tk.TclError as exc:
        parser.exit(1, f"无法创建桌面窗口：{exc}。请在本机图形桌面的终端中运行。\n")
    path = args.db or ("data/desktop-demo.db" if args.demo else "data/hezhang.db")
    try:
        with Repository(Path(path)) as repository:
            if args.demo:
                seed_demo(repository)
            Desktop(root, repository)
            root.mainloop()
    except (OSError, sqlite3.Error, DomainError) as exc:
        root.destroy()
        parser.exit(1, f"启动失败：{exc}\n")


if __name__ == "__main__":
    main()
