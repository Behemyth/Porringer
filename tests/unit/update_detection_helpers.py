"""Helpers for update detection helpers.

Shared factories for update-detection tests.
"""

from unittest.mock import AsyncMock, MagicMock

from porringer.core.plugin_schema.environment import Environment
from porringer.core.schema import Ecosystem, Package, PackageRef, PluginKind
from porringer.schema import SetupAction


def make_action(
    name: str = 'ruff',
    constraint: str | None = None,
    installer: str = 'pip',
    kind: PluginKind = PluginKind.TOOL,
) -> SetupAction:
    """Create a package ``SetupAction`` for tests."""
    pkg = PackageRef.model_validate(name if constraint is None else f'{name}{constraint}')
    return SetupAction(
        description=f"Install '{pkg}' via {installer}",
        kind=kind,
        ecosystem=Ecosystem('python'),
        installer=installer,
        package=pkg,
    )


def make_env(
    installed: list[Package] | None = None,
    updates: list[Package] | None = None,
) -> MagicMock:
    """Create a mock ``Environment``."""
    env = MagicMock(spec=Environment)
    env.packages = AsyncMock(return_value=installed or [])
    env.check_updates = AsyncMock(return_value=updates or [])
    type(env).package_name_validator = MagicMock(return_value='pep440')
    env.tool_name.return_value = 'pip'
    return env
