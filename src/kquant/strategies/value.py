"""value: 1/PER, 1/PSR, 1/PBR, 1/PCR 각각의 순위 평균 (spec §7).

- 분자는 TTM 당기순이익(지배주주)·TTM 매출액·최신 자본총계(지배주주)·TTM 영업활동현금흐름, 분모는 기준일 시가총액.
- 역수가 클수록 상위(1위). 역수를 쓰므로 적자·자본잠식 기업이 상위에 오지 않는다.
- 지표 값이 없는 종목은 그 지표에서 최하위 순위를 받는다.
"""

from __future__ import annotations

import pandas as pd

from kquant.strategies.base import final_rank, register

METRICS = {
    "earnings_yield": "지배당기순이익_ttm",  # 1/PER
    "sales_yield": "매출액_ttm",  # 1/PSR
    "book_yield": "지배자본총계",  # 1/PBR
    "cashflow_yield": "영업활동현금흐름_ttm",  # 1/PCR
}


@register("value", "가치", "1/PER, 1/PSR, 1/PBR, 1/PCR 순위 평균(TTM·최신 자본, 기준일 시가총액)",
          ("symbol", "marcap", *METRICS.values()))
def rank_value(u: pd.DataFrame) -> pd.DataFrame:
    out = pd.DataFrame({"symbol": u["symbol"].values}, index=u.index)
    rank_cols = []
    for name, col in METRICS.items():
        out[name] = u[col] / u["marcap"].where(u["marcap"] > 0)
        out[f"r_{name}"] = out[name].rank(ascending=False, method="average", na_option="bottom")
        rank_cols.append(f"r_{name}")
    out["score"] = out[rank_cols].mean(axis=1)
    out["rank"] = final_rank(out)
    return out.sort_values("rank").reset_index(drop=True)
