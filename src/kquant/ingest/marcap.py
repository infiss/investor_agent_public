"""FinanceData/marcap 연도별 parquet 동기화.

marcap 저장소는 평일 장 마감 후 GitHub Actions로 당해 연도 파일을 갱신한다.
키가 필요 없고, 당해 연도 파일(약 20MB)만 다시 받으면 된다.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import pandas as pd
import requests

RAW_URL = "https://raw.githubusercontent.com/FinanceData/marcap/master/data/marcap-{year}.parquet"
REQUIRED_COLUMNS = {"Date", "Code", "Name", "Close", "Changes", "Volume", "Amount", "Marcap", "Stocks", "Market"}
TIMEOUT = 120


@dataclass
class SyncResult:
    year: int
    path: Path
    status: str          # "updated" | "unchanged" | "rejected"
    latest_date: str | None
    rows: int | None
    message: str = ""


def _meta_path(target: Path) -> Path:
    return target.with_suffix(".meta.json")


def _validate(path: Path, year: int, previous_latest: pd.Timestamp | None) -> tuple[pd.Timestamp, int]:
    df = pd.read_parquet(path, columns=None)
    missing = REQUIRED_COLUMNS - set(df.columns) - {"ChangesRatio"}
    if missing:
        raise ValueError(f"필수 컬럼 누락: {sorted(missing)}")
    dates = pd.to_datetime(df["Date"])
    if not (dates.dt.year == year).all():
        raise ValueError("다른 연도 데이터가 섞여 있음")
    latest = dates.max()
    if previous_latest is not None and latest < previous_latest:
        raise ValueError(f"받은 파일의 최신일({latest.date()})이 기존({previous_latest.date()})보다 과거")
    return latest, len(df)


def sync_year(data_dir: Path, year: int | None = None, session: requests.Session | None = None) -> SyncResult:
    """한 연도 파일을 내려받아 검증 후 교체한다. ETag가 같으면 받지 않는다."""
    year = year or date.today().year
    target = data_dir / f"marcap-{year}.parquet"
    meta_file = _meta_path(target)
    meta = json.loads(meta_file.read_text(encoding="utf-8")) if meta_file.exists() else {}
    headers = {"If-None-Match": meta["etag"]} if meta.get("etag") and target.exists() else {}

    s = session or requests.Session()
    resp = s.get(RAW_URL.format(year=year), headers=headers, timeout=TIMEOUT)
    if resp.status_code == 304:
        return SyncResult(year, target, "unchanged", meta.get("latest_date"), meta.get("rows"))
    resp.raise_for_status()

    previous_latest = None
    if target.exists():
        previous_latest = pd.to_datetime(pd.read_parquet(target, columns=["Date"])["Date"]).max()

    tmp = target.with_suffix(".download")
    tmp.write_bytes(resp.content)
    try:
        latest, rows = _validate(tmp, year, previous_latest)
    except (ValueError, OSError) as exc:  # 손상 파일·스키마 불일치 시 기존 파일 유지
        tmp.unlink(missing_ok=True)
        return SyncResult(year, target, "rejected", None, None, str(exc))
    os.replace(tmp, target)
    meta = {"etag": resp.headers.get("ETag"), "latest_date": str(latest.date()), "rows": rows}
    meta_file.write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
    return SyncResult(year, target, "updated", meta["latest_date"], rows)
