"""운영자 CLI. 공개 에이전트 도구에는 노출하지 않는다."""

from __future__ import annotations

import argparse
import os
import sys
from datetime import date

import pandas as pd

from kquant.config import load_paths
from kquant.fin.dart_txt import discover
from kquant.fin.long import build_long
from kquant.fin.quality import marcap_at
from kquant.ingest.marcap import sync_year
from kquant.prices.adjust import build_events, update_events


def cmd_update_prices(args: argparse.Namespace) -> int:
    paths = load_paths()
    year = args.year or date.today().year
    res = sync_year(paths.marcap_raw, year)
    print(f"marcap-{year}: {res.status} latest={res.latest_date} rows={res.rows} {res.message}")
    if res.status == "rejected":
        return 1
    if args.no_events:
        return 0
    if res.status == "updated" or not paths.adj_events.exists():
        rebuild_events(paths, year)
    return 0


def rebuild_events(paths, year: int | None = None) -> None:
    files = sorted(paths.marcap_raw.glob("marcap-*.parquet"))
    paths.adj_events.parent.mkdir(parents=True, exist_ok=True)
    if year is None or not paths.adj_events.exists():
        events = build_events(files)
    else:
        target = paths.marcap_file(year)
        earlier = [f for f in files if int(f.stem.split("-")[1]) < year]
        events = update_events(pd.read_parquet(paths.adj_events), target, earlier)
    events.to_parquet(paths.adj_events, index=False)
    print(f"adj_events: {len(events):,}건 저장 → {paths.adj_events}")


def cmd_rebuild_events(args: argparse.Namespace) -> int:
    rebuild_events(load_paths(), None)
    return 0


def cmd_build_fin(args: argparse.Namespace) -> int:
    paths = load_paths()
    files = discover(paths.dart_raw)
    marcap_files = sorted(paths.marcap_raw.glob("marcap-*.parquet"))
    long, rep = build_long(files, marcap_lookup=lambda keys: marcap_at(marcap_files, keys))
    paths.fin_long.parent.mkdir(parents=True, exist_ok=True)
    tmp = paths.fin_long.with_suffix(".tmp.parquet")
    long.to_parquet(tmp, index=False)
    tmp.replace(paths.fin_long)
    conf = rep.conflicts.assign(values=lambda d: d["values"].astype(str), names=lambda d: d["names"].astype(str))
    conf.to_parquet(paths.fin_conflicts, index=False)
    print(f"fin long: {len(long):,}행, 기간 {len(rep.periods)}개 ({rep.periods[0]}–{rep.periods[-1]}) → {paths.fin_long}")
    print(f"숫자 변환 실패 {rep.unparsable}건, 동순위 충돌 {len(rep.conflicts)}건 → {paths.fin_conflicts}")
    flags = rep.scale_flags
    flags.to_parquet(paths.fin_quality, index=False)
    n_fix = int((flags["status"] == "corrected").sum())
    print(f"단위 오기입: 보정 {n_fix}건, 검토 필요 {len(flags) - n_fix}건 → {paths.fin_quality}")
    print(f"같은 결산기준일이 여러 라벨 연도에 있는 종목·분기: {len(rep.duplicate_periods)}행")
    return 0


def cmd_screen(args: argparse.Namespace) -> int:
    import json

    from kquant.catalog.screen import screen
    from kquant.context import DataContext

    print(json.dumps(screen(json.loads(args.query), DataContext()), ensure_ascii=False, indent=2))
    return 0


def cmd_run_grid(args: argparse.Namespace) -> int:
    from kquant.backtest.grid import run_grid
    from kquant.context import DataContext

    ctx = DataContext()
    meta = run_grid(ctx, ctx.paths.grid_dir, full=args.full, only=args.key or None)
    print(meta)
    return 0



def _print_json(obj) -> None:
    import json

    print(json.dumps(obj, ensure_ascii=False, indent=2))


def cmd_report(args: argparse.Namespace) -> int:
    import json

    from kquant import service
    from kquant.context import DataContext

    ctx = DataContext()
    res = service.strategy_report(ctx, json.loads(args.params))
    if res.get("ok") and "://" not in res["report"]:
        res["report_path"] = str(ctx.paths.report_dir / res["report"])  # 운영자 확인용(공개 응답에는 없음)
    _print_json(res)
    return 0



def cmd_check_hermes(args: argparse.Namespace) -> int:
    from kquant.hermes_check import check

    res = check(args.url)
    _print_json(res)
    return 0 if res["ok"] else 2


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="kquant", description="kquant 운영자 도구")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("update-prices", help="marcap 당해(또는 지정) 연도 파일 동기화 + 수정계수 갱신")
    p.add_argument("--year", type=int)
    p.add_argument("--no-events", action="store_true",
                   help="시세 파일만 받고 수정계수 이벤트는 건드리지 않음(새 PC에서 과거 연도를 받을 때)")
    p.set_defaults(func=cmd_update_prices)

    p = sub.add_parser("rebuild-events", help="전체 연도에서 수정계수 이벤트 테이블 재생성")
    p.set_defaults(func=cmd_rebuild_events)

    p = sub.add_parser("build-fin", help="DART txt 전체에서 long 포맷 재무 데이터 생성 (dataset/fin/long.parquet)")
    p.set_defaults(func=cmd_build_fin)

    p = sub.add_parser("screen", help="조건 검색 질의(JSON) 실행 — 공개 도구와 같은 경로")
    p.add_argument("query", help='예: {"sort": {"field": "market_cap"}, "limit": 5}')
    p.set_defaults(func=cmd_screen)

    p = sub.add_parser("run-grid", help="백테스트 그리드 계산(종료된 기간은 재사용, --full이면 전체 재계산)")
    p.add_argument("--full", action="store_true")
    p.add_argument("--key", action="append", help="특정 그리드 키만 (여러 번 지정 가능)")
    p.set_defaults(func=cmd_run_grid)


    p = sub.add_parser("report", help='전략 리포트 생성. 예: {"strategy": "value", "n": 20, "schedule": "quarterly", '
                                        '"start_year": 2018, "end_year": 2021}')
    p.add_argument("params")
    p.set_defaults(func=cmd_report)


    p = sub.add_parser("check-hermes", help="Hermes 공개 프로필의 노출 도구 점검(/v1/toolsets, HERMES_API_KEY 선택)")
    # 공개 프로필 = 8643(기본 포트 8642는 운영 프로필 몫, 2026-11-10 결정 D4). 클라우드에서는 서버 안에서 loopback으로 점검한다.
    p.add_argument("--url", default=os.environ.get("KQUANT_HERMES_URL") or "http://127.0.0.1:8643",
                   help="공개 프로필 API 서버 주소(기본: 환경변수 KQUANT_HERMES_URL, 없으면 http://127.0.0.1:8643)")
    p.set_defaults(func=cmd_check_hermes)

    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
