#!/usr/bin/env python3
"""Tail JetBrains IDEA SQL logs and rebuild executable SQL statements.

Python 3.12, standard library only.
"""

from __future__ import annotations

import ctypes
import json
import os
import platform
import queue
import re
import threading
import time
import tkinter as tk
import webbrowser
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox


APP_TITLE = "SQL Monitor"
APP_VERSION = "v1.0.2"
APP_BRAND = "菜鸟驿站出品"
THEME_TOGGLE_TEXT = "切换主题"
LATEST_VERSION_TEXT = "获取最新版本"
LATEST_VERSION_URL = "https://www.cainiao.com/"
CONFIG_FILE = Path.home() / ".sql_monitor_config.json"
DEFAULT_GEOMETRY = "1120x760"
DEFAULT_MAX_LOG_COUNT = "1000"
POLL_MS = 40
TAIL_SLEEP_SECONDS = 0.05
LINE_QUEUE_SIZE = 10000
EVENT_QUEUE_SIZE = 10000
SCROLL_BOTTOM_THRESHOLD = 0.999
STATUS_ANIMATION_MS = 120
STATUS_FRAMES = ("⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏")
TIMESTAMP_RE = re.compile(r"^\s*(?P<timestamp>\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2})\b\s*(?P<body>.*)$")
DIRECT_PRINT_KEYWORDS = [
    "sql id",
    "sqlId",
    "statement id",
    "MappedStatement",
]

SQL_KEYWORDS = {
    "ADD", "ALL", "ALTER", "AND", "ANY", "AS", "ASC", "BETWEEN", "BY", "CASE", "CHECK",
    "COLUMN", "CONSTRAINT", "CREATE", "CROSS", "DATABASE", "DEFAULT", "DELETE", "DESC",
    "DISTINCT", "DROP", "ELSE", "END", "EXISTS", "FALSE", "FOREIGN", "FROM", "FULL",
    "GROUP", "HAVING", "IN", "INDEX", "INNER", "INSERT", "INTERSECT", "INTO", "IS", "JOIN",
    "KEY", "LEFT", "LIKE", "LIMIT", "NOT", "NULL", "ON", "OR", "ORDER", "OUTER", "PRIMARY",
    "REFERENCES", "RIGHT", "SELECT", "SET", "TABLE", "THEN", "TRUE", "UNION", "UNIQUE",
    "UPDATE", "VALUES", "WHEN", "WHERE", "WITH",
}
SQL_TYPES = {
    "BIGINT", "BINARY", "BIT", "BLOB", "BOOLEAN", "CHAR", "CLOB", "DATE", "DATETIME",
    "DECIMAL", "DOUBLE", "FLOAT", "INT", "INTEGER", "JSON", "LONGTEXT", "NUMERIC", "REAL",
    "SMALLINT", "TEXT", "TIME", "TIMESTAMP", "TINYINT", "VARCHAR",
}
SQL_FUNCTIONS = {
    "AVG", "CAST", "COALESCE", "COUNT", "CURRENT_DATE", "CURRENT_TIME", "CURRENT_TIMESTAMP",
    "IFNULL", "LOWER", "MAX", "MIN", "NOW", "NVL", "SUM", "TRIM", "UPPER",
}

THEMES = {
    "light": {
        "bg": "#f1f2f4",
        "panel": "#f1f2f4",
        "fg": "#222222",
        "muted_fg": "#69707a",
        "entry_bg": "#ffffff",
        "entry_fg": "#111111",
        "text_bg": "#ffffff",
        "text_fg": "#111111",
        "button_bg": "#ffffff",
        "button_hover_bg": "#e9edf3",
        "button_disabled_bg": "#edf0f3",
        "button_disabled_fg": "#9aa1aa",
        "button_fg": "#222222",
        "border": "#b7bdc7",
        "active_border": "#2f7ee6",
        "select_bg": "#cfe8ff",
        "select_fg": "#111111",
        "search_bg": "#fff59d",
        "search_fg": "#111111",
        "search_current_bg": "#ffcc66",
        "search_current_fg": "#111111",
        "timestamp_fg": "#6a737d",
        "sql_keyword_fg": "#0033b3",
        "sql_type_fg": "#00627a",
        "sql_function_fg": "#7a3e9d",
        "sql_string_fg": "#067d17",
        "sql_number_fg": "#1750eb",
        "sql_comment_fg": "#8c8c8c",
        "sql_operator_fg": "#9a6700",
        "menu_active_bg": "#dbeafe",
        "menu_active_fg": "#111111",
        "link": "#0b63ce",
    },
    "dark": {
        "bg": "#0d1117",
        "panel": "#161b22",
        "surface": "#21262d",
        "fg": "#c9d1d9",
        "muted_fg": "#8b949e",
        "entry_bg": "#0d1117",
        "entry_fg": "#e6edf3",
        "text_bg": "#0b0f14",
        "text_fg": "#dbe7f3",
        "button_bg": "#21262d",
        "button_hover_bg": "#30363d",
        "button_disabled_bg": "#161b22",
        "button_disabled_fg": "#6e7681",
        "button_fg": "#f0f6fc",
        "border": "#30363d",
        "active_border": "#58a6ff",
        "select_bg": "#264f78",
        "select_fg": "#ffffff",
        "search_bg": "#664d00",
        "search_fg": "#ffffff",
        "search_current_bg": "#b7791f",
        "search_current_fg": "#ffffff",
        "timestamp_fg": "#6e7681",
        "sql_keyword_fg": "#cf8e6d",
        "sql_type_fg": "#56a8f5",
        "sql_function_fg": "#dcdcaa",
        "sql_string_fg": "#6aab73",
        "sql_number_fg": "#2aacb8",
        "sql_comment_fg": "#7a7e85",
        "sql_operator_fg": "#b9c0c9",
        "menu_active_bg": "#2f3847",
        "menu_active_fg": "#f0f6fc",
        "link": "#58a6ff",
    },
}


