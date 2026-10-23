"""Registry of analysis modules (explicit list: adding a module is one import and one line)."""

from app.ml.base import AnalysisModule
from app.ml.modules.anomalies import AnomalyModule
from app.ml.modules.churn import ChurnModule
from app.ml.modules.cohort import CohortRetentionModule
from app.ml.modules.drivers import DriverModule
from app.ml.modules.forecast import ForecastModule
from app.ml.modules.kpis import KpiModule
from app.ml.modules.pareto import ParetoModule
from app.ml.modules.segments import SegmentationModule
from app.ml.modules.trend import TrendSeasonalityModule

_MODULES: list[type[AnalysisModule]] = [
    KpiModule,
    TrendSeasonalityModule,
    ParetoModule,
    CohortRetentionModule,
    ForecastModule,
    AnomalyModule,
    SegmentationModule,
    ChurnModule,
    DriverModule,
]


def all_modules() -> list[AnalysisModule]:
    """Return a fresh instance of every registered module, in execution order."""
    return [cls() for cls in _MODULES]


def register(module: type[AnalysisModule]) -> type[AnalysisModule]:
    """Register an extra module class (used by later steps and by tests)."""
    if module not in _MODULES:
        _MODULES.append(module)
    return module


def unregister(module: type[AnalysisModule]) -> None:
    """Remove a module class from the registry."""
    if module in _MODULES:
        _MODULES.remove(module)
