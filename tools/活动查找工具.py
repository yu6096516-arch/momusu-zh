# -*- coding: utf-8 -*-
r"""
活动文本查找工具  (Monster Girl TD 活动剧情)
------------------------------------------------
用途：输入一句中文（或日文），在 活动\**\zh_Hans.json 里定位原文，
      并直接告诉你它属于哪个活动（弧目录 / 编号段）——便于给活动做分类。

特性：
  · 一次载入全部 1835 个文件 / 53971 个键到内存，之后搜索瞬时
  · 中文、日文都能搜；空格分隔多个词 = 同时包含（AND）
  · 模糊模式：忽略标点、空白、全半角差异
  · 「所属」列直接给出分类依据：弧目录名，或单篇文件的编号段（如 单篇·1114）
  · 可点表头排序；双击复制中文；右键复制中文/日文/路径/整行
  · 可导出 CSV（Excel 直接打开）、按所属汇总的 Markdown 分类清单
  · 「限定所属」下拉可把结果收窄到某一个活动

运行：双击 活动查找工具.bat，或 python 活动查找工具.py
自检：python 活动查找工具.py --test "关键词"   （不开窗口，结果写 _查找自检.txt）
      python 活动查找工具.py --smoke           （建界面 2 秒后自动关闭，验布局）
"""
import os
import re
import sys
import json
import glob
import threading
import traceback
from datetime import datetime

APP_DIR = os.path.dirname(os.path.abspath(__file__))
ACT_DIR = os.path.join(APP_DIR, "活动")
ROOT_TAG = "（根）"
NUM_RE = re.compile(r"(\d{3,})")

ROWS = []          # (group, file, idx, key, val)
LOAD_ERROR = []


def scan_files():
    return sorted(glob.glob(os.path.join(ACT_DIR, "**", "zh_Hans.json"), recursive=True))


def code_of(fname):
    """从文件名取编号段：ev1114011nml → 1114；eb1062042anml → 1062。取不到返回原文件名。"""
    m = NUM_RE.search(fname or "")
    if not m:
        return fname or "?"
    d = m.group(1)
    return d[:4] if len(d) >= 4 else d


def load_all(progress=None):
    global ROWS, LOAD_ERROR
    ROWS = []
    LOAD_ERROR = []
    files = scan_files()
    total = len(files)
    for n, path in enumerate(files, 1):
        rel = os.path.relpath(path, ACT_DIR)
        parts = rel.split(os.sep)
        if len(parts) >= 3:                 # 活动\弧目录\文件\zh_Hans.json
            arc, fname = parts[0], parts[-2]
        else:                               # 活动\文件\zh_Hans.json
            arc, fname = ROOT_TAG, parts[-2]
        group = arc if arc != ROOT_TAG else "编号 " + code_of(fname)
        try:
            with open(path, encoding="utf-8-sig") as f:
                obj = json.load(f)
        except Exception as e:
            LOAD_ERROR.append("%s : %s" % (rel, e))
            continue
        for i, (k, v) in enumerate(obj.items()):
            if not isinstance(v, str):
                v = str(v)
            ROWS.append((group, fname, i, k, v))
        if progress and n % 200 == 0:
            progress(n, total)
    if progress:
        progress(total, total)
    return ROWS


def norm(s):
    """模糊归一：只留中日文与字母数字，去标点空白，英文小写。"""
    out = []
    for ch in s:
        if ch.isalnum() or "\u4e00" <= ch <= "\u9fff" or "\u3040" <= ch <= "\u30ff":
            out.append(ch.lower())
    return "".join(out)


def search(rows, query, scope="both", fuzzy=False, group=None):
    q = (query or "").strip()
    if not q:
        return []
    terms = [t for t in q.split() if t]
    if fuzzy:
        terms = [norm(t) for t in terms]
    terms = [t for t in terms if t]
    if not terms:
        return []

    def fields(r):
        if scope == "zh":
            return (r[4],)
        if scope == "jp":
            return (r[3],)
        return (r[4], r[3])

    hits = []
    for r in rows:
        if group and group != "（全部）" and r[0] != group:
            continue
        fs = fields(r)
        if fuzzy:
            fs = tuple(norm(f) for f in fs)
        if all(any(t in f for f in fs) for t in terms):
            hits.append(r)
    return hits


def summarize(hits):
    """按所属统计：[(所属, 命中数, 文件数)]，命中数降序。"""
    st = {}
    for g, fname, idx, k, v in hits:
        e = st.setdefault(g, [0, set()])
        e[0] += 1
        e[1].add(fname)
    out = [(g, n, len(fs)) for g, (n, fs) in st.items()]
    out.sort(key=lambda x: (-x[1], x[0]))
    return out


def export_csv(hits, path):
    import csv
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["所属", "文件", "键序号", "中文", "日文"])
        for g, fn, idx, k, v in hits:
            w.writerow([g, fn, idx, v, k])