MYBATIS_PREPARING_RE = re.compile(r"==>\s+Preparing:\s*(?P<sql>.*)$")
MYBATIS_PARAMETERS_RE = re.compile(r"==>\s+Parameters:\s*(?P<params>.*)$")
SPRING_SQL_RE = re.compile(
    r"JdbcTemplate\s*[: -]+.*?Executing prepared SQL statement\s*\[(?P<sql>.*)\]"
)
SPRING_PARAM_RE = re.compile(
    r"Setting SQL statement parameter value:\s*column index\s+(?P<index>\d+),\s*"
    r"parameter value\s*\[(?P<value>.*?)\],\s*value class\s*\[(?P<class>.*?)\]",
    re.IGNORECASE,
)


@dataclass
class Param:
    value: str
    type_name: str = ""


@dataclass
class PendingSql:
    source: str
    template: str
    params: dict[int, Param]
    timestamp: str

    @property
    def placeholder_count(self) -> int:
        return count_placeholders(self.template)

    def ordered_params(self) -> list[Param]:
        return [self.params[index] for index in sorted(self.params)]


def current_timestamp() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def split_timestamp(line: str) -> tuple[str, str]:
    match = TIMESTAMP_RE.match(line)
    if match:
        return match.group("timestamp"), match.group("body")
    return current_timestamp(), line


def count_placeholders(sql: str) -> int:
    return sum(1 for _ in iter_placeholder_positions(sql))


def iter_placeholder_positions(sql: str):
    in_single = False
    in_double = False
    in_backtick = False
    in_line_comment = False
    in_block_comment = False
    index = 0
    while index < len(sql):
        char = sql[index]
        nxt = sql[index + 1] if index + 1 < len(sql) else ""

        if in_line_comment:
            if char in "\r\n":
                in_line_comment = False
            index += 1
            continue
        if in_block_comment:
            if char == "*" and nxt == "/":
                in_block_comment = False
                index += 2
            else:
                index += 1
            continue

        if not in_single and not in_double and not in_backtick:
            if char == "-" and nxt == "-":
                in_line_comment = True
                index += 2
                continue
            if char == "/" and nxt == "*":
                in_block_comment = True
                index += 2
                continue

        if char == "'" and not in_double and not in_backtick:
            if in_single and nxt == "'":
                index += 2
                continue
            in_single = not in_single
        elif char == '"' and not in_single and not in_backtick:
            in_double = not in_double
        elif char == "`" and not in_single and not in_double:
            in_backtick = not in_backtick
        elif char == "?" and not in_single and not in_double and not in_backtick:
            yield index
        index += 1


def replace_placeholders(sql: str, params: list[Param]) -> str:
    parts: list[str] = []
    last = 0
    for param, position in zip(params, iter_placeholder_positions(sql), strict=False):
        parts.append(sql[last:position])
        parts.append(format_sql_literal(param))
        last = position + 1
    parts.append(sql[last:])
    return "".join(parts)


def format_sql_literal(param: Param) -> str:
    raw_value = param.value.strip()
    type_name = param.type_name.lower()
    if raw_value.lower() in {"null", "<null>", "none"}:
        return "NULL"
    if raw_value.lower() in {"true", "false"} and "string" not in type_name:
        return raw_value.upper()

    numeric_types = (
        "int", "long", "short", "byte", "double", "float", "bigdecimal", "biginteger", "number",
    )
    if any(token in type_name for token in numeric_types) and is_number(raw_value):
        return raw_value
    if not type_name and is_number(raw_value):
        return raw_value

    escaped = raw_value.replace("'", "''")
    return f"'{escaped}'"


