"""Helpers for test runtime inference.

Tests for inferring a Python RUNTIME action from an already-local
``pyproject.toml``'s ``requires-python`` field (Phase 4A).
"""

import tempfile
from pathlib import Path
from typing import override

from packaging.version import Version

from porringer.backend.command.core.action_builder import build_actions
from porringer.core.plugin_schema.environment import CheckUpdatesParameters, Environment
from porringer.core.plugin_schema.runtime import RuntimeContext
from porringer.core.schema import Distribution, Ecosystem, Package, PackageRef, PluginKind, PluginParameters
from porringer.schema import SetupManifest

_PARAMS = PluginParameters(distribution=Distribution(version=Version('0.0.0')))
_PY = Ecosystem('python')


class _StubRuntimeEnv(Environment):
    """Minimal RUNTIME-kind Environment subclass for testing."""

    @staticmethod
    def ecosystem() -> Ecosystem | None:
        return _PY

    @staticmethod
    def plugin_kind() -> PluginKind:
        return PluginKind.RUNTIME

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


def _plugins() -> dict[str, Environment]:
    return {'pim': _StubRuntimeEnv(_PARAMS)}


class TestRuntimeInference:
    """Verify requires-python -> RUNTIME action inference."""

    @staticmethod
    def test_infers_runtime_from_local_pyproject() -> None:
        """A local pyproject.toml with requires-python>=3.14 infers a 3.14 runtime action."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            (tmp_path / 'pyproject.toml').write_text(
                '[project]\nname = "demo"\nrequires-python = ">=3.14"\n', encoding='utf-8'
            )

            manifest = SetupManifest()
            actions = build_actions(manifest, _plugins(), search_from=tmp_path)

            runtime_actions = [a for a in actions if a.kind == PluginKind.RUNTIME]
            assert len(runtime_actions) == 1
            assert runtime_actions[0].package is not None
            assert runtime_actions[0].package.name == '3.14'
            assert runtime_actions[0].installer == 'pim'

    @staticmethod
    def test_no_lower_bound_skips_inference() -> None:
        """requires-python with no lower bound (e.g. '<4') infers nothing."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            (tmp_path / 'pyproject.toml').write_text(
                '[project]\nname = "demo"\nrequires-python = "<4"\n', encoding='utf-8'
            )

            manifest = SetupManifest()
            actions = build_actions(manifest, _plugins(), search_from=tmp_path)

            assert all(a.kind != PluginKind.RUNTIME for a in actions)

    @staticmethod
    def test_no_pyproject_skips_inference() -> None:
        """No pyproject.toml anywhere in the ancestor chain infers nothing."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)

            manifest = SetupManifest()
            actions = build_actions(manifest, _plugins(), search_from=tmp_path)

            assert all(a.kind != PluginKind.RUNTIME for a in actions)

    @staticmethod
    def test_explicit_runtimes_entry_overrides_inference() -> None:
        """An explicit runtimes.python entry always wins over inference."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            (tmp_path / 'pyproject.toml').write_text(
                '[project]\nname = "demo"\nrequires-python = ">=3.14"\n', encoding='utf-8'
            )

            manifest = SetupManifest.model_validate({'runtimes': {'python': ['3.14.6']}})
            actions = build_actions(manifest, _plugins(), search_from=tmp_path)

            runtime_actions = [a for a in actions if a.kind == PluginKind.RUNTIME]
            assert len(runtime_actions) == 1
            assert runtime_actions[0].package is not None
            assert runtime_actions[0].package.name == '3.14.6'
