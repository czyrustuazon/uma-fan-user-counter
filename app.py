"""Uma Musume Global circle fan counter - Tkinter GUI."""
import threading
import tkinter as tk
from tkinter import ttk, messagebox

import core

MAX_WEEKS = 5
WEEK_COLS = tuple(f"w{k}" for k in range(1, MAX_WEEKS + 1))


def fmt(n):
    return "-" if n is None else f"{n:,}"


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Uma Fan Counter")
        self.geometry("1280x560")
        self.cfg = core.load_config()
        self.db = None

        style = ttk.Style(self)
        style.theme_use("clam")  # flatter than the native Windows theme, and styleable
        style.configure("TButton", padding=(12, 5))
        style.configure("Accent.TButton", background="#2f6fd1", foreground="#ffffff",
                        font=("Segoe UI", 9, "bold"))
        style.map("Accent.TButton", background=[("active", "#2559ab"), ("disabled", "#9db8e3")])
        style.configure("Card.TFrame", background="#e9ecf1")
        style.configure("Card.TLabel", background="#e9ecf1")

        # Row 1: setup + the fetch action
        top = ttk.Frame(self, padding=(10, 8))
        top.pack(fill="x")
        pad = {"padx": 5}
        L = lambda w: w.pack(side="left", **pad)
        L(ttk.Label(top, text="Circle ID:"))
        self.circle = tk.StringVar(value=self.cfg.get("circle_id", ""))
        L(ttk.Entry(top, textvariable=self.circle, width=14))
        L(ttk.Label(top, text="API key:"))
        self.key = tk.StringVar(value=self.cfg.get("api_key", ""))
        L(ttk.Entry(top, textvariable=self.key, width=26, show="*"))
        self.btn = ttk.Button(top, text="Fetch", style="Accent.TButton", command=self.fetch)
        L(self.btn)

        # Row 2: data filters, in their own shaded card
        card = ttk.Frame(self, style="Card.TFrame", padding=(10, 6))
        card.pack(fill="x", padx=10, pady=(0, 8))
        CL = lambda w: w.pack(side="left", **pad)
        CL(ttk.Label(card, text="Month:", style="Card.TLabel"))
        self.month_sel = tk.StringVar()
        self.month_box = ttk.Combobox(card, textvariable=self.month_sel, width=9, state="readonly")
        self.month_box.bind("<<ComboboxSelected>>", lambda e: self._render())
        self.month_box.pack(side="left", padx=(5, 18))
        CL(ttk.Label(card, text="Quota week:", style="Card.TLabel"))
        self.week_sel = tk.StringVar(value="Current")
        week_box = ttk.Combobox(card, textvariable=self.week_sel, width=9, state="readonly",
                                values=["Current"] + [f"Week {k}" for k in range(1, MAX_WEEKS + 1)])
        week_box.bind("<<ComboboxSelected>>", lambda e: self._render())
        week_box.pack(side="left", padx=(5, 18))
        CL(ttk.Label(card, text="Search member:", style="Card.TLabel"))
        self.query = tk.StringVar()
        self.query.trace_add("write", lambda *a: self._render())
        CL(ttk.Entry(card, textvariable=self.query, width=20))
        CL(ttk.Button(card, text="Clear", command=lambda: self.query.set("")))
        self.rows = []
        self.members = []

        # Status bar (packed first so it stays pinned to the bottom)
        bar = ttk.Frame(self, padding=(10, 5), relief="sunken")
        bar.pack(side="bottom", fill="x")
        self.status = tk.StringVar(value="Enter a circle ID and API key, then Fetch.")
        ttk.Label(bar, textvariable=self.status).pack(side="left")
        self.summary = tk.StringVar()
        ttk.Label(bar, textvariable=self.summary).pack(side="right")

        cols = ("name", "total", "daily", "last7") + WEEK_COLS + ("monthly", "quota")
        heads = ("Member", "Lifetime fans", "Day", "Last 7d") + tuple(f"Wk {k}" for k in range(1, MAX_WEEKS + 1)) \
            + ("Month", f"Quota ({core.WEEKLY_QUOTA // 10**6}M/wk)")
        style.configure("Treeview", rowheight=28, foreground="#2b2b2b")
        style.configure("Treeview.Heading", background="#d5dae3", foreground="#1f2430",
                        font=("Segoe UI", 9, "bold"), padding=(6, 6), relief="flat")
        style.map("Treeview.Heading", background=[("active", "#c4cbd8")])
        # Tk 8.6.9 ignores Treeview tag colors unless the style map drops its "!disabled !selected" entries.
        def fixed_map(option):
            return [e for e in style.map("Treeview", query_opt=option)
                    if e[:2] != ("!disabled", "!selected")]
        style.map("Treeview",
                  foreground=fixed_map("foreground") + [("selected", "#ffffff")],
                  background=fixed_map("background") + [("selected", "#2f6fd1")])
        wrap = ttk.Frame(self)
        wrap.pack(fill="both", expand=True, padx=10, pady=(0, 8))
        self.tree = ttk.Treeview(wrap, columns=cols, show="headings")
        vsb = ttk.Scrollbar(wrap, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        vsb.pack(side="right", fill="y")
        for c, h in zip(cols, heads):
            left = c == "name"
            self.tree.heading(c, text=h, anchor="w" if left else "e")
            self.tree.column(c, width=150 if left else 100, anchor="w" if left else "e",
                             stretch=left or c == "quota", minwidth=70)
        self.tree.column("quota", width=200)
        self.tree.tag_configure("odd", background="#eef0f3")
        self.tree.tag_configure("even", background="#ffffff")
        # Quota status colors the row text; only short members turn red.
        self.tree.tag_configure("ok", foreground="#1b7a3a")
        self.tree.tag_configure("short", foreground="#c62828")
        self.tree.tag_configure("partial", foreground="#8a6d00")
        self.tree.pack(side="left", fill="both", expand=True)

        self.bind("<Return>", lambda e: self.fetch())

    def fetch(self):
        cid = self.circle.get().strip()
        if not cid.isdigit():
            messagebox.showerror("Circle ID", "Circle ID must be numbers only.")
            return
        core.save_config({"circle_id": cid, "api_key": self.key.get().strip()})
        self.btn.state(["disabled"])
        self.status.set("Fetching...")
        threading.Thread(target=self._work, args=(cid, self.key.get().strip()), daemon=True).start()

    def _work(self, cid, key):
        try:
            data = core.fetch_circle(cid, key)
            members, y, m = core.parse_response(data)
            db = core.connect()
            core.store(db, members, y, m)
            rows = core.circle_table(db, members)
            months = core.months_available(db)
            db.close()
            self.after(0, self._show, rows, members, months, (y, m))
        except Exception as e:
            self.after(0, self._fail, str(e))

    def _fail(self, msg):
        self.btn.state(["!disabled"])
        self.status.set("Failed.")
        messagebox.showerror("Error", msg)

    def _show(self, rows, members, months, current):
        self.btn.state(["!disabled"])
        self.rows = rows
        self.members = members
        labels = [f"{y}-{m:02d}" for y, m in months]
        self.month_box["values"] = labels
        cur = f"{current[0]}-{current[1]:02d}"
        if self.month_sel.get() not in labels:
            self.month_sel.set(cur)
        self._render()

    def _selected_month(self):
        try:
            y, m = self.month_sel.get().split("-")
            return int(y), int(m)
        except ValueError:
            return None

    def _focus_week(self, weeks):
        """The week the quota column judges: a chosen week, or the latest week with data."""
        sel = self.week_sel.get()
        if sel == "Current":
            have = [w for w in weeks if w["gain"] is not None]
            return have[-1] if have else None
        idx = int(sel.split()[1]) - 1
        return weeks[idx] if idx < len(weeks) else None

    def _render(self):
        """Redraw the table, filtered by the search box. Summary covers the whole circle."""
        rows = self.rows
        ym = self._selected_month()
        if not rows or not ym:
            return
        q = self.query.get().strip().lower()
        year, month = ym

        # Week headings show the days each week counts (week 1 starts from the day-1 baseline).
        ranges = core.week_ranges(year, month)
        for k, col in enumerate(WEEK_COLS):
            if k < len(ranges):
                _, first, last, _ = ranges[k]
                self.tree.heading(col, text=f"Wk {k + 1} ({first}-{last})")
            else:
                self.tree.heading(col, text=f"Wk {k + 1} (-)")

        db = core.connect()
        data = {r["viewer_id"]: core.weeks_for(db, r["viewer_id"], year, month) for r in rows}
        db.close()

        def judge(r):
            """(gain, target, days, complete) for the quota week, or None."""
            w = self._focus_week(data[r["viewer_id"]]["weeks"])
            if not w or w["gain"] is None:
                return None
            return w["gain"], core.WEEKLY_QUOTA, w["days"], w["complete"], w["num"]

        judged = {r["viewer_id"]: judge(r) for r in rows}
        shown = [r for r in rows if q in r["name"].lower() or q in r["viewer_id"]]
        self.tree.delete(*self.tree.get_children())
        for i, r in enumerate(shown):
            d = data[r["viewer_id"]]
            cells = []
            for k in range(MAX_WEEKS):
                if k >= len(d["weeks"]) or d["weeks"][k]["gain"] is None:
                    cells.append("-")
                else:
                    w = d["weeks"][k]
                    cells.append(fmt(w["gain"]) + ("" if w["complete"] else "*"))
            j = judged[r["viewer_id"]]
            label = "-"
            tags = ["odd" if i % 2 else "even"]
            if j:
                gain, target, days, complete, num = j
                if gain >= target:
                    label = f"Wk {num}: ● ✔ OK"
                    tags.append("ok")
                elif complete:
                    label = f"Wk {num}: ● ✖ short {target - gain:,}"
                    tags.append("short")
                else:  # week not fully covered yet - don't call it short
                    label = f"Wk {num}: ● partial ({days}d, {target - gain:,} to go)"
                    tags.append("partial")
            self.tree.insert("", "end", tags=tags, values=(
                r["name"], fmt(r["total"]), fmt(r["daily"]), fmt(r["weekly"]), *cells,
                fmt(d["month"]), label))

        if q:
            self.status.set(f"Showing {len(shown)} of {len(rows)} members matching '{q}'")
        else:
            self.status.set(f"{len(rows)} members. Latest data: {rows[0]['as_of']}.  "
                            f"* = fewer days of data than the week")
        vals = [j for j in judged.values() if j]
        if vals:
            avg = sum(j[0] for j in vals) // len(vals)
            met = sum(j[0] >= j[1] for j in vals)
            under = sum(j[0] < j[1] and j[3] for j in vals)  # only fully covered weeks count as short
            partial = len(vals) - met - under
            self.summary.set(f"Avg gain: {avg:,}  |  Met: {met}  Short: {under}  Partial: {partial}")
        else:
            self.summary.set("No weekly data for this month yet - fetch again tomorrow "
                             "(history is saved locally).")


if __name__ == "__main__":
    App().mainloop()
