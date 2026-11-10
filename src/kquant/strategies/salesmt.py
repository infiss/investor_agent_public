"""salesmt: 전년 동기 대비 분기 영업이익 성장률 순위 (spec §7).

- 성장률 = (이번 분기 3개월 영업이익 − 전년 동기 3개월 영업이익) / 전년 동기 값.
- 대상: 이번 분기와 전년 동기 영업이익이 모두 양수인 종목뿐(전년 ≤ 0이면 성장률이 왜곡됨). 나머지는 결과에서 뺀다.
"""

from __future__ import annotations

import pandas as pd

from kquant.strategies.base import final_rank, register


@register("salesmt", "이익 모멘텀", "전년 동기 대비 분기 영업이익 성장률 순위(올해·전년 영업이익 모두 양수만)",
          ("symbol", "영업이익_q", "영업이익_q_1y"))
def rank_salesmt(u: pd.DataFrame) -> pd.DataFrame:
    cur, prev = u["영업이익_q"], u["영업이익_q_1y"]
    ok = (cur > 0) & (prev > 0)
    out = pd.DataFrame({"symbol": u.loc[ok, "symbol"].values, "op_income_q": cur[ok].values,
                        "op_income_q_1y": prev[ok].values})
    out["op_income_growth"] = (out["op_income_q"] - out["op_income_q_1y"]) / out["op_income_q_1y"]
    out["score"] = out["op_income_growth"]
    out["rank"] = final_rank(out, ascending=False)
    return out.sort_values("rank").reset_index(drop=True)
