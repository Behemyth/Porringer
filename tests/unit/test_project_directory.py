"""Helpers for test project directory.

Tests for project_directory and SkipReason functionality.
"""

import json
import tempfile
from asyncio import Queue
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from packaging.version import Version

from porringer.api import API
from porringer.backend.command.core import execution
from porringer.backend.command.core.discovery import DiscoveredPlugins
from porringer.core.plugin_schema.project_environment import ProjectCommandPlan
from porringer.core.schema import Distribution, Ecosystem, PluginKind, PluginParameters
from porringer.schema import (
    BatchSetupResults,
    InspectionStatus,
    SetupAction,
    SetupActionResult,
    SetupParameters,
    SetupResults,
    SkipReason,
)
from porringer.test.mock.project_environment import MockProjectEnvironment
from porringer.utility.utility import CommandResult


class _AvailableProjectEnvironment(MockProjectEnvironment):
    """Available project plugin for project-directory tests."""

    @classmethod
    def is_available(cls) -> bool:
        return True


class _MultiStepProjectEnvironment(_AvailableProjectEnvironment):
    """Project plugin that plans multiple command steps."""

    @classmethod
    def command_plan(cls, search_from: Path, *, runtime_context=None) -> ProjectCommandPlan:
        return ProjectCommandPlan(
            directory=search_from,
            argv=['mock-project', 'second'],
            steps=[['mock-project', 'first'], ['mock-project', 'second']],
        )


def _project_plugins() -> DiscoveredPlugins:
    """Return a discovered plugin set with one available project plugin."""
    plugin = _AvailableProjectEnvironment(PluginParameters(distribution=Distribution(version=Version('0.0.0'))))
    return DiscoveredPlugins(
        environments={},
        project_environments={'mock-project': plugin},
        scm_environments={},
    )


