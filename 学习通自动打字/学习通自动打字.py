# -*- coding: utf-8 -*-
"""
学习通自动打字 v1.0

针对"网页代码编辑器回车自动缩进，导致逐字输入时每行不断向右跑偏"的问题。
提供三种输入方式：
  * 粘贴模式（推荐）：整段放进剪贴板后按 Ctrl+V，编辑器原样插入，不会重排缩进。
  * 逐行键入：每行回车后先按两次 Home 回到第 0 列，清掉编辑器自动补的缩进，
              再输入本行自己的前导空格 + 正文。
  * 逐字键入：原样逐字敲（记事本、Word 等没有自动缩进的地方用）。

纯标准库实现（tkinter + ctypes 调用 Win32），不需要 pip 安装任何东西。
"""

import ctypes
import ctypes.wintypes as wt
import json
import os
import queue
import sys
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

APP_NAME = "学习通自动打字"
VERSION = "1.2"
BASE_DIR = os.path.join(os.environ.get("APPDATA") or os.path.expanduser("~"), APP_NAME)
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")

# ----------------------------------------------------------------------------
# Win32 基础封装
# ----------------------------------------------------------------------------

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

INPUT_KEYBOARD = 1
KEYEVENTF_EXTENDEDKEY = 0x0001
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_UNICODE = 0x0004
KEYEVENTF_SCANCODE = 0x0008

CF_UNICODETEXT = 13
GMEM_MOVEABLE = 0x0002

WM_HOTKEY = 0x0312
WM_QUIT = 0x0012
MOD_NOREPEAT = 0x4000

ULONG_PTR = ctypes.c_size_t


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", wt.LONG),
        ("dy", wt.LONG),
        ("mouseData", wt.DWORD),
        ("dwFlags", wt.DWORD),
        ("time", wt.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", wt.WORD),
        ("wScan", wt.WORD),
        ("dwFlags", wt.DWORD),
        ("time", wt.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [("uMsg", wt.DWORD), ("wParamL", wt.WORD), ("wParamH", wt.WORD)]


class _INPUTUNION(ctypes.Union):
    _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT), ("hi", HARDWAREINPUT)]


class INPUT(ctypes.Structure):
    _fields_ = [("type", wt.DWORD), ("u", _INPUTUNION)]


user32.SendInput.argtypes = (wt.UINT, ctypes.POINTER(INPUT), ctypes.c_int)
user32.SendInput.restype = wt.UINT
user32.MapVirtualKeyW.argtypes = (wt.UINT, wt.UINT)
user32.MapVirtualKeyW.restype = wt.UINT
user32.GetForegroundWindow.restype = wt.HWND
user32.GetWindowThreadProcessId.argtypes = (wt.HWND, ctypes.POINTER(wt.DWORD))
user32.OpenClipboard.argtypes = (wt.HWND,)
user32.OpenClipboard.restype = wt.BOOL
user32.SetClipboardData.argtypes = (wt.UINT, wt.HANDLE)
user32.SetClipboardData.restype = wt.HANDLE
user32.GetClipboardData.argtypes = (wt.UINT,)
user32.GetClipboardData.restype = wt.HANDLE
kernel32.GlobalAlloc.argtypes = (wt.UINT, ctypes.c_size_t)
kernel32.GlobalAlloc.restype = ctypes.c_void_p
kernel32.GlobalLock.argtypes = (ctypes.c_void_p,)
kernel32.GlobalLock.restype = ctypes.c_void_p
kernel32.GlobalUnlock.argtypes = (ctypes.c_void_p,)
kernel32.GetCurrentThreadId.restype = wt.DWORD

VK = {
    "RETURN": 0x0D,
    "HOME": 0x24,
    "END": 0x23,
    "DELETE": 0x2E,
    "TAB": 0x09,
    "CONTROL": 0x11,
    "SHIFT": 0x10,
    "ESC": 0x1B,
    "V": 0x56,
    "A": 0x41,
    "F8": 0x77,
    "F9": 0x78,
}
EXTENDED_KEYS = {0x24, 0x23, 0x2E, 0x25, 0x26, 0x27, 0x28, 0x2D, 0x21, 0x22}


class TypingBlocked(RuntimeError):
    """系统拒绝模拟按键（多半是目标程序以管理员权限运行）。"""


