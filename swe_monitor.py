#!/usr/bin/env python3
"""Monitor a SWE-bench run as a refreshing TABLE: progress, score, cost, tokens, cache, disk.

Reads the launcher's results.json (one record per graded instance, written incrementally)
and prints an aligned table each interval. Cross-checks estimated cost against the LLM Hub
/me balance (real spend — note it also counts anything else on the shared hub account).

Score = resolved / attempted, where attempted = every instance that finished running
(done + failed). A failed or done-but-unresolved instance counts as unresolved — the same
denominator the leaderboard uses, not resolved/done.

Usage: set -a; source .env; set +a; python3 swe_monitor.py [results.json] [interval_s] [total]
"""
import os, sys, time, json, shutil, unicodedata, urllib.request
from datetime import datetime

RESULTS = sys.argv[1] if len(sys.argv) > 1 else "output/swebench_pro_runs/baseline_deepseek/results.json"
INTERVAL = int(sys.argv[2]) if len(sys.argv) > 2 else 60
TOTAL = int(sys.argv[3]) if len(sys.argv) > 3 else 731
BASE = os.environ.get("LLM_HUB_API_BASE", "").rstrip("/")
KEY = os.environ.get("LLM_HUB_API_KEY", "")


def dw(s):  # display width: CJK/full-width glyphs take two columns
    return sum(2 if unicodedata.east_asian_width(c) in "WF" else 1 for c in str(s))


def pad(s, width):
    return str(s) + " " * max(0, width - dw(s))


def table(title, rows, note=""):
    L = max(dw(k) for k, _ in rows)
    R = max([dw(v) for _, v in rows] + [dw(title)])
    top = "┌" + "─" * (L + 2) + "┬" + "─" * (R + 2) + "┐"
    bot = "└" + "─" * (L + 2) + "┴" + "─" * (R + 2) + "┘"
    out = [f"╒═ {title} ═╕", top]
    for k, v in rows:
        sep = "├" + "─" * (L + 2) + "┼" + "─" * (R + 2) + "┤"
        if k == "---":
            out.append(sep)
        else:
            out.append(f"│ {pad(k, L)} │ {pad(v, R)} │")
    out.append(bot)
    if note:
        out.append(note)
    return "\n".join(out)


def hub_used():
    try:
        req = urllib.request.Request(BASE + "/me", headers={"Authorization": "Bearer " + KEY})
        return json.load(urllib.request.urlopen(req, timeout=15))["used_usd"]
    except Exception:
        return None


def load():
    try:
        return json.load(open(RESULTS))
    except Exception:
        return []


def agg(records):
    s = dict(n=0, done=0, resolved=0, failed=0, cost=0.0, tin=0, tout=0, cr=0, cw=0, calls=0)
    for r in records:
        s["n"] += 1
        if r.get("status") == "done":
            s["done"] += 1
        else:
            s["failed"] += 1
        if r.get("resolved"):
            s["resolved"] += 1
        sp = r.get("spend") or {}
        s["cost"] += float(sp.get("total_cost_usd", 0) or 0)
        s["tin"] += int(sp.get("input_tokens", 0) or 0)
        s["tout"] += int(sp.get("output_tokens", 0) or 0)
        s["cr"] += int(sp.get("cache_read_tokens", 0) or 0)
        s["cw"] += int(sp.get("cache_write_tokens", 0) or 0)
        s["calls"] += int(sp.get("n_llm_calls", 0) or 0)
    return s


start = time.time()
used0 = hub_used()
while True:
    recs = load()
    s = agg(recs)
    free = shutil.disk_usage("/mnt/raid/data/zwt").free / 1e12
    hit = (s["cr"] / (s["cr"] + s["tin"]) * 100) if (s["cr"] + s["tin"]) else 0.0
    # score = resolved / attempted (attempted = done + failed = every finished instance)
    attempted = s["n"]
    score = (s["resolved"] / attempted * 100) if attempted else 0.0
    used = hub_used()
    real = (used - used0) if (used is not None and used0 is not None) else None
    title = f"SWE-bench 监控  {datetime.now():%H:%M:%S}  (用时 {(time.time()-start)/60:.0f}min)"
    rows = [
        ("进度", f"{s['n']} / {TOTAL}   done={s['done']}  failed={s['failed']}"),
        ("resolved", f"{s['resolved']}"),
        ("分数", f"{score:.1f}%   (resolved {s['resolved']} / 已处理 {attempted})"),
        ("---", ""),
        ("成本估算", f"${s['cost']:.4f}"),
        ("hub真实(累计)", f"${real:.4f}   (含codex/共享账户)" if real is not None else "N/A"),
        ("tokens", f"in {s['tin']:,}   out {s['tout']:,}"),
        ("cache_read", f"{s['cr']:,}   (命中 {hit:.1f}%)"),
        ("LLM调用", f"{s['calls']:,}"),
        ("---", ""),
        ("磁盘剩余", f"{free:.2f} TB"),
    ]
    recent = []
    for r in recs[-3:]:
        sp = r.get("spend") or {}
        mark = "✓" if r.get("resolved") else "✗"
        recent.append(f"  {mark} {r.get('instance_id','?')[:46]:<46} {r.get('status'):<7} "
                      f"${float(sp.get('total_cost_usd',0) or 0):.4f}")
    note = "最近完成:\n" + ("\n".join(recent) if recent else "  (还没有完成的实例)")
    print(table(title, rows, note), flush=True)
    print(flush=True)
    time.sleep(INTERVAL)
