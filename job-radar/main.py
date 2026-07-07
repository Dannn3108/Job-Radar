"""Job Radar pipeline 進入點:抓取 → 入庫 → 快照 → 訊號 → 輸出。"""
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))

from fetchers import source_104
from core import db
from analytics import signals as sig
from output import digest


def main() -> None:
    cfg = yaml.safe_load((Path(__file__).parent / "config.yaml").read_text(encoding="utf-8"))

    # 1. 抓取
    jobs = source_104.fetch_all(cfg)

    # 2. 標題排除規則
    excludes = cfg.get("exclude_title_keywords", [])
    jobs = [j for j in jobs if not any(x in j["title"] for x in excludes)]

    # 3. 入庫(job_id 天然去重:同職缺被多個關鍵字抓到只會存一筆)
    conn = db.connect()
    inserted, updated = db.upsert_jobs(conn, jobs)
    delisted = db.mark_delisted(conn)
    db.write_snapshot(conn)
    print(f"新增 {inserted} | 更新 {updated} | 下架 {delisted}")

    # 4. 訊號
    all_signals = sig.velocity_signals(conn, cfg) + sig.discovery_signals(conn, cfg)
    for s in all_signals:
        print(f"  ⚡ {s['company']}: {s['detail']}")

    # 5. 輸出
    stats = {"fetched": len(jobs), "inserted": inserted, "delisted": delisted}
    path = digest.write_markdown_digest(conn, all_signals, stats)
    print(f"Digest 已寫入 {path}")
    digest.push_to_sheet(conn, all_signals)

    conn.close()


if __name__ == "__main__":
    main()
