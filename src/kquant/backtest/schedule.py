"""리밸런싱 일정 (2026-11-10 결정).

- ``annual``: 매년 사업보고서 법정 제출 기한(3/31) 뒤에 한 번 리밸런싱한다.
- ``quarterly``: 정기 보고서 기한 네 번(3/31 사업, 5/15 1Q, 8/14 반기, 11/14 3Q) 뒤마다 리밸런싱한다.
두 일정 모두 연중 투자하고, 기간마다 전량 매도·재매수한다(보수적 비용 가정).

날짜 규칙(2026-11-10): 판단일 = 기한 이후(당일 포함) 첫 거래일에서 ``LAG_TRADING_DAYS``(5)거래일 뒤,
리밸런싱(매수)일 = 판단일 다음 거래일. 실제 접수일 데이터가 없어 ``available_date``는 법정 기한(결산일 + 45/90일)으로
고정되므로, 기한을 넘겨 늦게 낸 보고서를 미리 아는 것처럼 쓰지 않도록 여유를 둔다. 기간의 마지막 평가일은 다음 판단일이다.
첫 기간은 2018년 사업보고서 기한(2017 사업보고서 기준, 판단 2018-04-09, 첫 매수 2018-04-10)이다.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

SCHEDULES = ("annual", "quarterly")
FIRST_YEAR = 2018
DEADLINES = {"annual": ((3, 31),), "quarterly": ((3, 31), (5, 15), (8, 14), (11, 14))}
LAG_TRADING_DAYS = 5  # 기한 이후 첫 거래일 → 판단일까지 여유(사용자 결정 2026-11-10)


@dataclass(frozen=True)
class Period:
    begin: pd.Timestamp  # 명목 시작일(보고서 제출 기한)
    end: pd.Timestamp  # 명목 종료일(다음 기한 전날)
    rebalance_date: pd.Timestamp  # 실제 매수일
    end_date: pd.Timestamp  # 실제 마지막 평가일(다음 판단일, 진행 중이면 최신일)
    closed: bool  # 다음 기간의 매수일까지 가격이 있음

    @property
    def id(self) -> str:
        return self.begin.strftime("%Y-%m")


def deadlines(schedule: str, latest: pd.Timestamp, first_year: int = FIRST_YEAR) -> list[pd.Timestamp]:
    """``latest``보다 하나 뒤의 기한까지(마지막 기간의 명목 종료일 계산용)."""
    if schedule not in SCHEDULES:
        raise ValueError(f"알 수 없는 일정: {schedule}")
    out = []
    for y in range(first_year, latest.year + 2):
        for m, d in DEADLINES[schedule]:
            out.append(pd.Timestamp(year=y, month=m, day=d))
            if out[-1] > latest:
                return out
    return out


def periods(schedule: str, trading_days: pd.DatetimeIndex, first_year: int = FIRST_YEAR) -> list[Period]:
    """``trading_days``: 전체 거래일(정렬). 마지막 값이 최신 데이터일이다."""
    latest = trading_days[-1]
    dl = deadlines(schedule, latest, first_year)

    def decision_and_buy(d: pd.Timestamp) -> tuple[pd.Timestamp | None, pd.Timestamp | None]:
        after = trading_days[trading_days >= d]
        k = LAG_TRADING_DAYS
        return (after[k] if len(after) > k else None), (after[k + 1] if len(after) > k + 1 else None)

    out = []
    for i, d in enumerate(dl[:-1]):
        _, reb = decision_and_buy(d)
        if reb is None:
            break
        nxt_decision, nxt_buy = decision_and_buy(dl[i + 1])
        closed = nxt_buy is not None
        end_date = nxt_decision if closed else latest
        out.append(Period(d, dl[i + 1] - pd.Timedelta(days=1), reb, end_date, closed))
    return out
