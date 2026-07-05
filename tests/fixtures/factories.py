"""Helpers for factories.

Canonical factories for constructing test doubles and schema objects.

These helpers centralise the boilerplate that was previously copy-pasted
across the unit suite: ``MagicMock(spec=Environment)`` wiring, ``SetupAction``
literals, ``RuntimeContext`` construction, and ``CommandResult`` stubs.

Each factory exposes sensible defaults so a test overrides only the field it
actually exercises, keeping the test body focused on the behaviour under test.
"""

from collections.abc import Callable
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

from packaging.version import Version

from porringer.core.plugin_schema.environment import Environment
from porringer.core.plugin_schema.runtime import RuntimeContext
from porringer.core.schema import Distribution, Ecosystem, Package, PackageRef, PluginKind
from porringer.schema import SetupAction
from porringer.utility.utility import CommandResult


def make_environment(
    *,
    kind: PluginKind = PluginKind.PACKAGE,
    ecosystem: str = 'python',
    tool_name: str = 'pip',
    available: bool | Callable[..., bool] = True,
    installed: list[Package] | None = None,
    updates: list[Package] | None = None,
    version: str = '1.0.0',
    validator: str | None = 'pep440',
) -> MagicMock:
    """Build a canonical ``Environment`` mock.

    Args:
        kind: Value returned by ``plugin_kind()``.
        ecosystem: Ecosystem name returned by ``ecosystem()``.
        tool_name: Value returned by ``tool_name()``.
        available: Either a bool returned by ``query_availability()`` or a
            callable used as its ``side_effect`` (e.g. to react to a runtime
            context argument).
        installed: Packages returned by the async ``packages()`` call.
        updates: Packages returned by the async ``check_updates()`` call.
        version: Distribution version reported by ``distribution()``.
        validator: Value returned by ``package_name_validator()``.

    Returns:
        A ``MagicMock`` specced against ``Environment`` with the requested
        behaviour wired up.
    """
    env = MagicMock(spec=Environment)
    env.packages = AsyncMock(return_value=installed or [])
    env.check_updates = AsyncMock(return_value=updates or [])
    env.tool_name.return_value = tool_name
    type(env).plugin_kind = MagicMock(return_value=kind)
    type(env).ecosystem = MagicMock(return_value=Ecosystem(ecosystem))
    type(env).package_name_validator = MagicMock(return_value=validator)
    type(env).distribution = MagicMock(return_value=Distribution(version=Version(version)))
    if callable(available):
        env.query_availability = MagicMock(side_effect=available)
    else:
        env.query_availability = MagicMock(return_value=available)
    return env


def setup_action(
    name: str = 'cppython',
    *,
    kind: PluginKind = PluginKind.TOOL,
    ecosystem: str = 'python',
    installer: str = 'pipx',
    constraint: str | None = None,
    include_prereleases: bool = False,
    runtime_tag: str | None = None,
    description: str | None = None,
) -> SetupAction:
    """Build a ``SetupAction``.

    Args:
        name: Package name (combined with ``constraint`` when provided).
        kind: The plugin kind discriminator.
        ecosystem: Ecosystem identifier.
        installer: The installer plugin name.
        constraint: Optional version constraint appended to ``name``.
        include_prereleases: Per-package pre-release opt-in.
        runtime_tag: Optional runtime tag.
        description: Override the generated human-readable description.

    Returns:
        A constructed ``SetupAction``.
    """
    pkg = PackageRef.model_validate(name if constraint is None else f'{name}{constraint}')
    if description is None:
        description = f"Install '{pkg}' via {installer}"
    return SetupAction(
        description=description,
        kind=kind,
        ecosystem=Ecosystem(ecosystem),
        installer=installer,
        package=pkg,
        include_prereleases=include_prereleases,
        runtime_tag=runtime_tag,
    )


def runtime_context(**executables: str | Path) -> RuntimeContext:
    """Build a ``RuntimeContext`` from keyword ``name=path`` pairs.

    Args:
        **executables: Mapping of runtime kind to executable path. String
            paths are converted to ``Path`` automatically.

    Returns:
        A ``RuntimeContext`` with the given executables.
    """
    return RuntimeContext(executables={name: Path(path) for name, path in executables.items()})


def command_ok(stdout: str = '', stderr: str = '') -> CommandResult:
    """Build a successful ``CommandResult`` (return code 0)."""
    return CommandResult(returncode=0, stdout=stdout, stderr=stderr)


def command_fail(stderr: str = 'error', *, stdout: str = '', code: int = 1) -> CommandResult:
    """Build a failed ``CommandResult`` (non-zero return code)."""
    return CommandResult(returncode=code, stdout=stdout, stderr=stderr)
