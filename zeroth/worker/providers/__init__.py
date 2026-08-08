from zeroth.config import settings
from zeroth.worker.providers.base import DeployResult, ZeropsProvider
from zeroth.worker.providers.simulated import SimulatedProvider
from zeroth.worker.providers.zcli import ZcliProvider

__all__ = ["DeployResult", "ZeropsProvider", "get_provider"]


def get_provider() -> ZeropsProvider:
    if settings.zerops_provider == "zcli" and settings.zerops_token:
        return ZcliProvider()
    return SimulatedProvider()
