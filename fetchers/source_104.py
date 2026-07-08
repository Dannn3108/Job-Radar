"""104 人力銀行 search API fetcher.

104 的搜尋 API 是半公開的(網頁前端自己在用),欄位可能隨改版變動。
因此這裡採防禦式寫法:
  1. 欄位用「候選名單」逐一嘗試
  2. 第一次執行會把 raw response 存到 data/debug/,解析失敗時可以直接看原始資料修
"""
import json
import time
from datetime import date
from pathlib import Path

import httpx

SEARCH_URL = "https://www.104.com.tw/jobs/search/api/jobs"
HEADERS = {
    "Referer": "https://www.104.com.tw/jobs/search/",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
    ),
    "Accept": "application/json",
}

DEBUG_DIR = Path(__file__).resolve().parent.parent / "data" / "debug"


def _pick(d: dict, *candidates, default=""):
    """從候選欄位名中取第一個存在的值。"""
    for key in candidates:
        if key in d and d[key] not in (None, ""):
            return d[key]
    return default


def _extract_items(payload: dict):
    """從 response 中找出職缺列表(容忍不同的包裝結構)。"""
    if isinstance(payload.get("data"), list):
        return payload["data"]
    data = payload.get("data", {})
    if isinstance(data, dict):
        for key in ("list", "jobs", "items"):
            if isinstance(data.get(key), list):
                return data[key]
    return []


def _total_pages(payload: dict) -> int:
    meta = payload.get("metadata", {}) or {}
    pagination = meta.get("pagination", {}) or {}
    for source in (pagination, meta, payload.get("data", {}) if isinstance(payload.get("data"), dict) else {}):
        for key in ("lastPage", "totalPage", "total_pages"):
            if isinstance(source, dict) and source.get(key):
                return int(source[key])
    return 1


def _parse_item(item: dict, keyword_group: str) -> dict:
    """raw item → 統一 schema。欄位名以 2025 年觀察到的為主,備援舊欄位名。"""
    link = item.get("link", {}) if isinstance(item.get("link"), dict) else {}
    job_url = _pick(link, "job") or _pick(item, "jobUrl", "url")
    if job_url.startswith("//"):
        job_url = "https:" + job_url

    return {
        "source": "104",
        "source_job_no": str(_pick(item, "jobNo", "id", "jobId")),
        "title": _pick(item, "jobName", "name", "title"),
        "company": _pick(item, "custName", "companyName", "company"),
        "company_no": str(_pick(item, "custNo", "companyNo")),
        "location": _pick(item, "jobAddrNoDesc", "jobAddress", "area"),
        "salary": _pick(item, "salaryDesc", "salary"),
        "posted_date": str(_pick(item, "appearDate", "postedDate")),
        "url": job_url,
        "keyword_group": keyword_group,
    }


def fetch_keyword(client: httpx.Client, keyword: str, keyword_group: str, cfg: dict) -> list[dict]:
    """抓單一關鍵字的所有分頁,回傳統一 schema 的職缺列表。"""
    results = []
    max_pages = cfg.get("max_pages_per_keyword", 5)
    delay = cfg.get("request_delay_seconds", 2.5)

    page = 1
    total = 1
    while page <= min(total, max_pages):
        params = {
            "keyword": keyword,
            "area": ",".join(cfg.get("areas", [])),
            "order": "16",  # 依日期排序(新→舊)
            "page": page,
            "pagesize": 20,
        }
        if cfg.get("jobexp"):
            params["jobexp"] = cfg["jobexp"]

        resp = client.get(SEARCH_URL, params=params, headers=HEADERS, timeout=30)
        resp.raise_for_status()
        payload = resp.json()

        # 首次執行留存 raw sample 供 debug
        DEBUG_DIR.mkdir(parents=True, exist_ok=True)
        sample_path = DEBUG_DIR / "sample_response.json"
        if not sample_path.exists():
            sample_path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2)[:200_000],
                encoding="utf-8",
            )

        items = _extract_items(payload)
        if page == 1:
            total = _total_pages(payload)
            print(f"  [{keyword_group}/{keyword}] 共 {total} 頁,抓取上限 {max_pages} 頁,第 1 頁 {len(items)} 筆")

        for item in items:
            parsed = _parse_item(item, keyword_group)
            if parsed["title"] and parsed["company"]:
                results.append(parsed)

        page += 1
        time.sleep(delay)

    return results


def fetch_all(cfg: dict) -> list[dict]:
    """依 config 的所有關鍵字組抓取,回傳合併結果(尚未去重)。"""
    all_jobs = []
    with httpx.Client(follow_redirects=True) as client:
        for group, keywords in cfg.get("keyword_groups", {}).items():
            for kw in keywords:
                try:
                    all_jobs.extend(fetch_keyword(client, kw, group, cfg))
                except Exception as e:  # 單一關鍵字失敗不中斷整個 pipeline
                    print(f"  !! [{group}/{kw}] 抓取失敗: {e}")
    print(f"共抓到 {len(all_jobs)} 筆(去重前),日期 {date.today()}")
    return all_jobs
