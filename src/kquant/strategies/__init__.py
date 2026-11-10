"""서비스 전략 (spec §7): value, salesmt, momentum. 레지스트리와 조합은 ``base``에 있다."""

from kquant.strategies.base import REGISTRY, combine, rank_strategies

__all__ = ["REGISTRY", "combine", "rank_strategies"]
