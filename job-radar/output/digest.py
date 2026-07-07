"""輸出層:每日 digest(markdown 存檔)+ 推送 Google Sheet(GAS webhook)。"""
import os
import sqlite3
from datetime import date
from pathlib import Path

import httpx

DIGEST_DIR = Path(__file__).resolve().parent.parent / "data" / "digests"

JOBS_HEADER = ["job_id", "首次發現", "公司", "職稱", "地點", "薪資", "關鍵字組", "狀態", "連結"]
SIGNALS_HEADER = ["日期", "類型", "公司", "說明"]


def _active_jobs(conn: sqlite3.Connection) -> list[list]:
    rows = conn.execute(
        """SELECT job_id, first_seen, company, title, location, salary,
                  keyword_group, status, url
           FROM jobs WHERE status = 'active'
           ORDER BY first_seen DESC, company"""
    ).fetchall()
    return [list(r) for r in rows]


def write_markdown_digest(conn, signals: list[dict], stats: dict) -> Path:
    today = date.today().isoformat()
    new_rows = conn.execute(
        "SELECT company, title, location, url FROM jobs WHERE first_seen = ? ORDER BY company",
        (today,),
    ).fetchall()

    lines = [f"# Job Radar Digest — {today}", ""]
    lines.append(f"抓取 {stats['fetched']} 筆 | 新增 {stats['inserted']} | 下架 {stats['delisted']}")
    lines.append("")
    if signals:
        lines.append("## ⚡ 訊號")
        for s in signals:
            lines.append(f"- **{s['company']}** — {s['detail']}")
        lines.append("")
    if new_rows:
        lines.append(f"## 🆕 今日新職缺({len(new_rows)})")
        for r in new_rows:
            lines.append(f"- {r['company']}|{r['title']}|{r['location']} — {r['url']}")

    DIGEST_DIR.mkdir(parents=True, exist_ok=True)
    path = DIGEST_DIR / f"{today}.md"
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def push_to_sheet(conn, signals: list[dict]) -> None:
    """POST 到 Apps Script webhook。未設定 SHEET_WEBHOOK_URL 時安靜跳過。"""
    url = os.environ.get("SHEET_WEBHOOK_URL", "").strip()
    token = os.environ.get("SHEET_TOKEN", "").strip()
    if not url:
        print("SHEET_WEBHOOK_URL 未設定,跳過 Google Sheet 推送")
        return

    payload = {
        "token": token,
        "date": date.today().isoformat(),
        "jobs_header": JOBS_HEADER,
        "jobs": _active_jobs(conn),
        "signals_header": SIGNALS_HEADER,
        "signals": [
            [date.today().isoformat(), s["type"], s["company"], s["detail"]]
            for s in signals
        ],
    }
    resp = httpx.post(url, json=payload, timeout=60, follow_redirects=True)
    resp.raise_for_status()
    print(f"Google Sheet 已更新: {resp.text[:100]}")
