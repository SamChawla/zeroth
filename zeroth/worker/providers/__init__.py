from zeroth.config import settings
from zeroth.worker.providers.base import DeployResult, ZeropsProvider
from zeroth.worker.providers.simulated import SimulatedProvider
from zeroth.worker.providers.zcli import ZcliProvider

__all__ = ["DeployResult", "ZeropsProvider", "get_provider"]


def get_provider(token: str | None = None) -> ZeropsProvider:
    """Pick the transport for one run.

    A token means the run targets the user's own Zerops account, so it forces
    the real provider regardless of the configured default - falling back to
    the simulator there would report a deployment that never happened.
    """
    if token:
        return ZcliProvider(token=token)
    if settings.pathfinder_provider == "zcli" and settings.zcli_token:
        return ZcliProvider()
    return SimulatedProvider()
