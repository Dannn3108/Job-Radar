"""Config 載入器:Google Sheet(Keywords & Filters)優先,失敗時退回 config.yaml。

回傳的 cfg 額外帶:
  - blacklist: [job_id, ...](來自 Sheet 隱藏分頁 _Blacklist)
  - watchlist: [{"name":..., "company_no":...}](統一為 dict 格式)
  - _config_source: "sheet" | "yaml" | "yaml_fallback (原因)"
"""
import os
from pathlib import Path

import httpx
import yaml

CONFIG_PATH = Path(__file__).resolve().parent.parent / "config.yaml"

# Sheet 端可覆蓋的鍵
SHEET_KEYS = (
    "keyword_groups", "areas", "jobexp",
    "exclude_title_keywords", "excluded_companies",
    "watchlist", "blacklist",
)


def _normalize_company_list(cfg: dict, key: str) -> None:
    """公司清單統一轉成 [{"name":..., "company_no":...}] 格式(容忍純名字列表)。"""
    normalized = []
    for w in cfg.get(key, []) or []:
        if isinstance(w, dict):
            normalized.append({"name": str(w.get("name", "")).strip(),
                               "company_no": str(w.get("company_no", "")).strip()})
        else:
            normalized.append({"name": str(w).strip(), "company_no": ""})
    cfg[key] = [w for w in normalized if w["name"]]


def load() -> dict:
    cfg = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))
    cfg.setdefault("blacklist", [])
    cfg["_config_source"] = "yaml"

    url = os.environ.get("SHEET_WEBHOOK_URL", "").strip()
    token = os.environ.get("SHEET_TOKEN", "").strip()

    if url:
        try:
            resp = httpx.get(
                url, params={"token": token, "action": "config"},
                timeout=60, follow_redirects=True,
            )
            resp.raise_for_status()
            remote = resp.json()
            if isinstance(remote, dict) and remote.get("error"):
                raise ValueError(f"GAS 回報錯誤: {remote['error']}")
            if not remote.get("keyword_groups"):
                raise ValueError("Sheet config 缺少關鍵字(keyword_groups 為空)")

            for key in SHEET_KEYS:
                if key in remote:
                    cfg[key] = remote[key]
            cfg["_config_source"] = "sheet"
        except Exception as e:
            cfg["_config_source"] = f"yaml_fallback ({type(e).__name__}: {e})"

    # yaml fallback 的舊鍵名相容:exclude_companies(純名單)→ excluded_companies
    if "excluded_companies" not in cfg and cfg.get("exclude_companies"):
        cfg["excluded_companies"] = cfg.get("exclude_companies")
    cfg.setdefault("excluded_companies", [])
    _normalize_company_list(cfg, "watchlist")
    _normalize_company_list(cfg, "excluded_companies")
    cfg["blacklist"] = [str(b).strip() for b in (cfg.get("blacklist") or []) if str(b).strip()]
    return cfg