_send_failures = 0


def _send_input(inp):
    global _send_failures
    sent = user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT))
    if sent != 1:
        _send_failures += 1
        if _send_failures > 3:
            raise TypingBlocked(
                "系统拒绝了模拟按键。若学习通/浏览器以管理员身份运行，"
                "请右键本程序选择“以管理员身份运行”。"
            )


def key_down(vk):
    inp = INPUT(type=INPUT_KEYBOARD)
    inp.u.ki.wVk = vk
    inp.u.ki.wScan = user32.MapVirtualKeyW(vk, 0)
    if vk in EXTENDED_KEYS:
        inp.u.ki.dwFlags = KEYEVENTF_EXTENDEDKEY
    _send_input(inp)


def key_up(vk):
    inp = INPUT(type=INPUT_KEYBOARD)
    inp.u.ki.wVk = vk
    inp.u.ki.wScan = user32.MapVirtualKeyW(vk, 0)
    inp.u.ki.dwFlags = KEYEVENTF_KEYUP
    if vk in EXTENDED_KEYS:
        inp.u.ki.dwFlags |= KEYEVENTF_EXTENDEDKEY
    _send_input(inp)


def press(vk, hold=0.008):
    key_down(vk)
    if hold:
        time.sleep(hold)
    key_up(vk)


def press_home_twice():
    # CodeMirror 的 Home 是"智能行首"：第一次到首个非空字符，第二次才到第 0 列。
    # 其他编辑器多按一次无副作用。
    press(VK["HOME"])
    press(VK["HOME"])


def send_char(ch):
    data = ch.encode("utf-16-le")
    for i in range(0, len(data), 2):
        code = int.from_bytes(data[i:i + 2], "little")
        for flags in (KEYEVENTF_UNICODE, KEYEVENTF_UNICODE | KEYEVENTF_KEYUP):
            inp = INPUT(type=INPUT_KEYBOARD)
            inp.u.ki.wVk = 0
            inp.u.ki.wScan = code
            inp.u.ki.dwFlags = flags
            _send_input(inp)


def send_ctrl(vk):
    key_down(VK["CONTROL"])
    time.sleep(0.01)
    press(vk)
    time.sleep(0.01)
    key_up(VK["CONTROL"])


def send_ctrl_v():
    send_ctrl(VK["V"])


def foreground_pid():
    hwnd = user32.GetForegroundWindow()
    pid = wt.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return pid.value


# ----------------------------------------------------------------------------
# 剪贴板
# ----------------------------------------------------------------------------

def get_clipboard_text():
    """读不到（比如剪贴板里是图片）时返回 None。"""
    if not user32.IsClipboardFormatAvailable(CF_UNICODETEXT):
        return None
    for _ in range(10):
        if user32.OpenClipboard(None):
            break
        time.sleep(0.05)
    else:
        return None
    try:
        handle = user32.GetClipboardData(CF_UNICODETEXT)
        if not handle:
            return None
        ptr = kernel32.GlobalLock(handle)
        if not ptr:
            return None
        try:
            return ctypes.wstring_at(ptr)
        finally:
            kernel32.GlobalUnlock(handle)
    finally:
        user32.CloseClipboard()


def set_clipboard_text(text):
    for _ in range(10):
        if user32.OpenClipboard(None):
            break
        time.sleep(0.05)
    else:
        raise RuntimeError("剪贴板被其他程序占用，请稍后重试。")
    try:
        user32.EmptyClipboard()
        data = text.encode("utf-16-le") + b"\x00\x00"
        handle = kernel32.GlobalAlloc(GMEM_MOVEABLE, len(data))
        if not handle:
            raise RuntimeError("申请剪贴板内存失败。")
        ptr = kernel32.GlobalLock(handle)
        if not ptr:
            raise RuntimeError("锁定剪贴板内存失败。")
        ctypes.memmove(ptr, data, len(data))
        kernel32.GlobalUnlock(handle)
        if not user32.SetClipboardData(CF_UNICODETEXT, handle):
            raise RuntimeError("写入剪贴板失败。")
        # 成功后所有权归系统，不能再 GlobalFree
    finally:
        user32.CloseClipboard()


