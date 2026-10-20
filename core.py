"""Fetching, storage and gain calculations for the Uma fan counter."""
import calendar
import json
import os
import sqlite3
import sys
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta, timezone

API_URL = "https://uma.moe/api/v4/circles?circle_id={}"
JST = timezone(timedelta(hours=9))
# Quota weeks are the same in every month: 1-8, 9-15, 16-23, 24-1st of next month.
WEEK_QUOTAS = (21_000_000, 21_000_000, 21_000_000, 21_000_000)
MONTHLY_QUOTA = 84_000_000  # = sum(WEEK_QUOTAS)

DATA_DIR = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), "UmaFanCounter")
CONFIG_PATH = os.path.join(DATA_DIR, "config.json")
DB_PATH = os.path.join(DATA_DIR, "fans.db")


# ---------- config ----------
def _app_dir():
    return os.path.dirname(sys.executable if getattr(sys, "frozen", False) else os.path.abspath(__file__))


def load_env(path=None):
    """Read KEY=VALUE lines from a .env file next to the app (or in the data dir)."""
    paths = [path] if path else [os.path.join(_app_dir(), ".env"), os.path.join(DATA_DIR, ".env")]
    env = {}
    for p in reversed(paths):  # app-dir file wins over data-dir file
        try:
            with open(p, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        k, v = line.split("=", 1)
                        env[k.strip()] = v.strip().strip("'\"")
        except OSError:
            pass
    return env


def load_config():
    try:
        with open(CONFIG_PATH, encoding="utf-8") as f:
            cfg = json.load(f)
    except (OSError, ValueError):
        cfg = {}
    env = load_env()
    # .env values fill in anything not already saved from the GUI
    if env.get("UMA_API_KEY") and not cfg.get("api_key"):
        cfg["api_key"] = env["UMA_API_KEY"]
    if env.get("UMA_CIRCLE_ID") and not cfg.get("circle_id"):
        cfg["circle_id"] = env["UMA_CIRCLE_ID"]
    return cfg


def save_config(cfg):
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f)


# ---------- fetching ----------
class ApiError(Exception):
    pass


