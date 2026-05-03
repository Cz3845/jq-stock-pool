import argparse
import json
import os
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

import requests


EASTMONEY_CONDITION = "前一日收盘价涨停，人气排名,非ST；非首板；非科创板；"
EASTMONEY_SEARCH_URL = "https://np-tjxg-b.eastmoney.com/api/smart-tag/stock/v3/pw/search-code"
DEFAULT_STOCK_POOL_FILE = "stock_pool.json"


@dataclass
class FetchResult:
    success: bool
    codes: list
    error: str = ""


def build_common_headers():
    return {
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Accept-Encoding": "gzip, deflate",
        "Connection": "keep-alive",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/134.0.6998.205 Safari/537.36",
        "sec-ch-ua": '"Chromium";v="134", "Not:A-Brand";v="24", "Google Chrome";v="134"',
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": '"Windows"',
    }


def build_eastmoney_payload(fingerprint):
    custom_data_new = json.dumps(
        [{"type": "text", "value": EASTMONEY_CONDITION, "extra": ""}],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return {
        "needAmbiguousSuggest": True,
        "pageSize": 50,
        "pageNo": 1,
        "fingerprint": fingerprint,
        "matchWord": "",
        "shareToGuba": False,
        "timestamp": str(int(time.time() * 1000000)),
        "requestId": "id" + uuid.uuid4().hex + str(int(time.time() * 1000)),
        "removedConditionIdList": [],
        "ownSelectAll": False,
        "needCorrect": True,
        "client": "WEB",
        "product": "",
        "needShowStockNum": False,
        "biz": "web_ai_select_stocks",
        "xcId": "xc" + uuid.uuid4().hex[:20],
        "gids": [],
        "dxInfoNew": [],
        "keyWordNew": EASTMONEY_CONDITION,
        "customDataNew": custom_data_new,
    }


def build_cookies(fingerprint):
    return {"qgqp_b_id": fingerprint, "st_si": str(uuid.uuid4().int % 100000000000000).zfill(14)}


def warmup_session(session, common_headers):
    warmup_requests = [
        ("https://xuangu.eastmoney.com/", None, {**common_headers, "Sec-Fetch-Site": "none", "Sec-Fetch-Mode": "navigate", "Sec-Fetch-Dest": "document"}),
        ("https://np-tjxg-operation-b.eastmoney.com/isPreserve", {"source": "WEB"}, {**common_headers, "Accept": "application/json, text/plain, */*", "Origin": "https://xuangu.eastmoney.com", "Referer": "https://xuangu.eastmoney.com/", "Sec-Fetch-Site": "same-site", "Sec-Fetch-Mode": "cors", "Sec-Fetch-Dest": "empty"}),
        ("https://np-tjxg-operation-b.eastmoney.com/ip/needFilter", None, {**common_headers, "Accept": "application/json, text/plain, */*", "Origin": "https://xuangu.eastmoney.com", "Referer": "https://xuangu.eastmoney.com/", "Sec-Fetch-Site": "same-site", "Sec-Fetch-Mode": "cors", "Sec-Fetch-Dest": "empty"}),
    ]
    for url, params, headers in warmup_requests:
        try:
            session.get(url, params=params, headers=headers, timeout=(5, 10))
        except requests.RequestException:
            continue


def find_stock_rows(value):
    if isinstance(value, list):
        rows = [item for item in value if isinstance(item, dict) and str(item.get("SECURITY_CODE", "")).strip()]
        if rows:
            return rows
        for item in value:
            rows = find_stock_rows(item)
            if rows:
                return rows
    elif isinstance(value, dict):
        for item in value.values():
            rows = find_stock_rows(item)
            if rows:
                return rows
    return []


def extract_stock_codes(payload):
    return [str(row.get("SECURITY_CODE", "")).strip() for row in find_stock_rows(payload)]


def fetch_eastmoney_stock_codes():
    fingerprint = uuid.uuid4().hex
    session = requests.Session()
    session.cookies.update(build_cookies(fingerprint))
    common_headers = build_common_headers()
    warmup_session(session, common_headers)
    headers = {
        **common_headers,
        "Accept": "application/json, text/plain, */*",
        "Content-Type": "application/json",
        "Origin": "https://xuangu.eastmoney.com",
        "Referer": "https://xuangu.eastmoney.com/",
        "Sec-Fetch-Site": "same-site",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Dest": "empty",
    }
    try:
        response = session.post(EASTMONEY_SEARCH_URL, headers=headers, json=build_eastmoney_payload(fingerprint), timeout=(5, 15))
        if response.status_code != 200:
            return FetchResult(False, [], f"http_{response.status_code}")
        payload = response.json()
    except (requests.RequestException, ValueError) as exc:
        return FetchResult(False, [], type(exc).__name__)
    if not isinstance(payload, dict) or str(payload.get("code", "")) != "100":
        return FetchResult(False, [], "business_error")
    return FetchResult(True, extract_stock_codes(payload), "")


def load_stock_pool_file(path):
    path = Path(path)
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as file_obj:
        data = json.load(file_obj)
    if not isinstance(data, dict):
        raise ValueError("stock pool JSON must be an object")
    return data


def update_stock_pool_data(existing_data, trade_date, codes, max_size):
    updated_data = dict(existing_data)
    updated_data[trade_date] = list(codes)[:max_size]
    return updated_data


def write_stock_pool_file(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def run_update(trade_date, file_path, dry_run=False, max_size=15):
    fetch_result = fetch_eastmoney_stock_codes()
    if not fetch_result.success:
        print(f"东方财富抓取失败，不更新股票池文件：date={trade_date}, error={fetch_result.error}")
        return False
    current_data = load_stock_pool_file(file_path)
    updated_data = update_stock_pool_data(current_data, trade_date, fetch_result.codes, max_size)
    if dry_run:
        print(json.dumps(updated_data, ensure_ascii=False, indent=2, sort_keys=True))
        return True
    write_stock_pool_file(file_path, updated_data)
    print(f"股票池文件更新完成：date={trade_date}, file={file_path}, raw_count={len(fetch_result.codes)}, final_count={len(updated_data[trade_date])}")
    return True


def parse_args():
    parser = argparse.ArgumentParser(description="Fetch Eastmoney stock pool and update stock_pool.json for GitHub Actions.")
    parser.add_argument("--date", required=True, help="Trade date in YYYY-MM-DD format")
    parser.add_argument("--file", default=DEFAULT_STOCK_POOL_FILE, help="Path to stock_pool.json")
    parser.add_argument("--dry-run", action="store_true", help="Print updated JSON without writing the file")
    return parser.parse_args()


def main():
    args = parse_args()
    max_size = int(os.environ.get("STOCK_POOL_MAX_SIZE", "15"))
    success = run_update(args.date, args.file, dry_run=args.dry_run, max_size=max_size)
    raise SystemExit(0 if success else 1)


if __name__ == "__main__":
    main()