# ----------------------------------------------------------------------------
# 全局热键（独立线程 + 自己的消息循环，避免被 Tk 的消息泵吞掉）
# ----------------------------------------------------------------------------

class HotkeyThread(threading.Thread):
    def __init__(self, bindings, out_queue):
        super().__init__(daemon=True)
        self.bindings = bindings  # [(hotkey_id, vk)]
        self.out = out_queue
        self.tid = None
        self.registered = []
        self.failed = []
        self.ready = threading.Event()

    def run(self):
        self.tid = kernel32.GetCurrentThreadId()
        for hid, vk in self.bindings:
            if user32.RegisterHotKey(None, hid, MOD_NOREPEAT, vk):
                self.registered.append((hid, vk))
            elif user32.RegisterHotKey(None, hid, 0, vk):
                self.registered.append((hid, vk))
            else:
                self.failed.append((hid, vk, ctypes.get_last_error()))
        self.ready.set()

        msg = wt.MSG()
        while True:
            ret = user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
            if ret in (0, -1):
                break
            if msg.message == WM_HOTKEY:
                self.out.put(("hotkey", int(msg.wParam)))

        for hid, _vk in self.registered:
            user32.UnregisterHotKey(None, hid)

    def stop(self):
        if self.tid:
            user32.PostThreadMessageW(self.tid, WM_QUIT, 0, 0)


# ----------------------------------------------------------------------------
# 打字任务
# ----------------------------------------------------------------------------

