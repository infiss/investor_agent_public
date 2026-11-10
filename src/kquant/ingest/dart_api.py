"""OpenDART API: 고유번호(corp_code) 매핑과 최근 공시 목록 (spec §10).

- ``update_corp_codes``: 운영자 CLI 전용. ``corpCode.xml``(zip)을 받아 상장사만 ``ref/corp_codes.parquet``로 저장한다.
- ``recent_disclosures``: 공개 도구(추천)가 호출한다. ``list.json``에서 최근 공시 N건. 타임아웃·오류는 빈 목록으로
  처리하고(서비스가 멈추지 않게) 사유를 돌려준다. 같은 종목은 프로세스 안에서 ``CACHE_SEC`` 동안 캐시한다.
API 키는 환경변수 ``DART_API_KEY``로만 받는다(코드·로그에 남기지 않는다).
"""

from __future__ import annotations

import io
import time
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

import pandas as pd
import requests

from kquant.config import secret

BASE = "https://opendart.fss.or.kr/api"
TIMEOUT = 10
CACHE_SEC = 3600
_cache: dict[tuple, tuple[float, dict]] = {}


def corp_codes_path(paths) -> Path:
    return paths.data / "ref" / "corp_codes.parquet"


def update_corp_codes(paths, session: requests.Session | None = None) -> int:
    s = session or requests.Session()
    try:
        r = s.get(f"{BASE}/corpCode.xml", params={"crtfc_key": secret("DART_API_KEY")}, timeout=60)
        r.raise_for_status()
    except requests.RequestException as e:
        # 예외 메시지에는 키가 담긴 URL이 들어가므로 그대로 올리지 않는다
        status = getattr(getattr(e, "response", None), "status_code", None)
        raise RuntimeError(f"고유번호 다운로드 실패({type(e).__name__}, HTTP {status})") from None
    with zipfile.ZipFile(io.BytesIO(r.content)) as z:
        root = ET.fromstring(z.read(z.namelist()[0]))
    rows = []
    for el in root.iter("list"):
        stock = (el.findtext("stock_code") or "").strip()
        if stock:
            rows.append({"symbol": stock, "corp_code": el.findtext("corp_code").strip(),
                         "corp_name": (el.findtext("corp_name") or "").strip(),
                         "modify_date": (el.findtext("modify_date") or "").strip()})
    df = pd.DataFrame(rows).sort_values("modify_date").drop_duplicates("symbol", keep="last")
    out = corp_codes_path(paths)
    out.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(out, index=False)
    return len(df)


def load_corp_codes(paths) -> dict[str, str]:
    f = corp_codes_path(paths)
    if not f.exists():
        return {}
    df = pd.read_parquet(f)
    return dict(zip(df["symbol"], df["corp_code"], strict=True))


def recent_disclosures(corp_code: str, n: int = 5, days: int = 90, session: requests.Session | None = None,
                       today: pd.Timestamp | None = None) -> dict:
    """{"items": [{date, title, filer, url}], "error": None|사유}."""
    key = (corp_code, n, days)
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < CACHE_SEC:
        return hit[1]
    try:
        api_key = secret("DART_API_KEY")
    except RuntimeError:
        return {"items": [], "error": "공시 조회 설정이 없습니다(DART_API_KEY)"}
    end = (today or pd.Timestamp.today()).normalize()
    params = {"crtfc_key": api_key, "corp_code": corp_code, "bgn_de": (end - pd.Timedelta(days=days)).strftime("%Y%m%d"),
              "end_de": end.strftime("%Y%m%d"), "page_count": max(1, min(n, 20)), "sort": "date", "sort_mth": "desc"}
    try:
        r = (session or requests).get(f"{BASE}/list.json", params=params, timeout=TIMEOUT)
        data = r.json()
    except (requests.RequestException, ValueError):
        return {"items": [], "error": "공시 서버에 연결하지 못했습니다"}
    status = data.get("status")
    if status == "013":  # 조회된 데이터 없음
        out = {"items": [], "error": None}
    elif status != "000":
        return {"items": [], "error": f"공시 조회 실패(상태 {status})"}
    else:
        out = {"items": [{
            "date": f"{x['rcept_dt'][:4]}-{x['rcept_dt'][4:6]}-{x['rcept_dt'][6:]}",
            "title": x.get("report_nm", "").strip(), "filer": x.get("flr_nm", ""),
            "url": f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={x['rcept_no']}",
        } for x in data.get("list", [])[:n]], "error": None}
    _cache[key] = (time.time(), out)
    return out
