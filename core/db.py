"""SQLite 儲存層 v2:新欄位 + 自動遷移(既有資料不會遺失)。"""
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

# v2 新增欄位:自動遷移(存在則略過)
NEW_COLUMNS = {
    "company_hash": "TEXT DEFAULT ''",
    "description": "TEXT DEFAULT ''",
    "period": "TEXT DEFAULT ''",
    "apply_cnt": "INTEGER DEFAULT 0",
    "co_industry": "TEXT DEFAULT ''",
    "employee_count": "INTEGER DEFAULT 0",
}


def make_job_id(job: dict) -> str:
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
    _migrate(conn)
    return conn


def _migrate(conn: sqlite3.Connection) -> None:
    existing = {r["name"] for r in conn.execute("PRAGMA table_info(jobs)")}
    for col, ddl in NEW_COLUMNS.items():
        if col not in existing:
            conn.execute(f"ALTER TABLE jobs ADD COLUMN {col} {ddl}")
            print(f"db migrate: 新增欄位 {col}")
    conn.commit()


def upsert_jobs(conn: sqlite3.Connection, jobs: list[dict]) -> tuple[int, int]:
    """寫入職缺。既有職缺更新 last_seen / apply_cnt / salary 等動態值。"""
    today = date.today().isoformat()
    inserted = updated = 0
    for job in jobs:
        job_id = make_job_id(job)
        row = conn.execute("SELECT job_id FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
        if row:
            conn.execute(
                """UPDATE jobs SET last_seen = ?, status = 'active',
                   apply_cnt = ?, salary = ?,
                   company_hash = CASE WHEN company_hash = '' THEN ? ELSE company_hash END,
                   description = CASE WHEN description = '' THEN ? ELSE description END
                   WHERE job_id = ?""",
                (today, job.get("apply_cnt", 0), job.get("salary", ""),
                 job.get("company_hash", ""), job.get("description", ""), job_id),
            )
            updated += 1
        else:
            conn.execute(
                """INSERT INTO jobs (job_id, source, source_job_no, company, company_no,
                   company_hash, title, location, salary, url, posted_date, keyword_group,
                   description, period, apply_cnt, co_industry, employee_count,
                   first_seen, last_seen, status)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?, 'active')""",
                (
                    job_id, job["source"], job["source_job_no"], job["company"],
                    job.get("company_no", ""), job.get("company_hash", ""),
                    job["title"], job["location"], job["salary"], job["url"],
                    job["posted_date"], job["keyword_group"],
                    job.get("description", ""), job.get("period", ""),
                    job.get("apply_cnt", 0), job.get("co_industry", ""),
                    job.get("employee_count", 0),
                    today, today,
                ),
            )
            inserted += 1
    conn.commit()
    return inserted, updated


def mark_delisted(conn: sqlite3.Connection) -> int:
    today = date.today().isoformat()
    cur = conn.execute(
        "UPDATE jobs SET status = 'delisted' WHERE last_seen < ? AND status = 'active'",
        (today,),
    )
    conn.commit()
    return cur.rowcount


def write_snapshot(conn: sqlite3.Connection) -> None:
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


def resolve_watchlist(conn: sqlite3.Connection, watchlist: list[dict]) -> list[dict]:
    """為 watchlist 補公司頁代碼:Sheet 已填的直接用,空白的從資料庫比對公司名。"""
    resolved = []
    for entry in watchlist:
        name = str(entry.get("name", "")).strip()
        code = str(entry.get("company_no", "")).strip()
        if name and not code:
            row = conn.execute(
                """SELECT company_hash FROM jobs
                   WHERE company LIKE ? AND company_hash != ''
                   ORDER BY last_seen DESC LIMIT 1""",
                (f"%{name}%",),
            ).fetchone()
            if row:
                code = row["company_hash"]
        resolved.append({"name": name, "company_no": code})
    return resolved


def all_active_job_ids(conn: sqlite3.Connection) -> list[str]:
    return [r["job_id"] for r in conn.execute("SELECT job_id FROM jobs WHERE status = 'active'")]