@pytest.mark.mock_packages
class TestProjectDirectorySkip:
    """Tests for project_directory=False skipping project-sync actions."""

    @staticmethod
    async def test_false_skips_project_sync_when_present(session_api: API) -> None:
        """Relevant project-sync actions are skipped when project_directory is False."""
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / 'pyproject.toml').write_text('[project]\nname = "demo"\n', encoding='utf-8')
            manifest_path = root / 'porringer.json'
            manifest_data = {
                'version': '1',
                'packages': {'python': ['requests']},
            }
            manifest_path.write_text(json.dumps(manifest_data))

            params = SetupParameters(paths=root, project_directory=False)
            report = await session_api.sync.inspect(params, plugins=_project_plugins())

            project_actions = [
                a for m in report.manifests for a in m.actions if a.action.kind == PluginKind.PROJECT.value
            ]
            assert project_actions
            assert all(action.status == InspectionStatus.SKIPPED for action in project_actions)

    @staticmethod
    async def test_false_keeps_package_actions(session_api: API) -> None:
        """Package actions still execute when project_directory is False."""
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / 'pyproject.toml').write_text('[project]\nname = "demo"\n', encoding='utf-8')
            manifest_path = root / 'porringer.json'
            manifest_data = {
                'version': '1',
                'packages': {'python': ['requests']},
            }
            manifest_path.write_text(json.dumps(manifest_data))

            params = SetupParameters(paths=root, project_directory=False)
            report = await session_api.sync.inspect(params, plugins=_project_plugins())

            package_results = [
                a for mr in report.manifests for a in mr.actions if a.action.kind == PluginKind.PACKAGE.value
            ]
            assert len(package_results) == 1
            project_skips = [r for r in package_results if r.skip_reason == SkipReason.NO_PROJECT_DIRECTORY.name]
            assert len(project_skips) == 0

    @staticmethod
    async def test_project_sync_executes_all_command_plan_steps() -> None:
        """Project sync executes each plugin-planned command in order."""
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / 'pyproject.toml').touch()
            action = SetupAction(
                description='Sync project via mock-project',
                kind=PluginKind.PROJECT,
                ecosystem=Ecosystem('python'),
                installer='mock-project',
            )
            plugin = _MultiStepProjectEnvironment(PluginParameters(distribution=Distribution(version=Version('0.0.0'))))
            event_queue = Queue()

            with patch(
                'porringer.backend.command.core.execution.run_command',
                new_callable=AsyncMock,
                return_value=CommandResult(returncode=0, stdout='', stderr=''),
            ) as mock_run:
                result = await execution._execute_project_install(
                    action,
                    {'mock-project': plugin},
                    root,
                    SetupParameters(),
                    event_queue=event_queue,
                )

            assert result.success is True
            assert [call.args[0] for call in mock_run.await_args_list] == [
                ['mock-project', 'first'],
                ['mock-project', 'second'],
            ]

    @staticmethod
    async def test_project_sync_failure_message_includes_exit_code() -> None:
        """A failed project-install's result message stays short but includes the exit code.

        Detailed subprocess output is streamed live as ActionProgress
        events (surfaced by the CLI under the failed action) rather
        than being stuffed into the result message, which stays a
        clean, single-line summary for JSONL/GUI consumers.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / 'pyproject.toml').touch()
            action = SetupAction(
                description='Sync project via mock-project',
                kind=PluginKind.PROJECT,
                ecosystem=Ecosystem('python'),
                installer='mock-project',
            )
            plugin = _AvailableProjectEnvironment(PluginParameters(distribution=Distribution(version=Version('0.0.0'))))
            event_queue = Queue()

            with patch(
                'porringer.backend.command.core.execution.run_command',
                new_callable=AsyncMock,
                return_value=CommandResult(returncode=1, stdout='', stderr='Lockfile hash mismatch, run pdm lock'),
            ):
                result = await execution._execute_project_install(
                    action,
                    {'mock-project': plugin},
                    root,
                    SetupParameters(),
                    event_queue=event_queue,
                )

            assert result.success is False
            assert result.message is not None
            assert 'mock-project' in result.message
            assert 'exit 1' in result.message
            assert 'Lockfile hash mismatch' not in result.message


class TestBatchSetupResultsSkips:
    """Tests for BatchSetupResults.skips, total_skipped, and total_succeeded."""

    @staticmethod
    def _make_result(
        kind: PluginKind | None,
        skipped: bool = False,
        skip_reason: SkipReason | None = None,
        message: str | None = None,
    ) -> SetupActionResult:
        label = kind.name if kind is not None else 'COMMAND'
        action = SetupAction(
            description=f'Test {label}',
            kind=kind,
        )
        return SetupActionResult(
            action=action,
            success=True,
            skipped=skipped,
            skip_reason=skip_reason,
            message=message,
        )

    def test_skips_returns_only_skipped(self) -> None:
        """The skips property returns only skipped results."""
        r1 = self._make_result(PluginKind.PACKAGE, skipped=False)
        r2 = self._make_result(
            PluginKind.PROJECT,
            skipped=True,
            skip_reason=SkipReason.NO_PROJECT_DIRECTORY,
            message='No project directory provided',
        )
        r3 = self._make_result(None, skipped=False)

        batch = BatchSetupResults(
            manifest_results=[SetupResults(actions=[], results=[r1, r2, r3])],
        )

        assert batch.total_skipped == 1
        assert len(batch.skips) == 1
        assert batch.skips[0].skipped
        assert batch.skips[0].skip_reason == SkipReason.NO_PROJECT_DIRECTORY

    def test_skips_empty_when_none_skipped(self) -> None:
        """The skips property returns empty list when nothing is skipped."""
        r1 = self._make_result(PluginKind.PACKAGE)
        batch = BatchSetupResults(
            manifest_results=[SetupResults(actions=[], results=[r1])],
        )

        assert batch.total_skipped == 0
        assert batch.skips == []

    @staticmethod
    def test_skips_carries_action_metadata() -> None:
        """Each skipped result carries full action metadata."""
        action = SetupAction(
            description='Sync project via uv',
            kind=PluginKind.PROJECT,
            ecosystem=Ecosystem('python'),
            installer='uv',
        )
        result = SetupActionResult(
            action=action,
            success=True,
            skipped=True,
            skip_reason=SkipReason.NO_PROJECT_DIRECTORY,
            message='No project directory provided',
        )
        batch = BatchSetupResults(
            manifest_results=[SetupResults(actions=[], results=[result])],
        )

        skip = batch.skips[0]
        assert skip.action.kind == PluginKind.PROJECT
        assert skip.action.ecosystem == 'python'
        assert skip.action.installer == 'uv'
        assert skip.skip_reason == SkipReason.NO_PROJECT_DIRECTORY

    def test_success_true_when_only_skips(self) -> None:
        """BatchSetupResults.success is True even when actions are skipped."""
        r1 = self._make_result(
            PluginKind.PROJECT,
            skipped=True,
            skip_reason=SkipReason.NO_PROJECT_DIRECTORY,
        )
        batch = BatchSetupResults(
            manifest_results=[SetupResults(actions=[], results=[r1])],
        )

        assert batch.success is True
        assert batch.total_skipped == 1

    def test_total_succeeded_excludes_skipped(self) -> None:
        """total_succeeded does not count skipped results."""
        r1 = self._make_result(PluginKind.PACKAGE, skipped=False)  # succeeded
        r2 = self._make_result(
            PluginKind.PROJECT,
            skipped=True,
            skip_reason=SkipReason.NO_PROJECT_DIRECTORY,
        )

        batch = BatchSetupResults(
            manifest_results=[SetupResults(actions=[], results=[r1, r2])],
        )

        assert batch.total_succeeded == 1
        assert batch.total_skipped == 1
        assert batch.total_failed == 0
