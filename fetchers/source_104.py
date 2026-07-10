"""104 fetcher(v4):薪資修正、新欄位、watchlist 專屬通道、飽和偵測支援。

- 薪資改用 salaryLow / salaryHigh 解析(依 2026-07 實測欄位)
- 新增欄位:description(截 300 字)、period、apply_cnt、co_industry、employee_count、company_hash
- fetch_all 回傳「批次」結構(含頁數資訊),供 main 做飽和偵測
- fetch_watchlist:抓 watchlist 公司的全部在架職缺,本地過濾關鍵字(絕不漏抓通道)
- 代理支援 path 參數,可轉發公司職缺列表端點(需搭配 worker.js v2)
"""
import json
import os
import time
from datetime import date
from pathlib import Path

import httpx

BASE = "https://www.104.com.tw"
SEARCH_PATH = "jobs/search/api/jobs"

PROXY_URL = os.environ.get("PROXY_URL", "").strip().rstrip("/")
PROXY_TOKEN = os.environ.get("PROXY_TOKEN", "").strip()

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "zh-TW,zh;q=0.9",
    "Referer": "https://www.104.com.tw/jobs/search/",
}

DEBUG_DIR = Path(__file__).resolve().parent.parent / "data" / "debug"
MAX_RETRIES = 3
DESC_MAX_CHARS = 300


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
    sources = [pagination, meta]
    if isinstance(payload.get("data"), dict):
        sources.append(payload["data"])
    for source in sources:
        for key in ("lastPage", "totalPage", "total_pages", "totalPages"):
            if isinstance(source, dict) and source.get(key):
                return int(source[key])
    return 1


def _format_salary(item: dict) -> str:
    """依 salaryLow/salaryHigh 組合薪資描述(預設視為月薪)。"""
    try:
        low = int(item.get("salaryLow") or 0)
        high = int(item.get("salaryHigh") or 0)
    except (TypeError, ValueError):
        return ""
    if low == 0 and high == 0:
        return "面議"
    if high >= 9999999 or high == 0:
        return f"{low:,} 以上"
    if low == 0:
        return f"最高 {high:,}"
    return f"{low:,} ~ {high:,}"


def _format_period(item: dict) -> str:
    """period 推測為年資要求(0=不拘)。此對應為推測,待累積資料驗證。"""
    p = item.get("period")
    if p in (None, ""):
        return ""
    try:
        n = int(p)
    except (TypeError, ValueError):
        return str(p)
    return "不拘" if n == 0 else f"{n}年以上"


def _company_hash(item: dict) -> str:
    """從 link.cust 取公司頁代碼(例 .../company/1a2x6bmzu0 → 1a2x6bmzu0)。"""
    link = item.get("link", {}) if isinstance(item.get("link"), dict) else {}
    cust_url = link.get("cust", "") or ""
    if "/company/" in cust_url:
        return cust_url.rstrip("/").split("/company/")[-1].split("?")[0]
    return ""


def _parse_item(item: dict, keyword_group: str, default_company: str = "") -> dict:
    link = item.get("link", {}) if isinstance(item.get("link"), dict) else {}
    job_url = _pick(link, "job") or _pick(item, "jobUrl", "url")
    if job_url.startswith("//"):
        job_url = "https:" + job_url

    desc = str(_pick(item, "description", "descSnippet", default=""))
    return {
        "source": "104",
        "source_job_no": str(_pick(item, "jobNo", "id", "jobId")),
        "title": _pick(item, "jobName", "name", "title"),
        "company": _pick(item, "custName", "companyName", "company") or default_company,
        "company_no": str(_pick(item, "custNo", "companyNo")),
        "company_hash": _company_hash(item),
        "location": _pick(item, "jobAddrNoDesc", "jobAddress", "area"),
        "salary": _format_salary(item),
        "posted_date": str(_pick(item, "appearDate", "postedDate")),
        "url": job_url,
        "keyword_group": keyword_group,
        "description": desc[:DESC_MAX_CHARS],
        "period": _format_period(item),
        "apply_cnt": int(item.get("applyCnt") or 0),
        "co_industry": _pick(item, "coIndustryDesc"),
        "employee_count": int(item.get("employeeCount") or 0),
    }


def _get_with_retry(client: httpx.Client, path: str, params: dict, referer: str = "") -> httpx.Response:
    """統一請求入口:代理模式帶 path+token,直連模式打 104 本體。"""
    if PROXY_URL:
        url = PROXY_URL
        real_params = dict(params)
        real_params["token"] = PROXY_TOKEN
        real_params["path"] = path
        headers = {"Accept": "application/json"}
    else:
        url = f"{BASE}/{path}"
        real_params = params
        headers = dict(HEADERS)
        if referer:
            headers["Referer"] = referer

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


def _dump_debug(payload: dict, filename: str) -> None:
    DEBUG_DIR.mkdir(parents=True, exist_ok=True)
    path = DEBUG_DIR / filename
    if not path.exists():
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2)[:200_000], encoding="utf-8"
        )