def fetch_circle(circle_id, api_key=""):
    headers = {"Accept": "application/json", "User-Agent": "UmaFanCounter/1.0"}
    if api_key:
        headers["X-API-Key"] = api_key
    req = urllib.request.Request(API_URL.format(circle_id), headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.load(resp)
    except urllib.error.HTTPError as e:
        try:
            msg = json.load(e).get("message", "")
        except Exception:
            msg = ""
        raise ApiError(f"HTTP {e.code}: {msg or e.reason}") from None
    except (urllib.error.URLError, OSError) as e:
        raise ApiError(f"Network error: {e}") from None


def _find_members(node):
    if isinstance(node, list):
        if node and all(isinstance(x, dict) for x in node) and any("daily_fans" in x for x in node):
            return node
        for x in node:
            r = _find_members(x)
            if r:
                return r
    elif isinstance(node, dict):
        for v in node.values():
            r = _find_members(v)
            if r:
                return r
    return None


def _find_year_month(node):
    if isinstance(node, dict):
        y, m = node.get("year"), node.get("month")
        if isinstance(y, int) and isinstance(m, int) and 1 <= m <= 12:
            return y, m
        for v in node.values():
            r = _find_year_month(v)
            if r:
                return r
    elif isinstance(node, list):
        for x in node:
            r = _find_year_month(x)
            if r:
                return r
    return None


def parse_response(data, today=None):
    """Return (members, year, month); members = [(viewer_id, name, daily_fans list)]."""
    members = _find_members(data)
    if not members:
        raise ApiError("No members with 'daily_fans' found in the response.")
    ym = _find_year_month(data)
    if not ym:
        t = today or datetime.now(JST).date()
        ym = (t.year, t.month)
    out = []
    for m in members:
        out.append((str(m.get("viewer_id", "")), m.get("trainer_name") or "?",
                    [int(v or 0) for v in m.get("daily_fans", [])]))
    return out, ym[0], ym[1]


# ---------- storage ----------
def connect(path=DB_PATH):
    if path != ":memory:":
        os.makedirs(os.path.dirname(path), exist_ok=True)
    db = sqlite3.connect(path)
    db.execute("CREATE TABLE IF NOT EXISTS fans (viewer_id TEXT, day TEXT, fans INTEGER, "
               "PRIMARY KEY (viewer_id, day))")
    db.execute("CREATE TABLE IF NOT EXISTS names (viewer_id TEXT PRIMARY KEY, name TEXT)")
    return db


def store(db, members, year, month):
    for vid, name, daily in members:
        db.execute("INSERT OR REPLACE INTO names VALUES (?, ?)", (vid, name))
        for i, v in enumerate(daily):
            if v > 0:  # 0 = future/unknown day
                d = date(year, month, i + 1).isoformat()
                db.execute("INSERT OR REPLACE INTO fans VALUES (?, ?, ?)", (vid, d, v))
    db.commit()


# ---------- stats ----------
def compute(db, viewer_id):
    """Daily / weekly / monthly gains from stored lifetime totals.

    Returns dict or None. 'weekly_days' < 7 means history is shorter than a week.
    """
    rows = db.execute("SELECT day, fans FROM fans WHERE viewer_id=? ORDER BY day",
                      (viewer_id,)).fetchall()
    if not rows:
        return None
    series = {date.fromisoformat(d): f for d, f in rows}
    last = max(series)
    cur = series[last]

    def gain(days_back):
        target = last - timedelta(days=days_back)
        if target in series:
            return cur - series[target], days_back
        older = [d for d in series if d < last and d >= target]
        if older:
            base = min(older)
            return cur - series[base], (last - base).days
        return None, 0

    daily, _ = gain(1)
    weekly, wdays = gain(7)
    month_days = [d for d in series if (d.year, d.month) == (last.year, last.month)]
    mbase = min(month_days)
    monthly = cur - series[mbase] if mbase != last else None
    return {"as_of": last, "total": cur, "daily": daily, "weekly": weekly,
            "weekly_days": wdays, "monthly": monthly}


def months_available(db):
    """Sorted list of (year, month) that have stored data."""
    rows = db.execute("SELECT DISTINCT substr(day, 1, 7) FROM fans ORDER BY 1").fetchall()
    return [(int(r[0][:4]), int(r[0][5:7])) for r in rows]


def week_ranges(year, month):
    """The four quota weeks of a month: 1-8, 9-15, 16-23, 24-(1st of next month).

    Returns [(week_no, first_counted_day, last_day, baseline_day, quota)] as dates. Gains are
    measured from the baseline day's lifetime total, so nothing from the previous month is
    counted: week 1 uses day 1 as its baseline (counts days 2-8), later weeks use the previous
    week's last day. Week 4 runs into day 1 of the next month.
    """
    nxt = date(year + (month == 12), month % 12 + 1, 1)
    ends = [date(year, month, 8), date(year, month, 15), date(year, month, 23), nxt]
    bases = [date(year, month, 1)] + ends[:-1]
    return [(k + 1, bases[k] + timedelta(days=1), ends[k], bases[k], WEEK_QUOTAS[k]) for k in range(4)]


def week_label(first, last):
    """'1 - 8 Oct' or '24 Oct - 1 Nov' (en dashes)."""
    if first.month == last.month:
        return f"{first.day} – {last.day} {last:%b}"
    return f"{first.day} {first:%b} – {last.day} {last:%b}"


def weeks_for(db, viewer_id, year, month):
    """Per-week gains for one month. Returns {'weeks': [...], 'month': gain or None}.

    Each week: {'num', 'first', 'last', 'gain', 'days', 'expected', 'complete', 'quota'}.
    gain is None when there is no data for the week; complete=False means the data
    covers fewer days than the week (history started mid-week, or week still in progress).
    """
    ranges = week_ranges(year, month)
    lo, hi = ranges[0][3], ranges[-1][2]
    rows = db.execute("SELECT day, fans FROM fans WHERE viewer_id=? AND day BETWEEN ? AND ?",
                      (viewer_id, lo.isoformat(), hi.isoformat())).fetchall()
    series = {date.fromisoformat(d): f for d, f in rows}
    weeks = []
    for num, wfirst, wlast, base, quota in ranges:
        expected = (wlast - base).days
        gain, days = None, 0
        if series:
            b = base if base in series else min((d for d in series if base < d <= wlast), default=None)
            e = max((d for d in series if d <= wlast), default=None)
            if b is not None and e is not None and e > b:
                gain, days = series[e] - series[b], (e - b).days
        weeks.append({"num": num, "first": wfirst, "last": wlast, "gain": gain, "days": days,
                      "expected": expected, "complete": days == expected, "quota": quota})
    month_gain = None
    if len(series) > 1:
        month_gain = series[max(series)] - series[min(series)]
    return {"weeks": weeks, "month": month_gain}


def circle_table(db, members):
    rows = []
    for vid, name, _ in members:
        s = compute(db, vid)
        if s:
            rows.append({"viewer_id": vid, "name": name, **s})
    rows.sort(key=lambda r: -(r["weekly"] or 0))
    return rows
