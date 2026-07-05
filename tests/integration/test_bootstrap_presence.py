"""Helpers for test bootstrap presence.

Presence test using the python-bootstrap example manifest.

Verifies that the inspection presence check works correctly for the
`examples/python-bootstrap/porringer.json` manifest.

Actions whose backing installer is available and whose package is
already installed should be skipped.  Actions with deferred
installers (`installer=None` — no provider on PATH) pass through
inspection as unavailable because presence cannot be checked.
"""

import importlib.metadata
from pathlib import Path

import pytest

from porringer.api import API
from porringer.core.schema import PluginKind
from porringer.schema import ActionInspection, InspectionStatus, SetupParameters, SkipReason

# Absolute path to the bootstrap example manifest directory
_BOOTSTRAP_DIR = Path(__file__).resolve().parents[2] / 'examples' / 'python-bootstrap'


@pytest.mark.fresh_plugins
class TestBootstrapPresence:
    """Inspect the python-bootstrap example and verify presence detection."""

    @staticmethod
    @pytest.fixture(scope='class')
    async def inspection_results(session_api: API) -> list[ActionInspection]:
        """Inspect the bootstrap manifest and return all action inspections.

        Class-scoped: the inspection is executed once and shared across
        every test in this class (all tests are read-only).
        """
        setup_params = SetupParameters(paths=_BOOTSTRAP_DIR)
        report = await session_api.sync.inspect(setup_params)

        assert len(report.manifests) == 1
        return list(report.manifests[0].actions)

    @staticmethod
    def test_all_manifest_sections_produce_results(inspection_results: list[ActionInspection]) -> None:
        """Every manifest section yields at least one result.

        PACKAGE is not asserted unconditionally: it only appears when the
        pipx bootstrap (installing pipx via pip) is synthesized, which
        only happens when pipx itself is not yet available on this host.
        """
        kinds = {r.action.kind for r in inspection_results}
        assert PluginKind.RUNTIME.value in kinds, 'No RUNTIME result'
        assert PluginKind.TOOL.value in kinds, 'No TOOL result'
        assert PluginKind.PROJECT.value in kinds, 'No PROJECT result'
        assert PluginKind.SCM.value in kinds, 'No SCM result'

    @staticmethod
    def test_inspection_actions_do_not_fail(inspection_results: list[ActionInspection]) -> None:
        """No action should fail inspection.

        Actions with `installer=None` are reported as unavailable, not
        as execution failures.
        """
        failed = [r for r in inspection_results if r.status == InspectionStatus.FAILED]
        assert not failed, f'{len(failed)} action(s) failed: ' + ', '.join(
            f'{r.action.description}: {r.message}' for r in failed
        )

    @staticmethod
    def test_skipped_actions_have_reason(inspection_results: list[ActionInspection]) -> None:
        """Every skipped action should carry a valid skip reason."""
        skipped = [r for r in inspection_results if r.skipped]
        for r in skipped:
            assert r.skip_reason is not None, f'Skipped action without reason: {r.action.description}'

    @staticmethod
    def test_installable_skip_reason(inspection_results: list[ActionInspection]) -> None:
        """Installable actions detected as present should report a presence skip reason."""
        skipped = [
            r
            for r in inspection_results
            if r.skipped
            and r.action.kind in {PluginKind.RUNTIME.value, PluginKind.PACKAGE.value, PluginKind.TOOL.value}
        ]
        for r in skipped:
            assert r.skip_reason in {SkipReason.ALREADY_INSTALLED.name, SkipReason.UPDATE_AVAILABLE.name}, (
                f'{r.action.description} skipped with unexpected reason: {r.skip_reason}'
            )
            if r.skip_reason == SkipReason.UPDATE_AVAILABLE.name:
                assert r.installed_version is not None
                assert r.available_version is not None

    @staticmethod
    def test_pipx_skipped_when_installed(inspection_results: list[ActionInspection]) -> None:
        """If `pipx` is installed as a pip package, its PACKAGE action should be skipped.

        The presence check uses `pip list` (not PATH), so we guard
        with `importlib.metadata` which matches pip's view.
        """
        try:
            importlib.metadata.distribution('pipx')
        except importlib.metadata.PackageNotFoundError:
            pytest.skip('pipx not installed as a pip package')
        pipx_results = [
            r
            for r in inspection_results
            if r.action.kind == PluginKind.PACKAGE.value and r.action.package_name == 'pipx'
        ]
        assert len(pipx_results) == 1
        assert pipx_results[0].skipped, 'pipx is installed but action was not skipped'

    @staticmethod
    def test_scm_action_present(inspection_results: list[ActionInspection]) -> None:
        """An SCM action should be present in the inspection results."""
        scm_results = [r for r in inspection_results if r.action.kind == PluginKind.SCM.value]
        assert len(scm_results) == 1

    @staticmethod
    def test_project_sync_action_present(inspection_results: list[ActionInspection]) -> None:
        """The implicit project-sync action should be present in the inspection results."""
        project_results = [r for r in inspection_results if r.action.kind == PluginKind.PROJECT.value]
        assert len(project_results) == 1
