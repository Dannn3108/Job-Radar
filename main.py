"""Job Radar pipeline v2:
Sheet config → 關鍵字通道 + watchlist 專屬通道 → 過濾 → 入庫 →
飽和偵測 / 0 筆保險絲 → 訊號 → digest + Sheet 推送。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from core import config_loader, db
from fetchers import source_104
from analytics import signals as sig
from output import digest


def main() -> None:
    system_signals = []

    # 1. 載入 config(Sheet 優先,yaml 備援)
    cfg = config_loader.load()
    print(f"config 來源: {cfg['_config_source']}")
    if cfg["_config_source"].startswith("yaml_fallback"):
        system_signals.append({
            "type": "system", "company": "-",
            "detail": f"⚠ 今日無法讀取 Sheet 設定,已改用 GitHub 備用設定執行。原因: {cfg['_config_source']}",
        })

    conn = db.connect()

    # 2. 關鍵字通道(廣度)
    batches = source_104.fetch_all(cfg)

    # 3. watchlist 專屬通道(深度,絕不漏抓)
    watchlist_resolved = db.resolve_watchlist(conn, cfg.get("watchlist", []))
    watch_jobs = source_104.fetch_watchlist(cfg, watchlist_resolved) if watchlist_resolved else []
    unresolved = [w["name"] for w in watchlist_resolved if not w["company_no"]]
    if unresolved:
        system_signals.append({
            "type": "system", "company": "-",
            "detail": "ℹ watchlist 尚無公司代碼(未曾出現在搜尋結果,系統將自動累積): " + "、".join(unresolved),
        })

    # 4. 過濾(職稱排除 + 公司 hash 嚴格排除;blacklist 只影響 Sheet 顯示)
    ex_title = cfg.get("exclude_title_keywords", []) or []
    excluded_resolved = db.resolve_watchlist(conn, cfg.get("excluded_companies", []))
    excluded_hashes = {e["company_no"] for e in excluded_resolved if e["company_no"]}
    unresolved_ex = [e["name"] for e in excluded_resolved if not e["company_no"]]
    if unresolved_ex:
        system_signals.append({
            "type": "system", "company": "-",
            "detail": "ℹ Blacklist 分頁的排除公司尚無 hash(系統將自動解析,或手動補 I 欄): " + "、".join(unresolved_ex),
        })

    def keep(j: dict) -> bool:
        if any(x in j["title"] for x in ex_title):
            return False
        if j.get("company_hash") and j["company_hash"] in excluded_hashes:
            return False
        return True

    # 5. 入庫(逐批,順便做飽和偵測)
    total_fetched = total_inserted = total_updated = 0
    for batch in batches:
        jobs = [j for j in batch["jobs"] if keep(j)]
        ins, upd = db.upsert_jobs(conn, jobs)
        total_fetched += len(jobs)
        total_inserted += ins
        total_updated += upd
        # 飽和偵測:抓滿頁數上限、實際還有更多頁、且整批幾乎都是首見
        if (
            batch["pages_fetched"] >= batch["max_pages"]
            and batch["total_pages"] > batch["max_pages"]
            and len(jobs) > 0
            and ins >= len(jobs) * 0.9
        ):
            system_signals.append({
                "type": "system", "company": "-",
                "detail": (
                    f"⚠ 關鍵字「{batch['keyword']}」抓滿 {batch['max_pages']} 頁且幾乎全為新職缺,"
                    "可能有遺漏 — 建議調高 config.yaml 的 max_pages_per_keyword"
                ),
            })

    w_jobs = [j for j in watch_jobs if keep(j)]
    w_ins, w_upd = db.upsert_jobs(conn, w_jobs)
    total_fetched += len(w_jobs)
    total_inserted += w_ins
    total_updated += w_upd

    # 6. 0 筆保險絲:整體抓到 0 筆時判定異常,不動資料庫狀態
    if total_fetched == 0:
        system_signals.append({
            "type": "system", "company": "-",
            "detail": "🚨 今日抓取 0 筆,疑似設定錯誤或來源異常 — 已跳過下架標記與快照,資料庫未變動。請檢查 Keywords & Filters 或 Actions log",
        })
        delisted = 0
        print("!! 0 筆保險絲觸發:跳過 mark_delisted / snapshot")
    else:
        delisted = db.mark_delisted(conn)
        db.write_snapshot(conn)

    print(f"新增 {total_inserted} | 更新 {total_updated} | 下架 {delisted}")

    # 7. 訊號
    all_signals = (
        sig.velocity_signals(conn, cfg)
        + sig.discovery_signals(conn, cfg)
        + system_signals
    )
    for s in all_signals:
        print(f"  ⚡ {s.get('company', '-')}: {s['detail']}")

    # 8. 輸出
    stats = {
        "fetched": total_fetched, "inserted": total_inserted,
        "delisted": delisted, "config_source": cfg["_config_source"],
    }
    path = digest.write_markdown_digest(conn, all_signals, stats)
    print(f"Digest 已寫入 {path}")

    # 代碼回寫:watchlist 與 excluded companies 的最新解析結果帶回 Sheet
    watchlist_final = db.resolve_watchlist(conn, cfg.get("watchlist", []))
    excluded_final = db.resolve_watchlist(conn, cfg.get("excluded_companies", []))
    watch_hashes = {w["company_no"] for w in watchlist_final if w["company_no"]}
    digest.push_to_sheet(
        conn, all_signals, cfg,
        watchlist_final, excluded_final,
        {e["company_no"] for e in excluded_final if e["company_no"]},
        watch_hashes,
        db.all_active_job_ids(conn),
    )

    conn.close()


if __name__ == "__main__":
    main()
