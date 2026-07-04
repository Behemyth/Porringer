"""Helpers for test sync inspection.

Tests for structured sync inspection reports.
"""

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from typer.testing import CliRunner

from porringer.api import API
from porringer.console.command import sync as sync_command
from porringer.console.entry import app
from porringer.core.plugin_schema.runtime import RuntimeContext
from porringer.schema import (
    SCHEMA_VERSION,
    BatchSetupResults,
    InspectionMode,
    InspectionStatus,
    SetupAction,
    SetupActionResult,
    SetupParameters,
    SetupResults,
    progress_event_snapshot,
)
from porringer.schema.progress import ActionCompletedEvent


def _write_manifest(path: Path, data: dict) -> Path:
    """Write a manifest into *path* and return the file path."""
    manifest = path / 'porringer.json'
    manifest.write_text(json.dumps(data), encoding='utf-8')
    return manifest


@pytest.mark.mock_packages
class TestSyncInspection:
    """API-level inspection report tests."""

    @staticmethod
    async def test_inspect_reports_actions_and_summary(session_api: API, tmp_path: Path) -> None:
        """Inspection returns stable action records without executing actions."""
        _write_manifest(tmp_path, {'version': '1', 'packages': {'python': ['requests']}})

        report = await session_api.sync.inspect(SetupParameters(paths=tmp_path))

        assert len(report.manifests) == 1
        assert report.schema_version == SCHEMA_VERSION
        assert report.summary.actions == 1
        first_action = report.manifests[0].actions[0]
        assert first_action.index == 0
        assert first_action.ref.manifest_index == 0
        assert first_action.ref.action_index == 0
        assert first_action.action_id == '0:0'
        assert first_action.action.action_id == '0:0'
        assert first_action.action.description
        payload = json.loads(report.model_dump_json())
        assert payload['schema_version'] == SCHEMA_VERSION
        assert payload['summary']['actions'] == 1
        assert payload['manifests'][0]['actions'][0]['ref']['action_id'] == '0:0'

    @staticmethod
    async def test_unknown_ecosystem_is_unavailable(session_api: API, tmp_path: Path) -> None:
        """Unresolved manifest ecosystems are reported as unavailable actions."""
        _write_manifest(
            tmp_path,
            {
                'version': '1',
                'packages': {'not-a-real-ecosystem': ['demo']},
            },
        )

        report = await session_api.sync.inspect(SetupParameters(paths=tmp_path))

        assert len(report.manifests) == 1
        assert report.manifests[0].actions[0].status == InspectionStatus.UNAVAILABLE
        assert report.summary.unavailable == 1
        assert report.status.value == 'failed'
        assert 'action.installer_unavailable' in {diagnostic.code for diagnostic in report.diagnostics}
        assert report.success is False

    @staticmethod
    async def test_failed_manifest_is_reported(session_api: API, tmp_path: Path) -> None:
        """Manifest load failures are captured as failed paths."""
        missing = tmp_path / 'missing'

        report = await session_api.sync.inspect(SetupParameters(paths=missing))

        assert len(report.failed_paths) == 1
        assert report.failed_paths[0].path == missing
        assert report.failed_paths[0].manifest_index == 0
        assert report.summary.failed_paths == 1
        assert report.success is False

    @staticmethod
    async def test_failed_manifest_progress_snapshot_preserves_manifest_index(session_api: API, tmp_path: Path) -> None:
        """JSONL snapshots preserve the original input index for failed manifest paths."""
        missing = tmp_path / 'missing'
        project = tmp_path / 'project'
        project.mkdir()
        _write_manifest(project, {'version': '1'})
        snapshots: list[dict] = []

        await session_api.sync.run(
            SetupParameters(paths=[missing, project], fail_fast=False),
            on_event=lambda event: snapshots.append(progress_event_snapshot(event).model_dump(mode='json')),
        )

        failed = [snapshot for snapshot in snapshots if snapshot['event_type'] == 'manifest_failed']
        loaded = [snapshot for snapshot in snapshots if snapshot['event_type'] == 'manifest_loaded']

        assert failed[0]['failed_path']['manifest_index'] == 0
        assert loaded[0]['manifest']['manifest_index'] == 1

    @staticmethod
    async def test_fast_inspect_skips_presence_resolution(session_api: API, tmp_path: Path) -> None:
        """Fast inspection reports action shape without calling action presence checks."""
        _write_manifest(tmp_path, {'version': '1', 'packages': {'python': ['requests']}})
        params = SetupParameters(paths=tmp_path, inspection_mode=InspectionMode.FAST)

        with (
            patch(
                'porringer.backend.command.core.inspection.inspect_action',
                side_effect=AssertionError('fast inspect should not call inspect_action'),
            ),
            patch(
                'porringer.backend.command.sync.Builder.resolve_runtime_context',
                new_callable=AsyncMock,
            ) as mock_resolve,
        ):
            report = await session_api.sync.inspect(params)

        mock_resolve.assert_not_awaited()
        assert report.inspection_mode == InspectionMode.FAST
        assert report.summary.actions == 1
        # FAST never probes presence, so a would-be NEEDED action reports
        # UNKNOWN instead. NEEDED must only mean 'checked and confirmed absent'.
        assert report.manifests[0].actions[0].status == InspectionStatus.UNKNOWN
        assert report.manifests[0].actions[0].installed_version is None

    @staticmethod
    async def test_complete_inspect_resolves_runtime_for_consumers(session_api: API, tmp_path: Path) -> None:
        """Complete inspection resolves runtime context only when action plugins need it."""
        _write_manifest(tmp_path, {'version': '1', 'packages': {'python': ['requests']}})
        plugins = await API.discover_plugins(resolve_runtime=False)
        context = RuntimeContext(executables={'python': Path('/python')})

        with patch(
            'porringer.backend.command.sync.Builder.resolve_runtime_context',
            new_callable=AsyncMock,
            return_value=context,
        ) as mock_resolve:
            report = await session_api.sync.inspect(SetupParameters(paths=tmp_path), plugins=plugins)

        mock_resolve.assert_awaited_once_with(plugins.environments)
        assert plugins.runtime_context is context
        assert report.summary.actions == 1


