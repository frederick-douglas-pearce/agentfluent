"""AgentFluent: Local-first agent analytics with prompt diagnostics."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("agentfluent")
except PackageNotFoundError:
    __version__ = "0.0.0"