def fetch_keyword(client: httpx.Client, keyword: str, keyword_group: str, cfg: dict) -> dict:
    """抓單一關鍵字,回傳批次:{keyword, group, jobs, pages_fetched, total_pages}。"""
    jobs = []
    max_pages = cfg.get("max_pages_per_keyword", 5)
    delay = cfg.get("request_delay_seconds", 2.5)
    areas = [a for a in cfg.get("areas", []) if str(a).strip()]

    page = 1
    total = 1
    while page <= min(total, max_pages):
        params = {"keyword": keyword, "order": "16", "page": page, "pagesize": 20}
        if areas:
            params["area"] = ",".join(str(a) for a in areas)
        if str(cfg.get("jobexp", "")).strip():
            params["jobexp"] = str(cfg["jobexp"]).strip()

        resp = _get_with_retry(client, SEARCH_PATH, params)
        payload = resp.json()
        _dump_debug(payload, "sample_response.json")

        items = _extract_items(payload)
        if page == 1:
            total = _total_pages(payload)
            print(f"  [{keyword_group}/{keyword}] 共 {total} 頁,上限 {max_pages} 頁,第 1 頁 {len(items)} 筆")

        for item in items:
            parsed = _parse_item(item, keyword_group)
            if parsed["title"] and parsed["company"]:
                jobs.append(parsed)

        page += 1
        time.sleep(delay)

    return {
        "keyword": keyword,
        "group": keyword_group,
        "jobs": jobs,
        "pages_fetched": min(total, max_pages),
        "total_pages": total,
        "max_pages": max_pages,
    }


def fetch_all(cfg: dict) -> list[dict]:
    """依 config 抓所有關鍵字,回傳批次列表(main 據此做飽和偵測)。"""
    batches = []
    with httpx.Client(follow_redirects=True) as client:
        for group, keywords in cfg.get("keyword_groups", {}).items():
            for kw in keywords:
                try:
                    batches.append(fetch_keyword(client, kw, group, cfg))
                except Exception as e:
                    print(f"  !! [{group}/{kw}] 抓取失敗: {e}")
                    batches.append({"keyword": kw, "group": group, "jobs": [],
                                    "pages_fetched": 0, "total_pages": 0,
                                    "max_pages": cfg.get("max_pages_per_keyword", 5),
                                    "failed": True})
    total = sum(len(b["jobs"]) for b in batches)
    print(f"關鍵字通道共抓到 {total} 筆(去重前),日期 {date.today()}")
    return batches


def fetch_watchlist(cfg: dict, resolved: list[dict]) -> list[dict]:
    """Watchlist 專屬通道:抓公司全部在架職缺,本地過濾關鍵字。

    resolved: [{"name": 公司名, "company_no": 公司頁代碼}](代碼空白者跳過並提示)
    回傳統一 schema 職缺列表。公司端點欄位未實測,採防禦式解析,
    首次執行會留存 sample_company_response.json 供比對。
    """
    # 攤平所有關鍵字:[(關鍵字小寫, 組名)]
    flat_keywords = [
        (str(kw).lower(), group)
        for group, kws in cfg.get("keyword_groups", {}).items()
        for kw in kws
    ]
    delay = cfg.get("request_delay_seconds", 2.5)
    results = []

    with httpx.Client(follow_redirects=True) as client:
        for entry in resolved:
            name, code = entry.get("name", ""), str(entry.get("company_no", "")).strip()
            if not code:
                print(f"  [watchlist/{name}] 尚無公司代碼(該公司未曾出現在搜尋結果),待系統累積後自動補")
                continue
            try:
                path = f"company/ajax/joblist/{code}"
                params = {"roleJobCat": 0, "area": 0, "page": 1, "pageSize": 100, "order": 8, "asc": 0}
                resp = _get_with_retry(client, path, params, referer=f"{BASE}/company/{code}")
                payload = resp.json()
                _dump_debug(payload, "sample_company_response.json")

                items = _extract_items(payload)
                matched = 0
                for item in items:
                    text = (
                        str(_pick(item, "jobName", "name", "title"))
                        + " "
                        + str(_pick(item, "description", "descSnippet", default=""))
                    ).lower()
                    hit_group = next((g for kw, g in flat_keywords if kw in text), None)
                    if hit_group:
                        parsed = _parse_item(item, hit_group, default_company=name)
                        if parsed["title"] and parsed["source_job_no"]:
                            parsed["company_hash"] = parsed["company_hash"] or code
                            results.append(parsed)
                            matched += 1
                print(f"  [watchlist/{name}] 在架 {len(items)} 筆,符合關鍵字 {matched} 筆")
            except Exception as e:
                print(f"  !! [watchlist/{name}] 抓取失敗: {e}")
            time.sleep(delay)

    print(f"watchlist 通道共 {len(results)} 筆")
    return results