class TypingJob(threading.Thread):
    def __init__(self, text, opts, emit, stop_event):
        super().__init__(daemon=True)
        self.text = text
        self.opts = opts
        self.emit = emit
        self.stop_event = stop_event

    # -- 小工具 -------------------------------------------------------------
    def log(self, msg, level="info"):
        self.emit("log", msg, level)

    def sleep(self, seconds):
        if seconds > 0:
            time.sleep(seconds)

    def _foreground_is_self(self):
        try:
            return foreground_pid() == os.getpid()
        except Exception:
            return False

    def _type_chars(self, s):
        delay = self.opts["char_delay"] / 1000.0
        for i, ch in enumerate(s):
            if self.stop_event.is_set():
                return False
            if ch == "\n":
                press(VK["RETURN"])
            elif ch == "\r":
                continue
            elif ch == "\t":
                press(VK["TAB"])
            else:
                send_char(ch)
            if delay:
                self.sleep(delay)
            elif i % 32 == 31 and self._foreground_is_self():
                return False
        return True

    def _clean_line(self, raw, o):
        line = raw.expandtabs(o["tab_width"]) if o["expand_tabs"] else raw
        if o["strip_trailing"]:
            line = line.rstrip(" \t")
        return line

    # -- 四种模式 -----------------------------------------------------------
    def _mode_paste(self):
        text = self.text
        old = get_clipboard_text()
        self.log("正在写入剪贴板…")
        set_clipboard_text(text)
        self.sleep(0.25)
        self.log("发送 Ctrl+V…")
        send_ctrl_v()
        self.sleep(0.6)
        if self.opts["restore_clipboard"] and old is not None:
            try:
                set_clipboard_text(old)
            except Exception:
                pass
        self.emit("progress", 1.0)
        return "完成（粘贴模式）"

    def _mode_line_paste(self):
        """逐行粘贴：每行连同换行一起 Ctrl+V 进去。

        全程不按回车，因此编辑器的“回车自动缩进”根本不触发，
        也不存在“清缩进时误删上一行”的风险。需要看着它一行行进时最稳。
        """
        o = self.opts
        lines = self.text.split("\n")
        total = max(1, len(lines))
        old = get_clipboard_text()
        try:
            for idx, raw in enumerate(lines):
                if self.stop_event.is_set():
                    return "已停止"
                if self._foreground_is_self():
                    return "已停止（焦点回到了本程序）"
                chunk = self._clean_line(raw, o)
                if idx < len(lines) - 1:
                    chunk += "\n"
                set_clipboard_text(chunk)
                self.sleep(0.06)
                send_ctrl_v()
                self.sleep(max(0.08, o["line_delay"] / 1000.0))
                if idx % 5 == 0:
                    self.emit("progress", (idx + 1) / total)
        finally:
            if o["restore_clipboard"] and old is not None:
                try:
                    self.sleep(0.15)
                    set_clipboard_text(old)
                except Exception:
                    pass
        self.emit("progress", 1.0)
        return "完成（逐行粘贴）"

    def _mode_line(self):
        o = self.opts
        lines = self.text.split("\n")
        total = max(1, len(lines))
        for idx, raw in enumerate(lines):
            if self.stop_event.is_set():
                return "已停止"
            if idx > 0 and self._foreground_is_self():
                return "已停止（焦点回到了本程序）"

            line = self._clean_line(raw, o)

            if idx > 0:
                # 先按 Esc 关掉代码提示弹窗：弹窗开着时回车会被它吞掉，
                # 回车没生效，后面的内容就会挤到上一行里。
                press(VK["ESC"])
                self.sleep(0.05)
                press(VK["RETURN"])
                self.sleep(o["line_delay"] / 1000.0)

            # 回到第 0 列再输入本行缩进。编辑器刚才自动补的空白会留在行尾，
            # 不再去“选中并删除”——那一步在回车没生效的编辑器里会把刚打完的行删掉。
            press_home_twice()

            if line:
                stripped = line.lstrip(" \t")
                indent = line[: len(line) - len(stripped)]
                if not self._type_chars(indent + stripped):
                    return "已停止"

            if idx % 5 == 0:
                self.emit("progress", (idx + 1) / total)
            self.sleep(max(0.03, o["line_delay"] / 2000.0))
        return "完成（逐行键入）"

    def _mode_char(self):
        ok = self._type_chars(self.text)
        self.emit("progress", 1.0)
        return "完成（逐字模式）" if ok else "已停止"

    # -- 入口 ---------------------------------------------------------------
    def run(self):
        try:
            delay = self.opts["start_delay"]
            for left in range(int(delay), 0, -1):
                if self.stop_event.is_set():
                    self.emit("done", "已取消")
                    return
                self.log(f"{left} 秒后开始，请立刻点击学习通代码编辑框…", "warn")
                self.emit("countdown", left)
                for _ in range(10):
                    if self.stop_event.is_set():
                        break
                    time.sleep(0.1)

            if self.stop_event.is_set():
                self.emit("done", "已取消")
                return

            if self._foreground_is_self():
                self.emit("done", "中止：当前窗口是本程序，请先点一下学习通编辑框再开始。")
                return

            self.emit("countdown", 0)

            # 先关掉可能开着的补全提示，再（可选）清空编辑框，保证从干净状态开始
            press(VK["ESC"])
            self.sleep(0.05)
            if self.opts.get("clear_first", True):
                self.log("先全选并清空编辑框（Ctrl+A、Delete）…", "warn")
                send_ctrl(VK["A"])
                self.sleep(0.15)
                press(VK["DELETE"])
                self.sleep(0.2)

            self.log("开始输入…", "ok")
            mode = self.opts["mode"]
            if mode == "paste":
                reason = self._mode_paste()
            elif mode == "line_paste":
                reason = self._mode_line_paste()
            elif mode == "line":
                reason = self._mode_line()
            else:
                reason = self._mode_char()
            self.emit("done", reason)
        except TypingBlocked as exc:
            self.emit("done", f"失败：{exc}")
        except Exception as exc:  # 兜底，避免线程静默死掉
            self.emit("done", f"出错：{exc!r}")


# ----------------------------------------------------------------------------
# 界面
# ----------------------------------------------------------------------------

DEFAULT_OPTS = {
    "mode": "paste",
    "start_delay": 3,
    "char_delay": 2,
    "line_delay": 150,
    "tab_width": 4,
    "expand_tabs": True,
    "strip_trailing": True,
    "restore_clipboard": True,
    "clear_first": True,
    "strip_fences": False,
}