def export_md(hits, path, query):
    L = ["# 查找结果：%s" % query, "",
         "- 生成时间：%s" % datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
         "- 命中：**%d** 条，涉及 **%d** 个文件、**%d** 个所属" % (
             len(hits), len({(h[0], h[1]) for h in hits}), len({h[0] for h in hits})),
         "", "## 按所属汇总", "",
         "| 所属 | 命中 | 文件数 |", "|---|---:|---:|"]
    for g, n, nf in summarize(hits):
        L.append("| %s | %d | %d |" % (g, n, nf))
    L += ["", "## 明细", ""]
    for g, fn, idx, k, v in hits:
        L += ["### %s \\ %s #%d" % (g, fn, idx), "",
              "- 日：%s" % k.replace("\n", "⏎"),
              "- 中：%s" % v.replace("\n", "⏎"), ""]
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(L))


def selftest(query, out="_查找自检.txt"):
    rows = load_all()
    hits = search(rows, query)
    L = ["文件总数=%d  键总数=%d  载入失败=%d" % (len(scan_files()), len(rows), len(LOAD_ERROR)),
         "查询=%s  命中=%d" % (query, len(hits)), "", "--- 按所属 ---"]
    for g, n, nf in summarize(hits)[:25]:
        L.append("%s\t%d条\t%d文件" % (g, n, nf))
    L += ["", "--- 前 15 条 ---"]
    for g, fn, idx, k, v in hits[:15]:
        L.append("%s\\%s#%d\n[日] %s\n[中] %s\n" % (g, fn, idx,
                                                   k.replace("\n", "⏎"), v.replace("\n", "⏎")))
    with open(os.path.join(APP_DIR, out), "w", encoding="utf-8") as f:
        f.write("\n".join(L))
    return len(hits)