def is_number(value: str) -> bool:
    return bool(re.fullmatch(r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)", value.strip()))


def parse_mybatis_parameters(raw: str) -> list[Param]:
    raw = raw.strip()
    if not raw:
        return []
    params: list[Param] = []
    position = 0
    pattern = re.compile(r"\s*(?P<value>.*?)(?:\((?P<type>[^()]*)\))(?P<sep>,\s*|$)")
    while position < len(raw):
        match = pattern.match(raw, position)
        if not match:
            remainder = raw[position:].strip().strip(",")
            if remainder:
                params.append(Param(remainder, ""))
            break
        params.append(Param(match.group("value").strip(), match.group("type").strip()))
        position = match.end()
    return params


class SqlLogParser:
    def __init__(self, event_queue: queue.Queue[tuple[str, str]]) -> None:
        self.event_queue = event_queue
        self.pending: PendingSql | None = None

    def parse_line(self, line: str) -> None:
        timestamp, body = split_timestamp(line)

        if self.should_print_directly(body):
            self.emit_direct_line(timestamp, body)

        if match := MYBATIS_PREPARING_RE.search(body):
            self.pending = PendingSql("MyBatis", match.group("sql").strip(), {}, timestamp)
            if self.pending.placeholder_count == 0:
                self.emit_sql(self.pending.timestamp, self.pending.template)
                self.pending = None
            return

        if match := MYBATIS_PARAMETERS_RE.search(body):
            if self.pending and self.pending.source == "MyBatis":
                params = parse_mybatis_parameters(match.group("params"))
                self.emit_sql(self.pending.timestamp, replace_placeholders(self.pending.template, params))
                self.pending = None
            return

        if match := SPRING_SQL_RE.search(body):
            self.pending = PendingSql("Spring JdbcTemplate", match.group("sql").strip(), {}, timestamp)
            if self.pending.placeholder_count == 0:
                self.emit_sql(self.pending.timestamp, self.pending.template)
                self.pending = None
            return

        if match := SPRING_PARAM_RE.search(body):
            if self.pending and self.pending.source == "Spring JdbcTemplate":
                index = int(match.group("index"))
                self.pending.params[index] = Param(match.group("value"), match.group("class"))
                if len(self.pending.params) >= self.pending.placeholder_count:
                    self.emit_sql(
                        self.pending.timestamp,
                        replace_placeholders(self.pending.template, self.pending.ordered_params()),
                    )
                    self.pending = None

    def emit_sql(self, timestamp: str, sql: str) -> None:
        normalized = " ".join(sql.split())
        if normalized:
            self.put_event(("sql", f"{timestamp} {normalized};"))

    def emit_direct_line(self, timestamp: str, line: str) -> None:
        normalized = " ".join(line.split())
        if normalized:
            self.put_event(("sql", f"{timestamp} {normalized}"))

    def should_print_directly(self, line: str) -> bool:
        lowered_line = line.lower()
        return any(keyword.lower() in lowered_line for keyword in DIRECT_PRINT_KEYWORDS)

    def put_event(self, event: tuple[str, str]) -> None:
        try:
            self.event_queue.put_nowait(event)
        except queue.Full:
            pass


class TailReader(threading.Thread):
    def __init__(self, path: str, line_queue: queue.Queue[str], stop_event: threading.Event) -> None:
        super().__init__(daemon=True)
        self.path = path
        self.line_queue = line_queue
        self.stop_event = stop_event

    def run(self) -> None:
        try:
            with open(self.path, "r", encoding="utf-8", errors="replace") as file:
                file.seek(0, os.SEEK_END)
                while not self.stop_event.is_set():
                    line = file.readline()
                    if line:
                        self.enqueue(line.rstrip("\r\n"))
                        continue
                    try:
                        if os.path.getsize(self.path) < file.tell():
                            file.seek(0, os.SEEK_SET)
                    except OSError:
                        pass
                    time.sleep(TAIL_SLEEP_SECONDS)
        except OSError as exc:
            self.enqueue(f"[读取日志失败] {exc}")

    def enqueue(self, line: str) -> None:
        try:
            self.line_queue.put_nowait(line)
        except queue.Full:
            pass


class Analyzer(threading.Thread):
    def __init__(
        self,
        line_queue: queue.Queue[str],
        event_queue: queue.Queue[tuple[str, str]],
        stop_event: threading.Event,
    ) -> None:
        super().__init__(daemon=True)
        self.line_queue = line_queue
        self.event_queue = event_queue
        self.stop_event = stop_event
        self.parser = SqlLogParser(event_queue)

    def run(self) -> None:
        while not self.stop_event.is_set() or not self.line_queue.empty():
            try:
                line = self.line_queue.get(timeout=0.1)
            except queue.Empty:
                continue
            self.parser.parse_line(line)
            self.line_queue.task_done()


class ThemedScrollbar(tk.Canvas):
    def __init__(self, parent: tk.Widget, command) -> None:
        super().__init__(parent, width=14, highlightthickness=0, borderwidth=0)
        self.command = command
        self.first = 0.0
        self.last = 1.0
        self.drag_start_y: int | None = None
        self.drag_start_first = 0.0
        self.track_color = "#161b22"
        self.thumb_color = "#30363d"
        self.thumb_hover_color = "#484f58"
        self.is_hovering = False

        self.bind("<Configure>", lambda _event: self.redraw())
        self.bind("<Button-1>", self.on_button_press)
        self.bind("<B1-Motion>", self.on_drag)
        self.bind("<ButtonRelease-1>", self.on_button_release)
        self.bind("<Enter>", self.on_enter)
        self.bind("<Leave>", self.on_leave)

    def set_theme(self, track_color: str, thumb_color: str, thumb_hover_color: str) -> None:
        self.track_color = track_color
        self.thumb_color = thumb_color
        self.thumb_hover_color = thumb_hover_color
        self.configure(bg=track_color)
        self.redraw()

    def set(self, first: str, last: str) -> None:
        self.first = max(0.0, min(1.0, float(first)))
        self.last = max(self.first, min(1.0, float(last)))
        self.redraw()

    def redraw(self) -> None:
        self.delete("thumb")
        height = max(1, self.winfo_height())
        width = max(1, self.winfo_width())
        thumb_top = int(self.first * height)
        thumb_bottom = int(self.last * height)
        if thumb_bottom - thumb_top < 28:
            thumb_bottom = min(height, thumb_top + 28)
        color = self.thumb_hover_color if self.is_hovering else self.thumb_color
        self.create_rectangle(
            3,
            thumb_top + 2,
            width - 3,
            max(thumb_top + 3, thumb_bottom - 2),
            fill=color,
            outline=color,
            tags="thumb",
        )

    def on_button_press(self, event: tk.Event) -> None:
        self.drag_start_y = event.y
        self.drag_start_first = self.first
        top, bottom = self.thumb_bounds()
        if event.y < top or event.y > bottom:
            self.scroll_to_pointer(event.y)

    def on_drag(self, event: tk.Event) -> None:
        if self.drag_start_y is None:
            return
        height = max(1, self.winfo_height())
        view_size = max(0.01, self.last - self.first)
        delta = (event.y - self.drag_start_y) / height
        target = max(0.0, min(1.0 - view_size, self.drag_start_first + delta))
        self.command("moveto", target)

    def on_button_release(self, _event: tk.Event) -> None:
        self.drag_start_y = None

    def on_enter(self, _event: tk.Event) -> None:
        self.is_hovering = True
        self.redraw()

    def on_leave(self, _event: tk.Event) -> None:
        self.is_hovering = False
        self.redraw()

    def scroll_to_pointer(self, y: int) -> None:
        height = max(1, self.winfo_height())
        view_size = max(0.01, self.last - self.first)
        target = max(0.0, min(1.0 - view_size, (y / height) - (view_size / 2)))
        self.command("moveto", target)

    def thumb_bounds(self) -> tuple[int, int]:
        height = max(1, self.winfo_height())
        return int(self.first * height), int(self.last * height)


class SQLMonitorApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title(APP_TITLE)
        self.config_data = self.load_config()
        self.theme_name = str(self.config_data.get("theme", "light"))
        self.apply_window_appearance()
        self.minsize(860, 560)

        self.path_var = tk.StringVar()
        self.max_log_var = tk.StringVar(value=DEFAULT_MAX_LOG_COUNT)
        self.status_var = tk.StringVar(value="准备")
        self.search_var = tk.StringVar()
        self.last_normal_geometry = str(self.config_data.get("geometry", DEFAULT_GEOMETRY))

        self.stop_event: threading.Event | None = None
        self.reader: TailReader | None = None
        self.analyzer: Analyzer | None = None
        self.line_queue: queue.Queue[str] | None = None
        self.event_queue: queue.Queue[tuple[str, str]] | None = None
        self.log_count = 0
        self.auto_scroll = True
        self.listening = False
        self.status_frame_index = 0
        self.status_animation_job: str | None = None
        self.search_window: tk.Toplevel | None = None
        self.search_entry: tk.Entry | None = None
        self.theme_widgets: list[tk.Widget] = []
        self.button_widgets: list[tk.Label] = []
        self.entry_widgets: list[tk.Entry] = []
        self.button_commands: dict[tk.Label, object] = {}

        self.configure_grid()
        self.build_controls()
        self.build_output_windows()
        self.restore_config()
        self.apply_theme()
        self.update_button_states()
        self.bind("<Configure>", self.remember_normal_geometry)
        self.bind_all("<Control-f>", self.open_search)
        self.bind_all("<Control-F>", self.open_search)
        self.protocol("WM_DELETE_WINDOW", self.on_close)
        self.after(POLL_MS, self.poll_events)

    def configure_grid(self) -> None:
        self.columnconfigure(0, weight=1)
        self.rowconfigure(2, weight=1)

    def build_controls(self) -> None:
        frame = tk.Frame(self, padx=8, pady=8)
        frame.grid(row=0, column=0, sticky="ew")
        frame.columnconfigure(1, weight=1)
        self.theme_widgets.append(frame)

        self.add_label(frame, "日志文件路径").grid(row=0, column=0, padx=(0, 6), sticky="w")
        path_entry = self.add_entry(frame, self.path_var)
        path_entry.grid(row=0, column=1, padx=(0, 6), sticky="ew")
        self.add_button(frame, "选择文件", self.choose_file).grid(row=0, column=2, padx=(0, 10))

        self.add_label(frame, "日志展示条数").grid(row=0, column=3, padx=(0, 6), sticky="w")
        max_entry = self.add_entry(frame, self.max_log_var, width=8)
        max_entry.configure(validate="key", validatecommand=(self.register(self.validate_digits), "%P"))
        max_entry.grid(row=0, column=4, padx=(0, 10))
        self.start_button = self.add_button(frame, "开始监听", self.start_listening)
        self.start_button.grid(row=0, column=5, padx=(0, 6))
        self.stop_button = self.add_button(frame, "停止监听", self.stop_listening)
        self.stop_button.grid(row=0, column=6)

    def build_output_windows(self) -> None:
        label_frame = tk.Frame(self, padx=8, pady=4)
        label_frame.grid(row=1, column=0, sticky="ew")
        self.theme_widgets.append(label_frame)
        self.add_label(label_frame, "SQL输出窗口").pack(anchor="w")
        self.sql_text = self.create_scrolled_text(row=2)
        self.create_output_menu()
        self.configure_text_tags()
        self.build_status_bar()

    def build_status_bar(self) -> None:
        status_frame = tk.Frame(self, padx=8, pady=5)
        status_frame.grid(row=3, column=0, sticky="ew")
        status_frame.columnconfigure(0, weight=1)
        self.theme_widgets.append(status_frame)

        self.status_label = self.add_label(status_frame, "")
        self.status_label.configure(textvariable=self.status_var)
        self.status_label.grid(row=0, column=0, sticky="w")

        right_frame = tk.Frame(status_frame)
        right_frame.grid(row=0, column=1, sticky="e")
        self.theme_widgets.append(right_frame)

        self.theme_link = self.add_link(right_frame, THEME_TOGGLE_TEXT, self.toggle_theme)
        self.theme_link.pack(side="left")
        self.version_label = self.add_label(right_frame, f" | 当前版本：{APP_VERSION} | ")
        self.version_label.pack(side="left")
        self.latest_link = self.add_link(right_frame, LATEST_VERSION_TEXT, self.open_latest_version)
        self.latest_link.pack(side="left")
        self.brand_label = self.add_label(right_frame, f" | {APP_BRAND}")
        self.brand_label.pack(side="left")

    def create_scrolled_text(self, row: int) -> tk.Text:
        frame = tk.Frame(self, padx=8, pady=0)
        frame.grid(row=row, column=0, sticky="nsew")
        frame.rowconfigure(0, weight=1)
        frame.columnconfigure(0, weight=1)
        self.theme_widgets.append(frame)

        text = tk.Text(
            frame,
            wrap="word",
            undo=False,
            font=self.console_font(),
            relief="solid",
            borderwidth=1,
            highlightthickness=0,
            padx=8,
            pady=6,
        )
        scrollbar = ThemedScrollbar(frame, command=text.yview)
        self.sql_scrollbar = scrollbar
        text.configure(yscrollcommand=self.on_sql_scroll)
        text.grid(row=0, column=0, sticky="nsew")
        scrollbar.grid(row=0, column=1, sticky="ns")
        self.theme_widgets.extend([text, scrollbar])
        for sequence in ("<MouseWheel>", "<Button-4>", "<Button-5>", "<KeyRelease>", "<ButtonRelease-1>"):
            text.bind(sequence, lambda _event: self.after_idle(self.refresh_auto_scroll), add=True)
        return text

    def console_font(self) -> tuple[str, int]:
        system = platform.system()
        if system == "Windows":
            return ("JetBrains Mono", 10)
        if system == "Darwin":
            return ("JetBrains Mono", 11)
        return ("JetBrains Mono", 10)

    def add_label(self, parent: tk.Widget, text: str) -> tk.Label:
        label = tk.Label(parent, text=text)
        self.theme_widgets.append(label)
        return label

    def add_entry(self, parent: tk.Widget, variable: tk.StringVar, width: int | None = None) -> tk.Entry:
        entry = tk.Entry(parent, textvariable=variable, width=width, relief="solid", borderwidth=1, highlightthickness=0)
        self.theme_widgets.append(entry)
        self.entry_widgets.append(entry)
        return entry

    def add_button(self, parent: tk.Widget, text: str, command) -> tk.Label:
        button = tk.Label(
            parent,
            text=text,
            cursor="hand2",
            padx=16,
            pady=3,
            relief="solid",
            borderwidth=1,
            anchor="center",
        )
        self.button_commands[button] = command
        button.enabled = True  # type: ignore[attr-defined]
        button.bind("<Button-1>", self.invoke_button)
        button.bind("<Return>", self.invoke_button)
        button.bind("<Enter>", lambda event: self.set_button_hover(event.widget, True))
        button.bind("<Leave>", lambda event: self.set_button_hover(event.widget, False))
        self.theme_widgets.append(button)
        self.button_widgets.append(button)
        return button

    def invoke_button(self, event: tk.Event) -> None:
        button = event.widget
        if not getattr(button, "enabled", True):
            return
        command = self.button_commands.get(button)
        if command:
            command()

    def set_button_enabled(self, button: tk.Label, enabled: bool) -> None:
        button.enabled = enabled  # type: ignore[attr-defined]
        button.configure(cursor="hand2" if enabled else "arrow")
        self.apply_button_style(button)

    def set_button_hover(self, button: tk.Widget, hovering: bool) -> None:
        if not getattr(button, "enabled", True):
            return
        theme = THEMES.get(self.theme_name, THEMES["light"])
        color = theme["button_hover_bg"] if hovering else theme["button_bg"]
        self.safe_configure(button, bg=color)

    def apply_button_style(self, button: tk.Widget) -> None:
        theme = THEMES.get(self.theme_name, THEMES["light"])
        enabled = getattr(button, "enabled", True)
        self.safe_configure(
            button,
            bg=theme["button_bg"] if enabled else theme["button_disabled_bg"],
            fg=theme["button_fg"] if enabled else theme["button_disabled_fg"],
            highlightbackground=theme["border"],
            borderwidth=1,
            relief="solid",
        )

    def update_button_states(self) -> None:
        self.set_button_enabled(self.start_button, not self.listening)
        self.set_button_enabled(self.stop_button, self.listening)

    def add_link(self, parent: tk.Widget, text: str, command) -> tk.Label:
        link = tk.Label(parent, text=text, cursor="hand2")
        link.bind("<Button-1>", lambda _event: command())
        self.theme_widgets.append(link)
        return link

    def create_output_menu(self) -> None:
        self.output_menu = tk.Menu(self.sql_text, tearoff=0, borderwidth=0, activeborderwidth=0, relief="flat")
        self.output_menu.add_command(label="搜索", command=lambda: self.open_search(None))
        self.output_menu.add_command(label="清空", command=self.clear_sql_output)
        self.sql_text.bind("<Button-3>", self.show_output_menu)
        self.sql_text.bind("<Button-2>", self.show_output_menu)
        self.sql_text.bind("<Control-Button-1>", self.show_output_menu)

    def show_output_menu(self, event: tk.Event) -> None:
        self.output_menu.tk_popup(event.x_root, event.y_root)

    def clear_sql_output(self) -> None:
        self.sql_text.delete("1.0", "end")
        self.log_count = 0
        self.clear_search_highlight()

    def choose_file(self) -> None:
        path = filedialog.askopenfilename(title="选择 IDEA SQL 日志文件")
        if path:
            self.path_var.set(path)

    def start_listening(self) -> None:
        if self.reader and self.reader.is_alive():
            return
        path = self.path_var.get().strip()
        if not path:
            messagebox.showwarning("缺少日志文件", "请选择日志文件路径。")
            return
        if not os.path.isfile(path):
            messagebox.showwarning("日志文件不存在", "日志文件路径不存在或不是文件。")
            return

        self.stop_event = threading.Event()
        self.line_queue = queue.Queue(maxsize=LINE_QUEUE_SIZE)
        self.event_queue = queue.Queue(maxsize=EVENT_QUEUE_SIZE)
        self.reader = TailReader(path, self.line_queue, self.stop_event)
        self.analyzer = Analyzer(self.line_queue, self.event_queue, self.stop_event)
        self.analyzer.start()
        self.reader.start()
        self.listening = True
        self.update_button_states()
        self.start_status_animation()

    def stop_listening(self) -> None:
        if self.stop_event:
            self.stop_event.set()
        self.listening = False
        self.update_button_states()
        self.stop_status_animation("停止监听")

    def start_status_animation(self) -> None:
        if self.status_animation_job:
            self.after_cancel(self.status_animation_job)
        self.status_frame_index = 0
        self.animate_status()

    def animate_status(self) -> None:
        if not self.listening:
            self.status_animation_job = None
            return
        frame = STATUS_FRAMES[self.status_frame_index % len(STATUS_FRAMES)]
        self.status_var.set(f"监听中 {frame}")
        self.status_frame_index += 1
        self.status_animation_job = self.after(STATUS_ANIMATION_MS, self.animate_status)

    def stop_status_animation(self, status: str) -> None:
        if self.status_animation_job:
            self.after_cancel(self.status_animation_job)
            self.status_animation_job = None
        self.status_var.set(status)

    def poll_events(self) -> None:
        if self.event_queue:
            while True:
                try:
                    event_type, text = self.event_queue.get_nowait()
                except queue.Empty:
                    break
                if event_type == "sql":
                    self.append_sql(text)
                self.event_queue.task_done()
        self.after(POLL_MS, self.poll_events)

    def append_sql(self, sql: str) -> None:
        should_follow = self.auto_scroll or self.is_sql_scrolled_to_bottom()
        self.log_count += 1
        self.insert_sql_record(sql)
        self.trim_sql_output()
        if self.search_var.get():
            self.highlight_search(focus_first=False)
        if should_follow:
            self.sql_text.see("end")
            self.auto_scroll = True

    def insert_sql_record(self, text: str) -> None:
        match = TIMESTAMP_RE.match(text)
        if not match:
            self.insert_sql_body(text)
            self.sql_text.insert("end", "\n\n")
            return

        timestamp = match.group("timestamp")
        body = match.group("body")
        self.sql_text.insert("end", timestamp, ("timestamp",))
        self.sql_text.insert("end", " ")
        self.insert_sql_body(body)
        self.sql_text.insert("end", "\n\n")

    def insert_sql_body(self, sql: str) -> None:
        for segment, tag in self.tokenize_sql(sql):
            if tag:
                self.sql_text.insert("end", segment, (tag,))
            else:
                self.sql_text.insert("end", segment)

    def tokenize_sql(self, sql: str):
        index = 0
        length = len(sql)
        while index < length:
            char = sql[index]
            nxt = sql[index + 1] if index + 1 < length else ""

            if char.isspace():
                start = index
                while index < length and sql[index].isspace():
                    index += 1
                yield sql[start:index], ""
                continue

            if char == "-" and nxt == "-":
                yield sql[index:], "sql_comment"
                break

            if char == "/" and nxt == "*":
                end = sql.find("*/", index + 2)
                if end == -1:
                    yield sql[index:], "sql_comment"
                    break
                yield sql[index:end + 2], "sql_comment"
                index = end + 2
                continue

            if char in {"'", '"'}:
                start = index
                quote = char
                index += 1
                while index < length:
                    current = sql[index]
                    next_char = sql[index + 1] if index + 1 < length else ""
                    if current == quote:
                        if quote == "'" and next_char == "'":
                            index += 2
                            continue
                        index += 1
                        break
                    if current == "\\" and index + 1 < length:
                        index += 2
                        continue
                    index += 1
                yield sql[start:index], "sql_string"
                continue

            if char == "`":
                start = index
                index += 1
                while index < length:
                    if sql[index] == "`":
                        index += 1
                        break
                    index += 1
                yield sql[start:index], "sql_string"
                continue

            if char.isdigit() or (char == "." and nxt.isdigit()):
                start = index
                index += 1
                while index < length and (sql[index].isalnum() or sql[index] in "."):
                    index += 1
                yield sql[start:index], "sql_number"
                continue

            if char.isalpha() or char == "_":
                start = index
                index += 1
                while index < length and (sql[index].isalnum() or sql[index] in "_$"):
                    index += 1
                word = sql[start:index]
                upper_word = word.upper()
                tag = ""
                if upper_word in SQL_KEYWORDS:
                    tag = "sql_keyword"
                elif upper_word in SQL_TYPES:
                    tag = "sql_type"
                elif upper_word in SQL_FUNCTIONS:
                    tag = "sql_function"
                yield word, tag
                continue

            if char in "=<>!+-*/%,.;()[]{}":
                yield char, "sql_operator"
                index += 1
                continue

            yield char, ""
            index += 1

    def trim_sql_output(self) -> None:
        limit = self.get_max_log_count()
        while self.log_count > limit:
            next_record = self.sql_text.search(r"\n\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}\s", "2.0", stopindex="end", regexp=True)
            if not next_record:
                self.sql_text.delete("1.0", "end")
                self.log_count = 0
                return
            self.sql_text.delete("1.0", f"{next_record}+1c")
            self.log_count -= 1

    def get_max_log_count(self) -> int:
        try:
            return max(1, int(self.max_log_var.get()))
        except ValueError:
            self.max_log_var.set(DEFAULT_MAX_LOG_COUNT)
            return int(DEFAULT_MAX_LOG_COUNT)

    def on_sql_scroll(self, first: str, last: str) -> None:
        self.sql_scrollbar.set(first, last)
        self.auto_scroll = float(last) >= SCROLL_BOTTOM_THRESHOLD

    def refresh_auto_scroll(self) -> None:
        self.auto_scroll = self.is_sql_scrolled_to_bottom()

    def is_sql_scrolled_to_bottom(self) -> bool:
        try:
            return self.sql_text.yview()[1] >= SCROLL_BOTTOM_THRESHOLD
        except tk.TclError:
            return True

    def configure_text_tags(self) -> None:
        theme = THEMES.get(self.theme_name, THEMES["light"])
        self.sql_text.tag_configure("timestamp", foreground=theme["timestamp_fg"])
        self.sql_text.tag_configure("sql_keyword", foreground=theme["sql_keyword_fg"])
        self.sql_text.tag_configure("sql_type", foreground=theme["sql_type_fg"])
        self.sql_text.tag_configure("sql_function", foreground=theme["sql_function_fg"])
        self.sql_text.tag_configure("sql_string", foreground=theme["sql_string_fg"])
        self.sql_text.tag_configure("sql_number", foreground=theme["sql_number_fg"])
        self.sql_text.tag_configure("sql_comment", foreground=theme["sql_comment_fg"])
        self.sql_text.tag_configure("sql_operator", foreground=theme["sql_operator_fg"])
        self.sql_text.tag_configure(
            "search_match",
            background=theme["search_bg"],
            foreground=theme["search_fg"],
        )
        self.sql_text.tag_configure(
            "search_current",
            background=theme["search_current_bg"],
            foreground=theme["search_current_fg"],
        )
        self.sql_text.tag_raise("search_match")
        self.sql_text.tag_raise("search_current")

    def open_search(self, event: tk.Event | None) -> str:
        if self.search_window and self.search_window.winfo_exists():
            self.search_window.deiconify()
            self.search_window.lift()
            if self.search_entry:
                self.search_entry.focus_set()
                self.search_entry.select_range(0, "end")
            return "break"

        self.search_window = tk.Toplevel(self)
        self.search_window.title("搜索日志")
        self.search_window.resizable(False, False)
        self.search_window.transient(self)
        self.search_window.protocol("WM_DELETE_WINDOW", self.close_search)
        self.search_window.bind("<Escape>", lambda _event: self.close_search())
        self.search_window.bind("<Return>", lambda _event: self.focus_next_match())

        frame = tk.Frame(self.search_window, padx=10, pady=10)
        frame.grid(row=0, column=0, sticky="nsew")
        self.theme_widgets.append(frame)
        label = self.add_label(frame, "关键词")
        label.grid(row=0, column=0, padx=(0, 6), sticky="w")
        self.search_entry = self.add_entry(frame, self.search_var, width=34)
        self.search_entry.grid(row=0, column=1, sticky="ew")
        close_button = self.add_button(frame, "关闭", self.close_search)
        close_button.grid(row=0, column=2, padx=(8, 0))
        self.search_var.trace_add("write", lambda *_args: self.highlight_search())
        self.apply_theme()
        self.search_entry.focus_set()
        self.search_entry.select_range(0, "end")
        self.highlight_search()
        return "break"

    def close_search(self) -> None:
        self.clear_search_highlight()
        self.search_var.set("")
        if self.search_window and self.search_window.winfo_exists():
            self.search_window.destroy()
        self.search_window = None
        self.search_entry = None

    def clear_search_highlight(self) -> None:
        self.sql_text.tag_remove("search_match", "1.0", "end")
        self.sql_text.tag_remove("search_current", "1.0", "end")

    def highlight_search(self, focus_first: bool = True) -> None:
        keyword = self.search_var.get()
        self.clear_search_highlight()
        if not keyword:
            return

        start = "1.0"
        first_match = ""
        while True:
            start = self.sql_text.search(keyword, start, stopindex="end", nocase=True)
            if not start:
                break
            end = f"{start}+{len(keyword)}c"
            self.sql_text.tag_add("search_match", start, end)
            if not first_match:
                first_match = start
            start = end

        if first_match:
            current_end = f"{first_match}+{len(keyword)}c"
            self.sql_text.tag_add("search_current", first_match, current_end)
            if focus_first:
                self.sql_text.see(first_match)
                self.auto_scroll = self.is_sql_scrolled_to_bottom()

    def focus_next_match(self) -> None:
        keyword = self.search_var.get()
        if not keyword:
            return
        current_ranges = self.sql_text.tag_ranges("search_current")
        start = current_ranges[1] if current_ranges else "insert"
        next_match = self.sql_text.search(keyword, start, stopindex="end", nocase=True)
        if not next_match:
            next_match = self.sql_text.search(keyword, "1.0", stopindex="end", nocase=True)
        if not next_match:
            return
        self.sql_text.tag_remove("search_current", "1.0", "end")
        self.sql_text.tag_add("search_current", next_match, f"{next_match}+{len(keyword)}c")
        self.sql_text.see(next_match)
        self.auto_scroll = self.is_sql_scrolled_to_bottom()

    def validate_digits(self, value: str) -> bool:
        return value == "" or value.isdigit()

    def toggle_theme(self) -> None:
        self.theme_name = "dark" if self.theme_name == "light" else "light"
        self.apply_theme()
        self.save_config()

    def open_latest_version(self) -> None:
        webbrowser.open(LATEST_VERSION_URL)

    def apply_theme(self) -> None:
        theme = THEMES.get(self.theme_name, THEMES["light"])
        self.configure(bg=theme["bg"])
        self.apply_window_appearance()

        for widget in self.theme_widgets:
            if isinstance(widget, tk.Text):
                self.safe_configure(
                    widget,
                    bg=theme["text_bg"],
                    fg=theme["text_fg"],
                    insertbackground=theme["text_fg"],
                    highlightbackground=theme["border"],
                    highlightcolor=theme["border"],
                    selectbackground=theme.get("select_bg", "#cfe8ff"),
                    selectforeground=theme.get("select_fg", theme["text_fg"]),
                    borderwidth=1,
                    relief="solid",
                )
            elif isinstance(widget, ThemedScrollbar):
                widget.set_theme(
                    theme["panel"],
                    theme.get("surface", theme["button_bg"]),
                    theme["button_hover_bg"],
                )
            elif widget in self.entry_widgets:
                self.safe_configure(
                    widget,
                    bg=theme["entry_bg"],
                    fg=theme["entry_fg"],
                    insertbackground=theme["entry_fg"],
                    highlightbackground=theme["border"],
                    highlightcolor=theme.get("active_border", theme["border"]),
                    selectbackground=theme.get("select_bg", "#cfe8ff"),
                    selectforeground=theme.get("select_fg", theme["entry_fg"]),
                    borderwidth=1,
                    relief="solid",
                    highlightthickness=1,
                )
            elif widget in self.button_widgets:
                self.apply_button_style(widget)
            elif isinstance(widget, tk.Frame):
                self.safe_configure(widget, bg=theme["panel"])
            else:
                self.safe_configure(widget, bg=theme["panel"], fg=theme["fg"])

        self.configure_text_tags()
        self.theme_link.configure(fg=theme["link"])
        self.latest_link.configure(fg=theme["link"])
        self.safe_configure(
            self.output_menu,
            bg=theme["panel"],
            fg=theme["fg"],
            activebackground=theme.get("menu_active_bg", theme["button_bg"]),
            activeforeground=theme.get("menu_active_fg", theme["fg"]),
            disabledforeground=theme.get("button_disabled_fg", theme["muted_fg"]),
            borderwidth=0,
            activeborderwidth=0,
            relief="flat",
        )
        if self.search_window and self.search_window.winfo_exists():
            self.search_window.configure(bg=theme["panel"])
            self.apply_window_appearance(self.search_window)

    def apply_window_appearance(self, window: tk.Tk | tk.Toplevel | None = None) -> None:
        target = window or self
        if platform.system() == "Windows":
            self.apply_windows_titlebar_theme(target)
            return

        appearances = ("darkAqua", "dark") if self.theme_name == "dark" else ("aqua", "light")
        for appearance in appearances:
            try:
                target.tk.call("::tk::unsupported::MacWindowStyle", "appearance", target._w, appearance)
                return
            except tk.TclError:
                continue

    def apply_windows_titlebar_theme(self, window: tk.Tk | tk.Toplevel) -> None:
        try:
            window.update_idletasks()
            hwnd = ctypes.windll.user32.GetParent(window.winfo_id())
            value = ctypes.c_int(1 if self.theme_name == "dark" else 0)
            for attribute in (20, 19):
                result = ctypes.windll.dwmapi.DwmSetWindowAttribute(
                    hwnd,
                    attribute,
                    ctypes.byref(value),
                    ctypes.sizeof(value),
                )
                if result == 0:
                    break
        except Exception:
            return

    def safe_configure(self, widget: tk.Widget, **options: str | int) -> None:
        for key, value in options.items():
            try:
                widget.configure(**{key: value})
            except tk.TclError:
                pass

    def load_config(self) -> dict[str, object]:
        try:
            with CONFIG_FILE.open("r", encoding="utf-8") as file:
                data = json.load(file)
                if isinstance(data, dict):
                    return data
        except (OSError, json.JSONDecodeError):
            pass
        return {}

    def restore_config(self) -> None:
        self.path_var.set(str(self.config_data.get("log_path", "")))
        saved_count = self.config_data.get("max_log_count", self.config_data.get("max_sql_count", DEFAULT_MAX_LOG_COUNT))
        self.max_log_var.set(str(saved_count or DEFAULT_MAX_LOG_COUNT))
        if "geometry" in self.config_data:
            self.geometry(self.last_normal_geometry)
        else:
            self.center_window(DEFAULT_GEOMETRY)
        if bool(self.config_data.get("maximized", False)):
            self.after(100, self.maximize_window)

    def center_window(self, size: str) -> None:
        width_text, height_text = size.split("x", maxsplit=1)
        width = int(width_text)
        height = int(height_text)
        x = max(0, (self.winfo_screenwidth() - width) // 2)
        y = max(0, (self.winfo_screenheight() - height) // 2)
        self.geometry(f"{width}x{height}+{x}+{y}")

    def maximize_window(self) -> None:
        try:
            self.state("zoomed")
        except tk.TclError:
            try:
                self.attributes("-zoomed", True)
            except tk.TclError:
                pass

    def save_config(self) -> None:
        maximized = self.is_maximized()
        data = {
            "log_path": self.path_var.get().strip(),
            "max_log_count": self.max_log_var.get().strip() or DEFAULT_MAX_LOG_COUNT,
            "theme": self.theme_name,
            "maximized": maximized,
            "geometry": self.last_normal_geometry if maximized else self.geometry(),
        }
        try:
            with CONFIG_FILE.open("w", encoding="utf-8") as file:
                json.dump(data, file, ensure_ascii=False, indent=2)
        except OSError:
            pass
        self.config_data = data

    def is_maximized(self) -> bool:
        try:
            if self.state() == "zoomed":
                return True
        except tk.TclError:
            pass
        try:
            return bool(self.attributes("-zoomed"))
        except tk.TclError:
            return False

    def remember_normal_geometry(self, event: tk.Event) -> None:
        if event.widget is self and not self.is_maximized():
            geometry = self.geometry()
            if geometry:
                self.last_normal_geometry = geometry

    def on_close(self) -> None:
        self.save_config()
        self.stop_listening()
        self.destroy()


def main() -> None:
    app = SQLMonitorApp()
    app.mainloop()


if __name__ == "__main__":
    main()