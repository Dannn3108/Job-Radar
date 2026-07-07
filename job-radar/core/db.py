"""SQLite 儲存層:jobs / daily_snapshots 兩張表。

WoW 一律以系統自己記錄的 first_seen 為準,不信任平台的 posted_date
(公司會重登職缺洗排序,平台日期會失真)。
"""
import hashlib
import sqlite3
from datetime import date
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "jobs.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    job_id        TEXT PRIMARY KEY,
    source        TEXT,
    source_job_no TEXT,
    company       TEXT,
    company_no    TEXT,
    title         TEXT,
    location      TEXT,
    salary        TEXT,
    url           TEXT,
    posted_date   TEXT,
    keyword_group TEXT,
    first_seen    TEXT,
    last_seen     TEXT,
    status        TEXT DEFAULT 'active'
);
CREATE INDEX IF NOT EXISTS idx_jobs_company ON jobs(company);
CREATE INDEX IF NOT EXISTS idx_jobs_first_seen ON jobs(first_seen);

CREATE TABLE IF NOT EXISTS daily_snapshots (
    snapshot_date TEXT,
    company       TEXT,
    keyword_group TEXT,
    active_jobs   INTEGER,
    new_jobs      INTEGER,
    PRIMARY KEY (snapshot_date, company, keyword_group)
);
"""


def make_job_id(job: dict) -> str:
    """優先用 source+jobNo(最穩);缺 jobNo 時退回 company+title。"""
    key = (
        f"{job['source']}:{job['source_job_no']}"
        if job.get("source_job_no")
        else f"{job['source']}:{job['company']}:{job['title']}"
    )
    return hashlib.sha1(key.encode("utf-8")).hexdigest()[:16]


def connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def upsert_jobs(conn: sqlite3.Connection, jobs: list[dict]) -> tuple[int, int]:
    """寫入職缺。回傳 (新增數, 既有更新數)。"""
    today = date.today().isoformat()
    inserted = updated = 0
    for job in jobs:
        job_id = make_job_id(job)
        row = conn.execute("SELECT job_id FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
        if row:
            conn.execute(
                "UPDATE jobs SET last_seen = ?, status = 'active' WHERE job_id = ?",
                (today, job_id),
            )
            updated += 1
        else:
            conn.execute(
                """INSERT INTO jobs (job_id, source, source_job_no, company, company_no,
                   title, location, salary, url, posted_date, keyword_group,
                   first_seen, last_seen, status)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?, 'active')""",
                (
                    job_id, job["source"], job["source_job_no"], job["company"],
                    job["company_no"], job["title"], job["location"], job["salary"],
                    job["url"], job["posted_date"], job["keyword_group"],
                    today, today,
                ),
            )
            inserted += 1
    conn.commit()
    return inserted, updated


def mark_delisted(conn: sqlite3.Connection) -> int:
    """今天沒再出現的職缺標記為下架。"""
    today = date.today().isoformat()
    cur = conn.execute(
        "UPDATE jobs SET status = 'delisted' WHERE last_seen < ? AND status = 'active'",
        (today,),
    )
    conn.commit()
    return cur.rowcount


def write_snapshot(conn: sqlite3.Connection) -> None:
    """寫入當日各公司 × 關鍵字組的統計快照。"""
    today = date.today().isoformat()
    conn.execute("DELETE FROM daily_snapshots WHERE snapshot_date = ?", (today,))
    conn.execute(
        """INSERT INTO daily_snapshots
           SELECT ?, company, keyword_group,
                  SUM(CASE WHEN status = 'active' THEN 1 ELSE 0 END),
                  SUM(CASE WHEN first_seen = ? THEN 1 ELSE 0 END)
           FROM jobs GROUP BY company, keyword_group""",
        (today, today),
    )
    conn.commit()
