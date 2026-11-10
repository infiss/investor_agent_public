"""경로와 비밀값 설정. 모든 경로는 환경변수(.env 포함)에서 읽고 pathlib로 다룬다."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class Paths:
    data: Path

    @property
    def marcap_raw(self) -> Path:
        """marcap 원본 연도별 parquet 위치. 레이아웃: ``{data}/marcap-YYYY.parquet``."""
        return self.data

    @property
    def adj_events(self) -> Path:
        return self.data / "prices" / "adj_events.parquet"

    @property
    def fin(self) -> Path:
        return self.data / "한국재무제표"

    @property
    def dart_raw(self) -> Path:
        """DART 일괄 다운로드 txt(+ ``_extracted/``) 보관 위치. 로컬 전용."""
        return self.fin / "재무제표 다운로드"

    @property
    def fin_long(self) -> Path:
        return self.data / "fin" / "long.parquet"

    @property
    def fin_conflicts(self) -> Path:
        return self.data / "fin" / "conflicts.parquet"

    @property
    def fin_quality(self) -> Path:
        """단위 오기입 판정표(보정 전 원래 배율과 판정 근거)."""
        return self.data / "fin" / "quality.parquet"

    @property
    def grid_dir(self) -> Path:
        """백테스트 그리드 결과(다시 만들 수 있으므로 git 제외)."""
        return self.data / "grid"

    @property
    def report_dir(self) -> Path:
        """HTML 리포트 출력 위치. ``KQUANT_REPORT_DIR``이 있으면 그 경로."""
        env = os.environ.get("KQUANT_REPORT_DIR")
        return Path(env) if env else self.data / "reports"

    def marcap_file(self, year: int) -> Path:
        return self.marcap_raw / f"marcap-{year}.parquet"


def load_paths(env_file: Path | None = None) -> Paths:
    load_dotenv(env_file or PROJECT_ROOT / ".env", override=False)
    data = Path(os.environ.get("KQUANT_DATA_DIR") or "dataset")  # .env.example을 그대로 복사한 빈 값도 기본값으로
    if not data.is_absolute():
        data = PROJECT_ROOT / data
    return Paths(data=data.resolve())


def secret(name: str) -> str:
    """비밀값을 환경변수에서 읽는다. 없으면 어떤 변수가 필요한지 알려준다."""
    load_dotenv(PROJECT_ROOT / ".env", override=False)
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"환경변수 {name}가 설정되지 않았습니다 (.env 또는 서버 secret).")
    return value
