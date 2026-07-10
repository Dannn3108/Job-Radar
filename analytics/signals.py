"""訊號層 v2:WoW 招募加速 + 新公司發現(watchlist 改為 dict 格式)。"""
import sqlite3
from datetime import date, timedelta


def velocity_signals(conn: sqlite3.Connection, cfg: dict) -> list[dict]:
    rules = cfg["signals"]["velocity"]
    today = date.today()
    week_ago = (today - timedelta(days=7)).isoformat()
    two_weeks_ago = (today - timedelta(days=14)).isoformat()

    rows = conn.execute(
        """SELECT company,
                  SUM(CASE WHEN first_seen >= ? THEN 1 ELSE 0 END) AS this_week,
                  SUM(CASE WHEN first_seen >= ? AND first_seen < ? THEN 1 ELSE 0 END) AS prev_week
           FROM jobs
           GROUP BY company
           HAVING this_week > 0""",
        (week_ago, two_weeks_ago, week_ago),
    ).fetchall()

    signals = []
    for r in rows:
        this_w, prev_w = r["this_week"], r["prev_week"]
        if this_w < rules["min_new_jobs"]:
            continue
        growth = ((this_w - prev_w) / prev_w * 100) if prev_w > 0 else None
        if prev_w == 0 or (growth is not None and growth >= rules["growth_pct"]):
            growth_txt = f"+{growth:.0f}%" if growth is not None else "上週 0 → 新開跑"
            signals.append({
                "type": "velocity",
                "company": r["company"],
                "detail": f"招募加速:本週新增 {this_w} 筆(上週 {prev_w} 筆,{growth_txt})",
                "this_week": this_w,
            })
    signals.sort(key=lambda s: s["this_week"], reverse=True)
    return signals


def discovery_signals(conn: sqlite3.Connection, cfg: dict) -> list[dict]:
    rules = cfg["signals"]["discovery"]
    watchlist_names = {w["name"] for w in cfg.get("watchlist", [])}
    since = (date.today() - timedelta(days=rules["window_days"])).isoformat()

    rows = conn.execute(
        """SELECT company, COUNT(*) AS n,
                  GROUP_CONCAT(DISTINCT keyword_group) AS groups
           FROM jobs
           WHERE first_seen >= ?
           GROUP BY company
           HAVING n >= ?
           ORDER BY n DESC""",
        (since, rules["min_jobs"]),
    ).fetchall()

    return [
        {
            "type": "discovery",
            "company": r["company"],
            "detail": (
                f"過去 {rules['window_days']} 天開出 {r['n']} 筆目標職缺"
                f"({r['groups']}),不在 watchlist — 建議研究後加入追蹤"
            ),
        }
        for r in rows
        if not any(name and name in r["company"] for name in watchlist_names)
    ]
