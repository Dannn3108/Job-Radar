"""輸出層 v2:新欄位、blacklist 過濾、Tracker 下架比對、watchlist 代碼回寫。"""
import os
import sqlite3
from datetime import date
from pathlib import Path
 
import httpx
 
DIGEST_DIR = Path(__file__).resolve().parent.parent / "data" / "digests"

# GAS 會在最前面補「標記」欄,這裡的 header 不含標記
JOBS_HEADER = [
    "Job ID", "First Discovery Date", "Company", "Title", "Location", "Salary", "Tenure",
    "Applications", "Industry", "Company Size", "Keyword", "JD", "Status", "Link",
]
SIGNALS_HEADER = ["Log-in Date", "Signal Type", "Company", "Description"]

SHEET_DESC_CHARS = 100



def _active_jobs(conn: sqlite3.Connection, blacklist: set[str],
                 excluded_hashes: set[str], watchlist_hashes: set[str]) -> list[list]:
    """在架職缺 → Sheet 列。排序:watchlist 公司置頂,其後按首次發現日新→舊。"""
    rows = conn.execute(
        """SELECT job_id, first_seen, company, title, location, salary, period,
                  apply_cnt, co_industry, employee_count, keyword_group,
                  description, status, url, company_hash
           FROM jobs WHERE status = 'active'"""
    ).fetchall()
    out = []
    for r in rows:
        if r["job_id"] in blacklist:
            continue
        if r["company_hash"] and r["company_hash"] in excluded_hashes:
            continue
        is_watch = 1 if (r["company_hash"] and r["company_hash"] in watchlist_hashes) else 0
        row = list(r)[:14]  # company_hash 僅供排序,不輸出到 Sheet
        row[11] = (row[11] or "")[:SHEET_DESC_CHARS]
        out.append((is_watch, row))
    # 排序:watchlist 置頂 → 首次發現日新→舊
    out.sort(key=lambda t: (t[0], t[1][1]), reverse=True)
    return [row for _, row in out]
 
 
def write_markdown_digest(conn, signals: list[dict], stats: dict) -> Path:
    today = date.today().isoformat()
    new_rows = conn.execute(
        "SELECT company, title, location, salary, url FROM jobs WHERE first_seen = ? ORDER BY company",
        (today,),
    ).fetchall()
 
    lines = [f"# Job Radar Digest — {today}", ""]
    lines.append(
        f"抓取 {stats['fetched']} 筆 | 新增 {stats['inserted']} | 下架 {stats['delisted']}"
        f" | config 來源: {stats.get('config_source', '?')}"
    )
    lines.append("")
    if signals:
        lines.append("## ⚡ 訊號")
        for s in signals:
            lines.append(f"- **{s['company']}** — {s['detail']}")
        lines.append("")
    if new_rows:
        lines.append(f"## 🆕 今日新職缺({len(new_rows)})")
        for r in new_rows:
            lines.append(f"- {r['company']}|{r['title']}|{r['location']}|{r['salary']} — {r['url']}")
 
    DIGEST_DIR.mkdir(parents=True, exist_ok=True)
    path = DIGEST_DIR / f"{today}.md"
    path.write_text("\n".join(lines), encoding="utf-8")
    return path
 
 
def push_to_sheet(conn, signals: list[dict], cfg: dict, watchlist_resolved: list[dict],
                  excluded_resolved: list[dict], excluded_hashes: set[str],
                  watchlist_hashes: set[str], active_ids: list[str]) -> None:
    url = os.environ.get("SHEET_WEBHOOK_URL", "").strip()
    token = os.environ.get("SHEET_TOKEN", "").strip()
    if not url:
        print("SHEET_WEBHOOK_URL 未設定,跳過 Google Sheet 推送")
        return
 
    blacklist = set(cfg.get("blacklist", []))
    payload = {
        "token": token,
        "date": date.today().isoformat(),
        "jobs_header": JOBS_HEADER,
        "jobs": _active_jobs(conn, blacklist, excluded_hashes, watchlist_hashes),
        "signals_header": SIGNALS_HEADER,
        "signals": [
            [date.today().isoformat(), s["type"], s.get("company", "-"), s["detail"]]
            for s in signals
        ],
        "watchlist_resolved": watchlist_resolved,
        "excluded_resolved": excluded_resolved,
        "active_ids": active_ids,
    }
    resp = httpx.post(url, json=payload, timeout=120, follow_redirects=True)
    resp.raise_for_status()
    print(f"Google Sheet 已更新: {resp.text[:100]}")
