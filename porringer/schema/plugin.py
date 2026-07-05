"""Data models and schemas for plugin.

Plugin metadata schemas.
"""

from dataclasses import dataclass
from enum import Enum, auto

from packaging.version import Version

from porringer.core.schema import PluginKind


class PluginCapability(Enum):
    """Capabilities a plugin may implement via protocol mixins.

    Used by :meth:`DiscoveredPlugins.capabilities` and
    :meth:`DiscoveredPlugins.capabilities` to let callers probe
    what a plugin supports without importing protocol classes.
    """

    RUNTIME_CONSUMER = auto()
    """Plugin consumes a resolved runtime executable (e.g. pip, uv)."""

    MANIFEST_CONTRIBUTOR = auto()
    """Plugin contributes a manifest source file (e.g. pyproject.toml)."""

    RUNTIME_PROVIDER = auto()
    """Plugin provides managed runtime installations (e.g. pim, pyenv)."""


@dataclass(slots=True)
class PluginInfo:
    """Metadata about a discovered plugin.

    Args:
        name: Canonical plugin name (e.g. `"uv"`, `"pip"`).
        kind: The plugin kind (package, tool, project, runtime, scm).
        version: The version of the plugin distribution.
        installed: Whether the underlying tool is available on the system.
        tool_version: The PEP 440 version of the underlying CLI tool, or `None`
            if the tool is unavailable or its version could not be determined.
        host_tool: When this entry represents a sub-plugin managed by
            another tool, the canonical name of that host tool
            (e.g. ``"pdm"``).  ``None`` for top-level plugins.
    """

    name: str
    kind: PluginKind
    version: Version
    installed: bool
    tool_version: Version | None
    host_tool: str | None = None