def run_gui(smoke=False):
    import tkinter as tk
    from tkinter import ttk, messagebox, filedialog

    root = tk.Tk()
    root.title("活动文本查找工具 — 活动剧情中文化")
    root.geometry("1220x740")
    try:
        root.tk.call("tk", "scaling", 1.15)
    except Exception:
        pass

    st = {"rows": [], "hits": [], "sort_col": None, "sort_desc": False}

    top = ttk.Frame(root, padding=(10, 8))
    top.pack(fill="x")
    ttk.Label(top, text="查找：").pack(side="left")
    ent = ttk.Entry(top, font=("Microsoft YaHei UI", 11))
    ent.pack(side="left", fill="x", expand=True, padx=(0, 8))

    scope = tk.StringVar(value="both")
    ttk.Radiobutton(top, text="中文+日文", variable=scope, value="both").pack(side="left")
    ttk.Radiobutton(top, text="只搜中文", variable=scope, value="zh").pack(side="left")
    ttk.Radiobutton(top, text="只搜日文", variable=scope, value="jp").pack(side="left")
    fuzzy = tk.BooleanVar(value=True)
    ttk.Checkbutton(top, text="模糊（忽略标点空格）", variable=fuzzy).pack(side="left", padx=(10, 0))
    btn = ttk.Button(top, text="查找 (Enter)")
    btn.pack(side="left", padx=(10, 0))

    bar = ttk.Frame(root, padding=(10, 0))
    bar.pack(fill="x")
    status = ttk.Label(bar, text="正在载入…")
    status.pack(side="left")
    gbox = ttk.Combobox(bar, width=30, state="readonly")
    gbox.pack(side="right", padx=(6, 0))
    ttk.Label(bar, text="限定所属：").pack(side="right")

    mid = ttk.Frame(root, padding=(10, 6))
    mid.pack(fill="both", expand=True)
    cols = ("grp", "file", "idx", "zh", "jp")
    heads = {"grp": "所属", "file": "文件", "idx": "#", "zh": "中文", "jp": "日文"}
    widths = {"grp": 170, "file": 135, "idx": 48, "zh": 470, "jp": 380}
    tree = ttk.Treeview(mid, columns=cols, show="headings", selectmode="browse")
    for c in cols:
        tree.heading(c, text=heads[c], command=lambda c=c: sort_by(c))
        tree.column(c, width=widths[c], anchor="w", stretch=(c in ("zh", "jp")))
    vs = ttk.Scrollbar(mid, orient="vertical", command=tree.yview)
    tree.configure(yscrollcommand=vs.set)
    tree.pack(side="left", fill="both", expand=True)
    vs.pack(side="right", fill="y")
    tree.tag_configure("odd", background="#f5f5f5")

    bot = ttk.Frame(root, padding=(10, 8))
    bot.pack(fill="x")
    info = ttk.Label(bot, text="")
    info.pack(side="left")

    def copy(text):
        root.clipboard_clear()
        root.clipboard_append(text)
        status.config(text="已复制：%s" % text[:40].replace("\n", " "))

    def fill(hits):
        tree.delete(*tree.get_children())
        for n, (g, fn, idx, k, v) in enumerate(hits):
            tree.insert("", "end", values=(g, fn, idx,
                                           v.replace("\n", " ⏎ "),
                                           k.replace("\n", " ⏎ ")),
                        tags=("odd",) if n % 2 else ())
        if hits:
            tree.selection_set(tree.get_children()[0])

    def sort_by(c):
        if not st["hits"]:
            return
        desc = not st["sort_desc"] if st["sort_col"] == c else False
        st["sort_col"], st["sort_desc"] = c, desc
        keyf = {"grp": lambda r: r[0], "file": lambda r: (r[0], r[1]),
                "idx": lambda r: (r[0], r[1], r[2]), "zh": lambda r: r[4],
                "jp": lambda r: r[3]}[c]
        st["hits"].sort(key=keyf, reverse=desc)
        fill(st["hits"])

    def on_double(e):
        it = tree.focus()
        if it:
            copy(tree.set(it, "zh"))

    def menu(e):
        it = tree.identify_row(e.y)
        if not it:
            return
        tree.selection_set(it)
        g, fn, idx = tree.set(it, "grp"), tree.set(it, "file"), tree.set(it, "idx")
        zh, jp = tree.set(it, "zh"), tree.set(it, "jp")
        m = tk.Menu(root, tearoff=0)
        m.add_command(label="复制中文", command=lambda: copy(zh))
        m.add_command(label="复制日文", command=lambda: copy(jp))
        m.add_command(label="复制 所属\\文件#序号", command=lambda: copy("%s\\%s#%s" % (g, fn, idx)))
        m.add_command(label="复制整行（日 / 中）", command=lambda: copy("%s\n%s" % (jp, zh)))
        m.tk_popup(e.x_root, e.y_root)

    tree.bind("<Double-1>", on_double)
    tree.bind("<Button-3>", menu)

    def do_search(*_):
        if not st["rows"]:
            return
        hits = search(st["rows"], ent.get(), scope.get(), fuzzy.get(), gbox.get())
        hits.sort(key=lambda r: (r[0], r[1], r[2]))
        st["hits"] = hits
        fill(hits)
        summ = summarize(hits)
        info.config(text="命中 %d 条 ／ %d 个文件 ／ %d 个所属" % (
            len(hits), len({(h[0], h[1]) for h in hits}), len(summ)))
        status.config(text=("主要集中在：" + "；".join("%s %d" % (g, n) for g, n, _ in summ[:3]))
                      if summ else "无命中")

    btn.configure(command=do_search)
    ent.bind("<Return>", do_search)
    root.bind("<Control-f>", lambda e: (ent.focus_set(), ent.select_range(0, "end")))

    def save_csv():
        if not st["hits"]:
            return
        p = filedialog.asksaveasfilename(defaultextension=".csv", initialfile="查找结果.csv",
                                         filetypes=[("CSV", "*.csv")])
        if p:
            export_csv(st["hits"], p)
            status.config(text="已导出 CSV：%s" % os.path.basename(p))

    def save_md():
        if not st["hits"]:
            return
        p = filedialog.asksaveasfilename(defaultextension=".md", initialfile="查找结果.md",
                                         filetypes=[("Markdown", "*.md")])
        if p:
            export_md(st["hits"], p, ent.get())
            status.config(text="已导出分类清单：%s" % os.path.basename(p))

    ttk.Button(bot, text="导出 CSV", command=save_csv).pack(side="right", padx=(6, 0))
    ttk.Button(bot, text="导出分类清单", command=save_md).pack(side="right")

    def loader():
        try:
            rows = load_all(lambda n, t: status.config(text="正在载入 %d / %d 个文件…" % (n, t)))
            st["rows"] = rows
            groups = sorted({r[0] for r in rows})
            gbox.configure(values=["（全部）"] + groups)
            gbox.set("（全部）")
            msg = "已载入 %d 个文件 / %d 个键" % (len(scan_files()), len(rows))
            if LOAD_ERROR:
                msg += "，%d 个解析失败" % len(LOAD_ERROR)
            status.config(text=msg)
            info.config(text="输入关键词后按 Enter（Ctrl+F 回到输入框）")
            ent.focus_set()
        except Exception:
            status.config(text="载入失败")
            messagebox.showerror("载入失败", traceback.format_exc())

    threading.Thread(target=loader, daemon=True).start()
    ent.focus_set()
    if smoke:
        root.after(2000, root.destroy)
    root.mainloop()


if __name__ == "__main__":
    if len(sys.argv) >= 3 and sys.argv[1] == "--test":
        print("hits=%d" % selftest(sys.argv[2]))
    elif len(sys.argv) >= 2 and sys.argv[1] == "--smoke":
        run_gui(smoke=True)
        print("smoke ok")
    else:
        run_gui()
