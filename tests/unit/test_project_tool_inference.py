"""Helpers for test project tool inference.

Tests for synthesizing a TOOL install action when the intended project
plugin's own CLI is unavailable (e.g. ``pdm`` not on PATH but a
``pdm.lock`` identifies it as the project's manager).
"""

import tempfile
from pathlib import Path
from typing import override

from packaging.version import Version

from porringer.backend.command.core.action_builder import build_actions
from porringer.backend.command.core.discovery import DiscoveredPlugins
from porringer.core.plugin_schema.environment import BootstrapRequirement, CheckUpdatesParameters, Environment
from porringer.core.plugin_schema.runtime import RuntimeContext
from porringer.core.schema import (
    Distribution,
    Ecosystem,
    Package,
    PackageRef,
    PluginKind,
    PluginParameters,
)
from porringer.schema import SetupManifest
from porringer.test.mock.project_environment import MockProjectEnvironment

_PARAMS = PluginParameters(distribution=Distribution(version=Version('0.0.0')))
_PY = Ecosystem('python')


class _StubEnv(Environment):
    """Minimal Environment subclass for testing (mirrors test_bootstrap_actions.py)."""

    @staticmethod
    def ecosystem() -> Ecosystem | None:
        return _PY

    @staticmethod
    def plugin_kind() -> PluginKind:
        return PluginKind.PACKAGE

    @staticmethod
    def is_supported() -> bool:
        return True

    @classmethod
    def is_available(cls) -> bool:
        return True

    @override
    def install_command(
        self, package: PackageRef, *, include_prereleases: bool = False, runtime_context: RuntimeContext | None = None
    ) -> list[str]:
        return ['stub', 'install', package.name]

    @override
    def uninstall_command(self, package: PackageRef, *, runtime_context: RuntimeContext | None = None) -> list[str]:
        return ['stub', 'uninstall', package.name]

    @override
    def upgrade_command(
        self, package: PackageRef, *, include_prereleases: bool = False, runtime_context: RuntimeContext | None = None
    ) -> list[str]:
        return ['stub', 'upgrade', package.name]

    @override
    async def packages(
        self, *, project_path: Path | None = None, runtime_context: RuntimeContext | None = None
    ) -> list[Package]:
        return []

    @override
    async def check_updates(self, params: CheckUpdatesParameters) -> list[Package]:
        return []


def _make_pip(*, available: bool = True) -> _StubEnv:
    class _Pip(_StubEnv):
        @classmethod
        def is_available(cls) -> bool:
            return available

    return _Pip(_PARAMS)


def _make_pipx(*, available: bool = True) -> _StubEnv:
    class _Pipx(_StubEnv):
        @staticmethod
        def plugin_kind() -> PluginKind:
            return PluginKind.TOOL

        @classmethod
        def is_available(cls) -> bool:
            return available

        @classmethod
        @override
        def bootstrap_requirement(cls) -> BootstrapRequirement | None:
            return BootstrapRequirement(installer='pip', package=PackageRef.model_validate('pipx'))

    return _Pipx(_PARAMS)


def _make_pdm_project(*, available: bool = False) -> MockProjectEnvironment:
    """A 'pdm' project plugin with pdm.lock evidence and configurable availability."""

    class _Pdm(MockProjectEnvironment):
        _project_evidence_files = ('pdm.lock',)

        @classmethod
        @override
        def tool_name(cls) -> str:
            return 'pdm'

        @classmethod
        @override
        def is_available(cls) -> bool:
            return available

    return _Pdm(_PARAMS)


def _write_project(tmp_path: Path) -> None:
    """Write a minimal pyproject.toml + pdm.lock marking pdm as the project manager."""
    (tmp_path / 'pyproject.toml').write_text('[project]\nname = "demo"\n', encoding='utf-8')
    (tmp_path / 'pdm.lock').write_text('', encoding='utf-8')


class TestProjectToolInference:
    """Verify TOOL-install synthesis driven by project evidence."""

    @staticmethod
    def test_synthesizes_pdm_tool_install_when_pdm_unavailable() -> None:
        """No manifest tools entry + pdm.lock evidence + pdm unavailable -> synthesized chain."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            _write_project(tmp_path)

            manifest = SetupManifest()
            plugins = DiscoveredPlugins(
                environments={'pipx': _make_pipx(available=False), 'pip': _make_pip(available=True)},
                project_environments={'pdm': _make_pdm_project(available=False)},
                scm_environments={},
            )

            actions = build_actions(manifest, plugins, search_from=tmp_path)

            tool_actions = [a for a in actions if a.kind == PluginKind.TOOL]
            package_actions = [a for a in actions if a.kind == PluginKind.PACKAGE]
            project_actions = [a for a in actions if a.kind == PluginKind.PROJECT]

            assert len(project_actions) == 1
            assert project_actions[0].installer is None  # pdm not available -> deferred sync

            assert len(tool_actions) == 1
            assert tool_actions[0].package is not None
            assert tool_actions[0].package.name == 'pdm'

            assert len(package_actions) == 1
            assert package_actions[0].package is not None
            assert package_actions[0].package.name == 'pipx'
            assert package_actions[0].installer == 'pip'

    @staticmethod
    def test_no_synthesis_when_pdm_available() -> None:
        """When pdm's own CLI is available, no TOOL synthesis is needed."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            _write_project(tmp_path)

            manifest = SetupManifest()
            plugins = DiscoveredPlugins(
                environments={'pipx': _make_pipx(available=True), 'pip': _make_pip(available=True)},
                project_environments={'pdm': _make_pdm_project(available=True)},
                scm_environments={},
            )

            actions = build_actions(manifest, plugins, search_from=tmp_path)

            assert all(a.kind != PluginKind.TOOL for a in actions)
            project_actions = [a for a in actions if a.kind == PluginKind.PROJECT]
            assert len(project_actions) == 1
            assert project_actions[0].installer == 'pdm'

    @staticmethod
    def test_explicit_tools_entry_overrides_synthesis() -> None:
        """An explicit manifest tools entry for pdm prevents a duplicate synthesized action."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            _write_project(tmp_path)

            manifest = SetupManifest.model_validate({'tools': {'python': ['pdm==2.28']}})
            plugins = DiscoveredPlugins(
                environments={'pipx': _make_pipx(available=False), 'pip': _make_pip(available=True)},
                project_environments={'pdm': _make_pdm_project(available=False)},
                scm_environments={},
            )

            actions = build_actions(manifest, plugins, search_from=tmp_path)

            pdm_tool_actions = [
                a for a in actions if a.kind == PluginKind.TOOL and a.package is not None and a.package.name == 'pdm'
            ]
            assert len(pdm_tool_actions) == 1
            assert pdm_tool_actions[0].package is not None
            assert pdm_tool_actions[0].package.constraint == '==2.28'
