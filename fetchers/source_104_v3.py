"""104 人力銀行 search API fetcher(v3:支援 Cloudflare Worker 代理)。

v3 變更:
  - 若環境變數 PROXY_URL / PROXY_TOKEN 有設定,所有請求改走代理
    (GitHub Actions IP 被 104 封鎖時的解法)
  - 走代理時不需要 warm-up 和瀏覽器 headers(由 Worker 端處理)
"""
import json
import os
import time
from datetime import date
from pathlib import Path

import httpx

SEARCH_PAGE = "https://www.104.com.tw/jobs/search/"
SEARCH_URL = "https://www.104.com.tw/jobs/search/api/jobs"

PROXY_URL = os.environ.get("PROXY_URL", "").strip().rstrip("/")
PROXY_TOKEN = os.environ.get("PROXY_TOKEN", "").strip()

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "zh-TW,zh;q=0.9,en-US;q=0.8,en;q=0.7",
    "Referer": "https://www.104.com.tw/jobs/search/",
    "Origin": "https://www.104.com.tw",
    "Sec-Fetch-Dest": "empty",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Site": "same-origin",
}

DEBUG_DIR = Path(__file__).resolve().parent.parent / "data" / "debug"
MAX_RETRIES = 3


def _pick(d: dict, *candidates, default=""):
    for key in candidates:
        if key in d and d[key] not in (None, ""):
            return d[key]
    return default


def _extract_items(payload: dict):
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


def _request_url_and_params(params: dict) -> tuple[str, dict, dict]:
    """依是否設定代理,決定實際請求的 URL / params / headers。"""
    if PROXY_URL:
        proxied = dict(params)
        proxied["token"] = PROXY_TOKEN
        return PROXY_URL, proxied, {"Accept": "application/json"}
    return SEARCH_URL, params, HEADERS


def _warm_up(client: httpx.Client) -> None:
    if PROXY_URL:
        print(f"使用代理模式: {PROXY_URL[:40]}...(跳過 warm-up)")
        return
    try:
        resp = client.get(
            SEARCH_PAGE,
            params={"keyword": "採購"},
            headers={**HEADERS, "Accept": "text/html,application/xhtml+xml"},
            timeout=30,
        )
        print(f"warm-up: HTTP {resp.status_code}, cookies: {len(client.cookies)} 個")
    except Exception as e:
        print(f"warm-up 失敗(不中斷): {e}")


def _get_with_retry(client: httpx.Client, params: dict) -> httpx.Response:
    url, real_params, headers = _request_url_and_params(params)
    last_exc = None
    for attempt in range(MAX_RETRIES):
        try:
            resp = client.get(url, params=real_params, headers=headers, timeout=30)
            resp.raise_for_status()
            return resp
        except httpx.HTTPStatusError as e:
            last_exc = e
            if e.response.status_code in (403, 429):
                wait = 5 * (2 ** attempt)
                print(f"    HTTP {e.response.status_code},{wait}s 後重試({attempt + 1}/{MAX_RETRIES})")
                time.sleep(wait)
            else:
                raise
    raise last_exc


def fetch_keyword(client: httpx.Client, keyword: str, keyword_group: str, cfg: dict) -> list[dict]:
    results = []
    max_pages = cfg.get("max_pages_per_keyword", 5)
    delay = cfg.get("request_delay_seconds", 2.5)

    page = 1
    total = 1
    while page <= min(total, max_pages):
        params = {
            "keyword": keyword,
            "area": ",".join(cfg.get("areas", [])),
            "order": "16",
            "page": page,
            "pagesize": 20,
        }
        if cfg.get("jobexp"):
            params["jobexp"] = cfg["jobexp"]

        resp = _get_with_retry(client, params)
        payload = resp.json()

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
    all_jobs = []
    failures = 0
    total_keywords = sum(len(v) for v in cfg.get("keyword_groups", {}).values())

    with httpx.Client(follow_redirects=True) as client:
        _warm_up(client)
        time.sleep(2)

        for group, keywords in cfg.get("keyword_groups", {}).items():
            for kw in keywords:
                try:
                    all_jobs.extend(fetch_keyword(client, kw, group, cfg))
                except Exception as e:
                    failures += 1
                    print(f"  !! [{group}/{kw}] 抓取失敗: {e}")

    if failures == total_keywords and total_keywords > 0:
        mode = "代理" if PROXY_URL else "直連"
        print(
            f"\n*** 全部關鍵字抓取失敗(目前為{mode}模式)***\n"
            "*** 請把此 log 貼給 Claude 診斷 ***"
        )

    print(f"共抓到 {len(all_jobs)} 筆(去重前),日期 {date.today()}")
    return all_jobs