@pytest.mark.mock_packages
def test_preview_cli_json(tmp_path: Path, test_config) -> None:
    """``porringer preview --json`` emits a JSON inspection report."""
    _write_manifest(tmp_path, {'version': '1', 'packages': {'python': ['requests']}})
    runner = CliRunner()

    result = runner.invoke(app, ['preview', str(tmp_path), '--json'], obj=test_config)

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload['schema_version'] == SCHEMA_VERSION
    assert payload['inspection_mode'] == InspectionMode.COMPLETE.value
    assert payload['summary']['actions'] == 1
    assert payload['manifests'][0]['actions'][0]['action_id'] == '0:0'
    assert payload['manifests'][0]['actions'][0]['action']['package_name'] == 'requests'


@pytest.mark.mock_packages
def test_preview_cli_fast_json(tmp_path: Path, test_config) -> None:
    """``porringer preview --mode fast --json`` emits a fast inspection report."""
    _write_manifest(tmp_path, {'version': '1', 'packages': {'python': ['requests']}})
    runner = CliRunner()

    result = runner.invoke(app, ['preview', str(tmp_path), '--mode', 'fast', '--json'], obj=test_config)

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload['inspection_mode'] == InspectionMode.FAST.value
    assert payload['summary']['actions'] == 1
    assert payload['manifests'][0]['actions'][0]['status'] == InspectionStatus.UNKNOWN.value


@pytest.mark.mock_packages
def test_preview_cli_envelope(tmp_path: Path, test_config) -> None:
    """``porringer preview --envelope`` emits the common result envelope."""
    _write_manifest(tmp_path, {'version': '1', 'packages': {'python': ['requests']}})
    runner = CliRunner()

    result = runner.invoke(app, ['preview', str(tmp_path), '--mode', 'fast', '--envelope'], obj=test_config)

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload['event_type'] == 'result'
    assert payload['operation'] == 'sync.inspect'
    assert payload['summary']['total'] == 1
    # FAST reports UNKNOWN (not NEEDED), so it must not suggest a 'Run action'
    # follow-up for an action whose presence was never actually checked.
    assert payload['follow_up_actions'] == []


@pytest.mark.mock_packages
def test_preview_cli_explain(tmp_path: Path, test_config) -> None:
    """``porringer preview --explain`` renders diagnostics for a fast inspection.

    FAST reports UNKNOWN (not NEEDED), so it produces no 'Run action'
    follow-up. The explain output is just the summary line.
    """
    _write_manifest(tmp_path, {'version': '1', 'packages': {'python': ['requests']}})
    runner = CliRunner()

    result = runner.invoke(app, ['preview', str(tmp_path), '--mode', 'fast', '--explain'], obj=test_config)

    assert result.exit_code == 0
    assert '1 action(s)' in result.output
    assert 'Follow-up actions:' not in result.output


@pytest.mark.mock_packages
def test_install_cli_jsonl_and_record(tmp_path: Path, test_config) -> None:
    """``porringer install --jsonl --record`` emits events and writes a replay record."""
    _write_manifest(tmp_path, {'version': '1', 'packages': {'python': ['requests']}})
    record_path = tmp_path / 'run-record.json'
    runner = CliRunner()

    action = SetupAction(description='mock action')
    batch_results = BatchSetupResults(
        manifest_results=[
            SetupResults(
                actions=[action],
                results=[SetupActionResult(action=action, success=True, message='ok')],
            )
        ]
    )

    async def _fake_run(parameters, *, on_event=None):
        if on_event is not None:
            on_event(
                ActionCompletedEvent(action=action, result=SetupActionResult(action=action, success=True, message='ok'))
            )
        return SimpleNamespace(results=batch_results)

    fake_api = SimpleNamespace(sync=SimpleNamespace(run=_fake_run))

    with patch('porringer.console.command.install.create_api', return_value=fake_api):
        result = runner.invoke(
            app,
            ['install', str(tmp_path), '--jsonl', '--record', str(record_path), '--yes'],
            obj=test_config,
        )
    assert result.exit_code == 0
    lines = [json.loads(line) for line in result.output.splitlines() if line.strip()]
    assert lines[-1]['event_type'] == 'result'
    assert lines[-1]['operation'] == 'sync.run'
    assert any(line.get('event_type') == 'action_completed' for line in lines)

    record = json.loads(record_path.read_text(encoding='utf-8'))
    assert record['operation'] == 'sync.run'
    assert record['correlation_id'] == lines[-1]['correlation_id']
    assert record['events']


def test_install_record_write_keeps_existing_file_when_replace_fails(tmp_path: Path) -> None:
    """Replay record writes clean up temp files and leave existing records intact on replace failure."""
    record_path = tmp_path / 'run-record.json'
    record_path.write_text('existing', encoding='utf-8')

    with (
        patch.object(Path, 'replace', side_effect=OSError('replace failed')),
        pytest.raises(OSError, match='replace failed'),
    ):
        sync_command._write_replay_record(record_path, {'operation': 'sync.run'})

    assert record_path.read_text(encoding='utf-8') == 'existing'
    assert not tuple(tmp_path.glob(f'.{record_path.name}.*.tmp'))
