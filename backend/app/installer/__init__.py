"""Installer public API, loaded lazily to avoid the engine-adapter dependency cycle."""

from .catalog import INSTALLER_CATALOG
from .models import InstallJob, InstallRequest, ModelInstallRequest, ToolRepairRequest

__all__ = ["INSTALLER_CATALOG", "InstallerManager", "InstallJob", "InstallRequest",
           "ModelInstallRequest", "ToolRepairRequest", "CommandResult", "CommandRunner"]


def __getattr__(name):
    if name == "InstallerManager":
        from .manager import InstallerManager
        return InstallerManager
    if name in {"CommandResult", "CommandRunner"}:
        from .runner import CommandResult, CommandRunner
        return {"CommandResult": CommandResult, "CommandRunner": CommandRunner}[name]
    raise AttributeError(name)
