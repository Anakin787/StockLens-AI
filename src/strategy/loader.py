"""Importing user-written strategies named in config.

Strategies live outside the engine - the design gives ``strategy/`` a base
class and leaves ``<my_strategy>.py`` to the user - so they are named in
config as ``module:Class`` and imported here.

Every failure is loud. A strategy that silently fails to load is a strategy
that silently stops trading, and "nothing happened today" looks identical to
"the market gave no signals".
"""

import importlib

from src.strategy.base import Strategy
from src.toss.errors import TossConfigError


def load_strategy(path):
    """Import and instantiate one ``module:Class`` path."""
    if ":" not in path:
        raise TossConfigError(
            f"전략 경로는 'module:Class' 형식이어야 합니다: {path!r} "
            "(예: src.strategy.my_strategy:MyStrategy)"
        )

    module_name, _, class_name = path.partition(":")
    try:
        module = importlib.import_module(module_name.strip())
    except ImportError as exc:
        raise TossConfigError(f"전략 모듈을 import할 수 없습니다: {module_name} ({exc})") from exc

    try:
        factory = getattr(module, class_name.strip())
    except AttributeError:
        raise TossConfigError(
            f"{module_name} 안에 {class_name}이(가) 없습니다."
        ) from None

    if not (isinstance(factory, type) and issubclass(factory, Strategy)):
        raise TossConfigError(
            f"{path}는 Strategy를 상속해야 합니다. 순수 함수 계약(evaluate(ctx) -> "
            "list[Signal], I/O 없음)이 백테스트의 전제입니다."
        )

    return factory()


def load_strategies(trading_config):
    """Import every strategy named in ``trading.strategies``."""
    return [load_strategy(path) for path in trading_config.strategies]
