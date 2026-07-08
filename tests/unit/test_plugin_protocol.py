"""Protocol-conformance matrix across every discovered plugin.

A single parametrized test that walks the plugin registry and asserts
the contract every plugin (Environment / ProjectEnvironment /
ScmEnvironment) must satisfy.  Catches drift the moment a new or
modified plugin violates a base-class invariant — without exercising
any wrapped CLI tool.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import cache
from importlib import metadata
from pathlib import Path
from typing import Literal, cast

import pytest
from packaging.utils import canonicalize_name

from porringer.backend.command.core.discovery import DiscoveredPlugins, discover_all_plugins
from porringer.core.plugin_schema.environment import Environment
from porringer.core.plugin_schema.project_environment import ProjectEnvironment, ProjectInstaller
from porringer.core.plugin_schema.scm import ScmEnvironment
from porringer.core.plugin_schema.tool_based import ToolBasedPlugin
from porringer.core.schema import PackageRef

_PKG = PackageRef(name='example')
_PluginGroup = Literal['environment', 'project_environment', 'scm']


@dataclass(frozen=True, slots=True)
class _PluginKey:
    """Collection-time key for one registered plugin entry point."""

    group: _PluginGroup
    name: str


@dataclass(frozen=True, slots=True)
class _PluginMatrix:
    """Parametrization keys grouped by plugin protocol."""

    all_plugins: tuple[_PluginKey, ...]
    environments: tuple[_PluginKey, ...]
    project_environments: tuple[_PluginKey, ...]
    scm_environments: tuple[_PluginKey, ...]


def _entry_point_keys(group: _PluginGroup) -> tuple[_PluginKey, ...]:
    """Return sorted plugin keys without loading plugin classes."""
    entry_points = metadata.entry_points(group=f'porringer.{group}')
    names = sorted({str(canonicalize_name(entry_point.name)) for entry_point in entry_points})
    return tuple(_PluginKey(group=group, name=name) for name in names)


def _classify_environment_group() -> tuple[tuple[_PluginKey, ...], tuple[_PluginKey, ...]]:
    """Load and classify every ``porringer.environment`` entry point.

    There is no separate "project" plugin type — ``Environment`` and
    project-only ``ProjectInstaller`` plugins are all registered under
    ``porringer.environment``.  This mirrors the production partition
    in ``porringer.backend.command.core.discovery.scan_environment_group``:
    plugins that install packages (with or without an additional
    project-install capability) are classified as environments;
    project-only plugins (``pdm``, ``poetry``) are classified as
    project environments.

    Returns:
        Tuple of ``(environment_keys, project_environment_keys)``.
    """
    entry_points = metadata.entry_points(group='porringer.environment')
    environments: set[str] = set()
    project_environments: set[str] = set()
    for entry_point in entry_points:
        name = str(canonicalize_name(entry_point.name))
        try:
            loaded = entry_point.load()
        except Exception:
            continue
        if not isinstance(loaded, type):
            continue
        if issubclass(loaded, Environment):
            environments.add(name)
        elif issubclass(loaded, ProjectInstaller):
            project_environments.add(name)
    return (
        tuple(_PluginKey(group='environment', name=name) for name in sorted(environments)),
        tuple(_PluginKey(group='project_environment', name=name) for name in sorted(project_environments)),
    )


@cache
def _plugin_matrix() -> _PluginMatrix:
    """Return cached collection keys for the protocol matrix."""
    environments, project_environments = _classify_environment_group()
    scm_environments = _entry_point_keys('scm')
    all_plugins = tuple(sorted((*environments, *project_environments, *scm_environments), key=lambda key: key.name))
    return _PluginMatrix(
        all_plugins=all_plugins,
        environments=environments,
        project_environments=project_environments,
        scm_environments=scm_environments,
    )


def _ids(keys: tuple[_PluginKey, ...]) -> list[str]:
    """Project stable parametrize IDs from plugin keys."""
    return [key.name for key in keys]


def pytest_generate_tests(metafunc: pytest.Metafunc) -> None:
    """Parametrize protocol fixtures from lightweight entry-point keys."""
    matrix = _plugin_matrix()
    if 'any_plugin' in metafunc.fixturenames:
        metafunc.parametrize('any_plugin', matrix.all_plugins, ids=_ids(matrix.all_plugins), indirect=True)
    if 'environment_plugin' in metafunc.fixturenames:
        metafunc.parametrize(
            'environment_plugin',
            matrix.environments,
            ids=_ids(matrix.environments),
            indirect=True,
        )
    if 'project_environment_plugin' in metafunc.fixturenames:
        metafunc.parametrize(
            'project_environment_plugin',
            matrix.project_environments,
            ids=_ids(matrix.project_environments),
            indirect=True,
        )
    if 'scm_environment_plugin' in metafunc.fixturenames:
        metafunc.parametrize(
            'scm_environment_plugin',
            matrix.scm_environments,
            ids=_ids(matrix.scm_environments),
            indirect=True,
        )


@pytest.fixture(scope='session')
def discovered_plugins() -> DiscoveredPlugins:
    """Discover plugin instances once for protocol tests that actually run."""
    return discover_all_plugins(use_cache=True)


def _plugin_from_key(plugins: DiscoveredPlugins, key: _PluginKey) -> ToolBasedPlugin:
    """Resolve a parametrized plugin key to the discovered plugin instance."""
    match key.group:
        case 'environment':
            plugin = plugins.environments.get(key.name)
        case 'project_environment':
            plugin = plugins.project_environments.get(key.name)
        case 'scm':
            plugin = plugins.scm_environments.get(key.name)
    if plugin is None:
        pytest.fail(f"Plugin entry point '{key.name}' in porringer.{key.group} was not discoverable")
    return cast(ToolBasedPlugin, plugin)


@pytest.fixture
def any_plugin(request: pytest.FixtureRequest, discovered_plugins: DiscoveredPlugins) -> ToolBasedPlugin:
    """Return the plugin instance for a generated all-plugin protocol case."""
    return _plugin_from_key(discovered_plugins, cast(_PluginKey, request.param))


@pytest.fixture
def environment_plugin(request: pytest.FixtureRequest, discovered_plugins: DiscoveredPlugins) -> Environment:
    """Return the environment plugin instance for a generated protocol case."""
    plugin = _plugin_from_key(discovered_plugins, cast(_PluginKey, request.param))
    assert isinstance(plugin, Environment)
    return plugin


@pytest.fixture
def project_environment_plugin(
    request: pytest.FixtureRequest,
    discovered_plugins: DiscoveredPlugins,
) -> ProjectEnvironment:
    """Return the project-environment plugin instance for a generated protocol case."""
    plugin = _plugin_from_key(discovered_plugins, cast(_PluginKey, request.param))
    assert isinstance(plugin, ProjectEnvironment)
    return plugin


@pytest.fixture
def scm_environment_plugin(request: pytest.FixtureRequest, discovered_plugins: DiscoveredPlugins) -> ScmEnvironment:
    """Return the SCM plugin instance for a generated protocol case."""
    plugin = _plugin_from_key(discovered_plugins, cast(_PluginKey, request.param))
    assert isinstance(plugin, ScmEnvironment)
    return plugin


class TestPluginContract:
    """Invariants that every discovered plugin must satisfy."""

    @staticmethod
    def test_tool_name_is_str_or_none(any_plugin: ToolBasedPlugin) -> None:
        """``tool_name()`` returns ``str`` or ``None``."""
        plugin = any_plugin
        tool = type(plugin).tool_name()
        assert tool is None or isinstance(tool, str)
        assert tool is None or tool

    @staticmethod
    def test_ecosystem_when_declared(any_plugin: ToolBasedPlugin) -> None:
        """When ``ecosystem()`` is declared it returns a non-empty string."""
        plugin = any_plugin
        try:
            eco = type(plugin).ecosystem()
        except NotImplementedError:
            return
        assert eco is None or (isinstance(eco, str) and eco)

    @staticmethod
    def test_auxiliary_tools_is_sequence_of_str(any_plugin: ToolBasedPlugin) -> None:
        """``auxiliary_tools()`` returns a sequence of non-empty strings."""
        plugin = any_plugin
        aux = type(plugin).auxiliary_tools()
        assert all(isinstance(t, str) and t for t in aux)


def _assert_argv(argv: object, *, expected_first: str | None) -> None:
    assert isinstance(argv, list), f'expected list[str], got {type(argv).__name__}'
    assert argv, 'expected non-empty argv'
    assert all(isinstance(a, str) for a in argv), f'non-str element in {argv!r}'
    if expected_first is not None:
        # First argv element must reference the wrapped tool — either
        # directly (``npm install ...``) or as an interpreter routing
        # to it (``python -m pip ...``, ``poetry env use``).  We only
        # require *some* argv element to mention the tool name.
        assert any(expected_first in a for a in argv), f'tool name {expected_first!r} not present in argv {argv!r}'


class TestEnvironmentContract:
    """Argv-shape invariants for every ``Environment`` plugin."""

    @staticmethod
    def test_install_command_shape(environment_plugin: Environment) -> None:
        """``install_command()`` returns a non-empty argv list referencing the tool."""
        plugin = environment_plugin
        argv = plugin.install_command(_PKG)
        _assert_argv(argv, expected_first=type(plugin).tool_name())

    @staticmethod
    def test_upgrade_command_shape(environment_plugin: Environment) -> None:
        """``upgrade_command()`` returns a non-empty argv list referencing the tool."""
        plugin = environment_plugin
        argv = plugin.upgrade_command(_PKG)
        _assert_argv(argv, expected_first=type(plugin).tool_name())

    @staticmethod
    def test_uninstall_command_shape(environment_plugin: Environment) -> None:
        """``uninstall_command()`` returns a non-empty argv list referencing the tool."""
        plugin = environment_plugin
        argv = plugin.uninstall_command(_PKG)
        _assert_argv(argv, expected_first=type(plugin).tool_name())


class TestProjectEnvironmentContract:
    """Argv-shape invariants for every ``ProjectEnvironment`` plugin."""

    @staticmethod
    def test_sync_command_shape(project_environment_plugin: ProjectEnvironment, tmp_path: Path) -> None:
        """``command_plan()`` returns a non-empty argv list referencing the tool."""
        plugin = project_environment_plugin
        argv = type(plugin).command_plan(tmp_path).argv
        _assert_argv(argv, expected_first=plugin.tool_name())

    @staticmethod
    def test_consumed_runtime_kind_is_str(project_environment_plugin: ProjectEnvironment) -> None:
        """``consumed_runtime_kind()`` returns a non-empty string."""
        plugin = project_environment_plugin
        kind = plugin.consumed_runtime_kind()
        assert isinstance(kind, str)
        assert kind


class TestScmEnvironmentContract:
    """Required overrides for every ``ScmEnvironment`` plugin."""

    @staticmethod
    def test_required_overrides_present(scm_environment_plugin: ScmEnvironment) -> None:
        """``clone``, ``get_remote_urls``, ``find_repo_root`` are overridden (not abstract)."""
        plugin = scm_environment_plugin
        cls = type(plugin)
        for method_name in ('clone', 'get_remote_urls', 'find_repo_root', 'tool_name'):
            attr = getattr(cls, method_name)
            assert getattr(attr, '__isabstractmethod__', False) is False, (
                f'{cls.__name__}.{method_name} is still abstract'
            )