class App:
    def __init__(self, root):
        self.root = root
        self.queue = queue.Queue()
        self.stop_event = threading.Event()
        self.job = None
        self.hotkeys = None
        self.restoring = False

        self.opts = dict(DEFAULT_OPTS)
        self._load_config()

        self._build_ui()
        self._start_hotkeys()
        self.root.after(60, self._poll)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    # -- 配置 ---------------------------------------------------------------
    def _load_config(self):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            saved = data.get("opts", {})
            # v1.0 的配置里带 clear_autoindent：顺手把行间隔抬到更安全的 150ms
            if "clear_autoindent" in saved and "line_delay" in saved:
                try:
                    saved["line_delay"] = max(int(saved["line_delay"]), 150)
                except (TypeError, ValueError):
                    saved["line_delay"] = 150
            for k, v in saved.items():
                if k in self.opts:
                    self.opts[k] = v
            self.saved_text = data.get("text", "")
        except Exception:
            self.saved_text = ""

    def _save_config(self):
        try:
            os.makedirs(BASE_DIR, exist_ok=True)
            data = {"opts": self.opts, "text": self.text.get("1.0", "end-1c")}
            with open(CONFIG_PATH, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    # -- 界面 ---------------------------------------------------------------
    def _build_ui(self):
        r = self.root
        r.title(f"{APP_NAME} v{VERSION}")
        r.geometry("900x780")
        r.minsize(760, 620)

        style = ttk.Style()
        try:
            style.theme_use("vista")
        except tk.TclError:
            pass
        style.configure("Hint.TLabel", foreground="#666")
        style.configure("Ok.TLabel", foreground="#1a7f37")
        style.configure("Err.TLabel", foreground="#c62828")
        style.configure("Warn.TLabel", foreground="#b26a00")

        pad = {"padx": 10, "pady": 4}

        # 模式
        mode_frame = ttk.LabelFrame(r, text="输入方式")
        self.mode_var = tk.StringVar(value=self.opts["mode"])
        modes = [
            ("paste", "粘贴模式（推荐）"),
            ("line_paste", "逐行粘贴（最稳）"),
            ("line", "逐行键入"),
            ("char", "逐字键入"),
        ]
        for i, (val, label) in enumerate(modes):
            ttk.Radiobutton(
                mode_frame, text=label, value=val, variable=self.mode_var,
                command=self._on_mode_change,
            ).pack(side="left", padx=(10, 14), pady=6)

        # 文本区
        text_frame = ttk.LabelFrame(r, text="要输入的代码 / 文本")

        toolbar = ttk.Frame(text_frame)
        toolbar.pack(fill="x", padx=8, pady=(6, 0))
        ttk.Button(toolbar, text="导入文件…", command=self._import_file).pack(side="left")
        ttk.Button(toolbar, text="清空", command=self._clear_text).pack(side="left", padx=6)
        self.count_var = tk.StringVar(value="0 行 / 0 字符")
        ttk.Label(toolbar, textvariable=self.count_var, style="Hint.TLabel").pack(side="right")

        self.text = tk.Text(
            text_frame, wrap="none", undo=True, font=("Consolas", 11),
            relief="solid", borderwidth=1,
        )
        self.text.pack(side="left", fill="both", expand=True, padx=(8, 0), pady=8)
        sy = ttk.Scrollbar(text_frame, orient="vertical", command=self.text.yview)
        sy.pack(side="right", fill="y", pady=8, padx=(0, 8))
        self.text.configure(yscrollcommand=sy.set)
        if self.saved_text:
            self.text.insert("1.0", self.saved_text)
        self.text.bind("<<Modified>>", self._on_modified)

        # 参数
        opt = ttk.LabelFrame(r, text="参数")
        self.start_var = tk.IntVar(value=self.opts["start_delay"])
        self.char_var = tk.IntVar(value=self.opts["char_delay"])
        self.line_var = tk.IntVar(value=self.opts["line_delay"])
        self.tab_var = tk.IntVar(value=self.opts["tab_width"])
        self.expand_var = tk.BooleanVar(value=self.opts["expand_tabs"])
        self.strip_var = tk.BooleanVar(value=self.opts["strip_trailing"])
        self.restore_var = tk.BooleanVar(value=self.opts["restore_clipboard"])
        self.fence_var = tk.BooleanVar(value=self.opts["strip_fences"])
        self.clear_first_var = tk.BooleanVar(value=self.opts["clear_first"])

        ttk.Label(opt, text="开始延时(秒)").grid(row=0, column=0, sticky="w", padx=(10, 2), pady=2)
        ttk.Spinbox(opt, from_=0, to=15, width=4, textvariable=self.start_var).grid(
            row=0, column=1, sticky="w", padx=(0, 12), pady=2
        )
        ttk.Label(opt, text="每字间隔(ms)").grid(row=0, column=2, sticky="w", padx=(0, 2))
        ttk.Spinbox(opt, from_=0, to=200, width=4, textvariable=self.char_var).grid(
            row=0, column=3, sticky="w", padx=(0, 12), pady=2
        )
        ttk.Label(opt, text="每行间隔(ms)").grid(row=0, column=4, sticky="w", padx=(0, 2))
        ttk.Spinbox(opt, from_=0, to=2000, width=5, textvariable=self.line_var).grid(
            row=0, column=5, sticky="w", padx=(0, 12), pady=2
        )
        ttk.Label(opt, text="Tab 宽度").grid(row=0, column=6, sticky="w", padx=(0, 2))
        ttk.Spinbox(opt, from_=1, to=8, width=4, textvariable=self.tab_var).grid(
            row=0, column=7, sticky="w", pady=2
        )

        ttk.Checkbutton(opt, text="Tab 转空格", variable=self.expand_var).grid(
            row=1, column=0, columnspan=2, sticky="w", padx=(8, 0), pady=2
        )
        ttk.Checkbutton(opt, text="去掉行尾空白", variable=self.strip_var).grid(
            row=1, column=2, columnspan=2, sticky="w", pady=2
        )
        ttk.Checkbutton(opt, text="粘贴后还原剪贴板", variable=self.restore_var).grid(
            row=1, column=4, columnspan=3, sticky="w", pady=2
        )
        self.clear_first_chk = ttk.Checkbutton(
            opt, text="开始前清空编辑框", variable=self.clear_first_var
        )
        self.clear_first_chk.grid(row=1, column=7, columnspan=1, sticky="w", pady=2)
        ttk.Checkbutton(opt, text="去掉 Markdown 代码块标记 ```", variable=self.fence_var).grid(
            row=2, column=0, columnspan=4, sticky="w", padx=(8, 0), pady=(2, 6)
        )

        # 操作
        action = ttk.Frame(r)
        self.start_btn = ttk.Button(action, text="▶ 开始 (F8)", command=self.start)
        self.start_btn.pack(side="left")
        self.stop_btn = ttk.Button(action, text="■ 停止 (F9)", command=self.stop, state="disabled")
        self.stop_btn.pack(side="left", padx=8)
        ttk.Button(action, text="使用说明", command=self._show_help).pack(side="left")
        self.topmost_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(action, text="窗口置顶", variable=self.topmost_var,
                        command=self._toggle_topmost).pack(side="left", padx=10)

        self.progress = ttk.Progressbar(action, mode="determinate", maximum=100)
        self.progress.pack(side="right", fill="x", expand=True, padx=(16, 0))

        # 状态
        self.status_var = tk.StringVar(value="就绪。F8 开始 / F9 停止。")
        self.status = ttk.Label(r, textvariable=self.status_var, anchor="w")

        # 日志
        log_frame = ttk.LabelFrame(r, text="日志")
        self.log_text = tk.Text(log_frame, height=6, wrap="word", state="disabled",
                                font=("Microsoft YaHei UI", 9), relief="flat",
                                background="#fafafa")
        self.log_text.pack(fill="both", expand=True, padx=6, pady=6)
        self.log_text.tag_configure("err", foreground="#c62828")
        self.log_text.tag_configure("ok", foreground="#1a7f37")
        self.log_text.tag_configure("warn", foreground="#b26a00")
        self.log_text.tag_configure("info", foreground="#444444")

        # 布局：底部区域先占位，文本框最后撑满剩余空间（否则窗口变小时底部会被挤掉）
        log_frame.pack(side="bottom", fill="x", **pad)
        self.status.pack(side="bottom", fill="x", padx=12)
        action.pack(side="bottom", fill="x", **pad)
        opt.pack(side="bottom", fill="x", **pad)
        mode_frame.pack(side="top", fill="x", **pad)
        text_frame.pack(side="top", fill="both", expand=True, **pad)

        self._on_mode_change()
        self._update_count()
        self._log(f"{APP_NAME} v{VERSION} 已启动。F8 开始，F9 停止。", "ok")

    # -- 交互 ---------------------------------------------------------------
    def _on_mode_change(self):
        mode = self.mode_var.get()
        if hasattr(self, "status_var"):
            self.status_var.set(
                {
                    "paste": "粘贴模式：整段放进剪贴板后 Ctrl+V，最快，推荐优先使用。",
                    "line_paste": "逐行粘贴：每行单独 Ctrl+V，不按回车，任何编辑器都不会跑缩进。",
                    "line": "逐行键入：每行先按两次 Home 回行首再输入，缩进不会叠加。",
                    "char": "逐字模式：原样敲，适合记事本 / Word。",
                }[mode]
            )

    def _on_modified(self, _event=None):
        self.text.edit_modified(False)
        self._update_count()

    def _update_count(self):
        content = self.text.get("1.0", "end-1c")
        self.count_var.set(f"{content.count(chr(10)) + (1 if content else 0)} 行 / {len(content)} 字符")

    def _import_file(self):
        path = filedialog.askopenfilename(
            title="选择要输入的文本 / 代码文件",
            filetypes=[("文本文件", "*.txt *.py *.java *.c *.cpp *.js *.md *.json"),
                       ("所有文件", "*.*")],
        )
        if not path:
            return
        try:
            with open(path, "r", encoding="utf-8-sig") as f:
                data = f.read()
        except UnicodeDecodeError:
            with open(path, "r", encoding="gbk", errors="replace") as f:
                data = f.read()
        self.text.delete("1.0", "end")
        self.text.insert("1.0", data.replace("\r\n", "\n").replace("\r", "\n"))
        self._update_count()
        self._log(f"已导入：{path}")

    def _clear_text(self):
        self.text.delete("1.0", "end")
        self._update_count()

    def _toggle_topmost(self):
        try:
            self.root.attributes("-topmost", bool(self.topmost_var.get()))
        except Exception:
            pass

    def _log(self, msg, level="info"):
        stamp = time.strftime("%H:%M:%S")
        self.log_text.configure(state="normal")
        self.log_text.insert("end", f"[{stamp}] {msg}\n", level)
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    # -- 热键 ---------------------------------------------------------------
    def _start_hotkeys(self):
        self.hotkeys = HotkeyThread([(1, VK["F8"]), (2, VK["F9"])], self.queue)
        self.hotkeys.start()
        self.hotkeys.ready.wait(1.0)
        if self.hotkeys.failed:
            self._log("全局热键注册失败（可能被其他程序占用），请用界面按钮开始/停止。", "warn")

    # -- 开始 / 停止 --------------------------------------------------------
    def _collect_opts(self):
        try:
            self.opts.update(
                mode=self.mode_var.get(),
                start_delay=max(0, int(self.start_var.get())),
                char_delay=max(0, int(self.char_var.get())),
                line_delay=max(0, int(self.line_var.get())),
                tab_width=max(1, int(self.tab_var.get())),
                expand_tabs=bool(self.expand_var.get()),
                strip_trailing=bool(self.strip_var.get()),
                restore_clipboard=bool(self.restore_var.get()),
                clear_first=bool(self.clear_first_var.get()),
                strip_fences=bool(self.fence_var.get()),
            )
        except (tk.TclError, ValueError):
            raise ValueError("参数里有非法数字，请检查。")
        return dict(self.opts)

    def _prepare_text(self, text, strip_fences):
        text = text.replace("\r\n", "\n").replace("\r", "\n")
        if strip_fences:
            lines = [ln for ln in text.split("\n") if not ln.strip().startswith("```")]
            text = "\n".join(lines)
        if self.opts.get("expand_tabs", True):
            text = text.expandtabs(int(self.opts.get("tab_width", 4)))
        return text

    def start(self):
        if self.job and self.job.is_alive():
            return
        raw = self.text.get("1.0", "end-1c")
        if not raw.strip():
            self.status_var.set("请先粘贴或导入要输入的代码。")
            return
        try:
            opts = self._collect_opts()
        except ValueError as exc:
            messagebox.showwarning(APP_NAME, str(exc))
            return

        text = self._prepare_text(raw, opts["strip_fences"])
        self._save_config()
        self.stop_event.clear()
        global _send_failures
        _send_failures = 0
        self.progress["value"] = 0
        self.start_btn.configure(state="disabled")
        self.stop_btn.configure(state="normal")

        self.job = TypingJob(text, opts, self._emit, self.stop_event)
        self.job.start()

    def stop(self):
        if self.job and self.job.is_alive():
            self.stop_event.set()
            self.status_var.set("正在停止…")
            self._log("收到停止指令。", "warn")

    # -- 主线程事件循环 -----------------------------------------------------
    def _emit(self, kind, *args):
        self.queue.put((kind, args))

    def _poll(self):
        try:
            while True:
                item = self.queue.get_nowait()
                kind = item[0]
                args = item[1] if len(item) > 1 else ()
                if not isinstance(args, tuple):
                    args = (args,)
                if kind == "log":
                    self._log(args[0], args[1] if len(args) > 1 else "info")
                elif kind == "progress":
                    self.progress["value"] = max(0, min(100, args[0] * 100))
                elif kind == "countdown":
                    if args[0]:
                        self.status_var.set(f"{args[0]} 秒后开始，请点击学习通代码编辑框…")
                    else:
                        self.status_var.set("正在输入…（按 F9 停止）")
                elif kind == "done":
                    self._finish(args[0])
                elif kind == "hotkey":
                    if args[0] == 1:
                        self.start()
                    elif args[0] == 2:
                        self.stop()
        except queue.Empty:
            pass
        except Exception as exc:
            self._log(f"界面刷新异常：{exc!r}", "err")
        finally:
            self.root.after(60, self._poll)

    def _finish(self, reason):
        self.start_btn.configure(state="normal")
        self.stop_btn.configure(state="disabled")
        self.status_var.set(reason)
        level = "ok" if str(reason).startswith("完成") else "warn"
        if "失败" in str(reason) or "出错" in str(reason) or "中止" in str(reason):
            level = "err"
        self._log(reason, level)
        if not str(reason).startswith("完成"):
            self._restore_window()
        else:
            self.progress["value"] = 100

    def _restore_window(self):
        try:
            self.root.deiconify()
            self.root.lift()
        except Exception:
            pass

    # -- 其他 ---------------------------------------------------------------
    def _show_help(self):
        messagebox.showinfo(
            f"{APP_NAME} 使用说明",
            "1) 把代码粘到上面的框里（或点“导入文件”）。\n"
            "2) 选中输入方式（默认“粘贴模式”）。\n"
            "3) 点“开始 (F8)”，在倒计时的几秒内点一下学习通的代码编辑框。\n"
            "4) 按 F9 可随时停止。\n\n"
            "四种方式怎么选？\n"
            "· 粘贴模式：整段 Ctrl+V，最快最稳，优先用它。\n"
            "· 逐行粘贴：每行单独 Ctrl+V，完全不按回车，任何编辑器都不会跑缩进。\n"
            "· 逐行键入：真的逐字敲，每行先 Esc 关提示窗、再 Home×2 回行首。\n"
            "· 逐字键入：原样敲，给记事本 / Word 用。\n\n"
            "为什么学习通里会乱？\n"
            "1) 编辑器的代码提示弹窗开着时，回车会被它吞掉，换行没发生，\n"
            "   后面的内容就挤到上一行，越打越乱。现在每次回车前先按 Esc 关弹窗。\n"
            "2) 编辑框里如果还留着上次的残码，新内容会插在旧内容中间。\n"
            "   现在开始前会先 Ctrl+A、Delete 清空（可在参数里关掉）。\n\n"
            "为什么记事本正常、学习通会往右缩进？\n"
            "网页编辑器按回车会自动继承上一行缩进，逐字输入又把源码自带的\n"
            "前导空格再打一遍，于是每行多叠一层。\n\n"
            "小提示：\n"
            "· 学习通编辑器会自动补括号/引号，粘贴类方式能避免这个问题。\n"
            "· 逐行键入时行尾可能留下编辑器补的空格，不影响 Python。\n"
            "· 如果浏览器以管理员身份运行，本程序也需要以管理员身份运行。\n"
            "· 设置和文本会自动保存在 %APPDATA%\\学习通自动打字\\config.json。",
        )

    def _on_close(self):
        self.stop_event.set()
        try:
            self._save_config()
        except Exception:
            pass
        if self.hotkeys:
            self.hotkeys.stop()
        self.root.destroy()


def main():
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass

    root = tk.Tk()
    app = App(root)

    if "--selftest" in sys.argv:
        root.after(700, root.destroy)
    root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
