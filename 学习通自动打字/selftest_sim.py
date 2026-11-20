# -*- coding: utf-8 -*-
"""离线自测：模拟“回车自动缩进”的编辑器，验证四种模式都能得到正确代码。

不会真的发送按键：把 press/send_char/send_ctrl_v 换成直接操作 Text 控件。
"""
import importlib.util
import os
import tkinter as tk

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("xx", os.path.join(HERE, "学习通自动打字.py"))
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

out = []

# ---- 1) 剪贴板往返 ---------------------------------------------------------
old = m.get_clipboard_text()
m.set_clipboard_text("剪贴板测试 abc 123")
assert m.get_clipboard_text() == "剪贴板测试 abc 123"
if old is not None:
    m.set_clipboard_text(old)
out.append("clipboard ok")

# ---- 2) 待输入代码（就是用户报错的那段） -----------------------------------
CODE = (
    "class Circle:\n"
    "    def __init__(self, r):\n"
    "        self.r = r\n"
    "\n"
    "    def get_area(self):\n"
    "        return 3.14 * self.r ** 2\n"
    "\n"
    "    def get_perimeter(self):\n"
    "        return 2 * 3.14 * self.r\n"
    "\n"
    "\n"
    "# 输入半径\n"
    'r = float(input("输入: "))\n'
    "\n"
    "# 创建实例\n"
    "circle = Circle(r)\n"
    "\n"
    "# 输出面积和周长，保留两位小数点\n"
    'print(f"{circle.get_area():.2f} {circle.get_perimeter():.2f}")\n'
)


def make_editor(auto_indent=True):
    root = tk.Tk()
    root.geometry("700x420")
    txt = tk.Text(root, font=("Consolas", 11), undo=True)

    if auto_indent:
        def on_return(event):
            line_start = txt.index("insert linestart")
            prefix = txt.get(line_start, "insert")
            indent = prefix[: len(prefix) - len(prefix.lstrip(" \t"))]
            txt.insert("insert", "\n" + indent)
            return "break"

        txt.bind("<Return>", on_return)

    txt.pack(fill="both", expand=True)
    txt.focus_force()
    root.update()
    return root, txt


shift = {"on": False}


def sim_install(txt):
    def fake_press(vk, hold=0.0):
        if vk == m.VK["RETURN"]:
            txt.event_generate("<Return>", when="now")
        elif vk == m.VK["HOME"]:
            txt.event_generate("<Home>", when="now")
        elif vk == m.VK["END"]:
            txt.event_generate("<Shift-End>" if shift["on"] else "<End>", when="now")
        elif vk == m.VK["DELETE"]:
            txt.event_generate("<Delete>", when="now")
        elif vk == m.VK["TAB"]:
            txt.insert("insert", "\t")

    def fake_ctrl_v():
        txt.insert("insert", m.get_clipboard_text() or "")

    m.press = fake_press
    m.key_down = lambda vk: shift.__setitem__("on", True) if vk == m.VK["SHIFT"] else None
    m.key_up = lambda vk: shift.__setitem__("on", False) if vk == m.VK["SHIFT"] else None
    m.send_char = lambda ch: txt.insert("insert", ch)
    m.send_ctrl_v = fake_ctrl_v
    m.send_ctrl = lambda vk: None
    m.foreground_pid = lambda: 999999


def run_job(mode):
    root, txt = make_editor(auto_indent=True)
    sim_install(txt)
    opts = dict(m.DEFAULT_OPTS)
    opts.update(mode=mode, start_delay=0, char_delay=0, line_delay=0,
                expand_tabs=True, strip_trailing=True, restore_clipboard=False)
    events = []
    job = m.TypingJob(CODE, opts, lambda k, *a: events.append((k, a)), m.threading.Event())
    job.run()
    txt.update()
    content = txt.get("1.0", "end-1c")
    root.destroy()
    nospace = "\n".join(ln.rstrip() for ln in content.split("\n"))
    exact = content == CODE
    loose = nospace == "\n".join(ln.rstrip() for ln in CODE.split("\n"))
    done = [a for k, a in events if k == "done"]
    return exact, loose, done, content


for mode in ("paste", "line_paste", "line", "char"):
    exact, loose, done, content = run_job(mode)
    if mode == "char":
        # 逐字模式本来就不该用在这种会自动缩进的编辑器上，这里只验证它“确实会跑偏”
        verdict = "预期跑偏（该模式只给记事本用）" if not loose else "反常：没有跑偏"
    else:
        verdict = "MATCH" if exact else ("MATCH(仅行尾空格)" if loose else "MISMATCH")
    out.append(f"[{mode}] {verdict} done={done}")
    if verdict == "MISMATCH":
        out.append("---- 实际 ----\n" + content)

with open(os.path.join(HERE, "selftest_result.txt"), "w", encoding="utf-8") as f:
    f.write("\n".join(out))
