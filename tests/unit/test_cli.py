"""Helpers for test cli.

Test the click cli.
"""

import json
import logging
from typing import Any, cast
from unittest.mock import AsyncMock, patch

import pytest
from dirty_equals import IsPartialDict, IsStr
from packaging.version import Version
from typer.testing import CliRunner

from porringer.console.command import sync as sync_command
from porringer.console.entry import app
from porringer.core.schema import PluginKind
from porringer.schema import (
    ActionCompletedEvent,
    ActionProgress,
    ActionProgressEvent,
    ActionRef,
    ActionStartedEvent,
    PluginInfo,
    SetupAction,
    SetupActionResult,
    SetupParameters,
)

# A minimal fake result so that ``plugin list`` never spawns subprocesses.
_FAKE_PLUGINS = [
    PluginInfo(name='stub', kind=PluginKind.PACKAGE, version=Version('0.1.0'), installed=True, tool_version=None)
]
EXPECTED_DUPLICATE_ACTIONS = 2


class _FakeConsole:
    """Tiny stand-in for a Rich Console that records printed renderables."""

    def __init__(self) -> None:
        """Initialize captured print calls."""
        self.printed: list[str] = []

    def print(self, *args: Any, **kwargs: Any) -> None:
        """Capture printed text (rendered to its plain string form)."""
        self.printed.append(' '.join(str(a) for a in args))


class _FakeProgress:
    """Tiny stand-in for Rich Progress used by progress tracker tests."""

    def __init__(self) -> None:
        """Initialize captured task state."""
        self.tasks: list[dict[str, Any]] = []
        self.updates: list[tuple[int, dict[str, Any]]] = []
        self.console = _FakeConsole()

    def add_task(self, description: str, *, total: float | int) -> int:
        """Capture added tasks and return a stable integer task id."""
        task_id = len(self.tasks)
        self.tasks.append({'description': description, 'total': total})
        return task_id

    def update(self, task_id: int, **kwargs: Any) -> None:
        """Capture task updates."""
        self.updates.append((task_id, kwargs))


@pytest.fixture(autouse=True)
def _reset_logger():
    """Ensure CLI tests do not leak logger handlers or propagation state."""
    logger = logging.getLogger('porringer')
    old_propagate = logger.propagate
    logger.handlers.clear()
    logger.setLevel(logging.NOTSET)
    logger.propagate = True
    yield
    logger.handlers.clear()
    logger.setLevel(logging.NOTSET)
    logger.propagate = old_propagate


class TestCLI:
    """Tests for the typer CLI."""

    @staticmethod
    def test_version(test_config) -> None:
        """Verifies the version command works."""
        runner = CliRunner()
        result = runner.invoke(app, ['--version'], obj=test_config)

        assert result.exit_code == 0

    @staticmethod
    def test_plugin_list_shows_output(test_config) -> None:
        """``plugin list`` prints discovered plugins by default."""
        runner = CliRunner()
        with patch(
            'porringer.console.command.plugin.PluginCommands.list',
            new=AsyncMock(return_value=_FAKE_PLUGINS),
        ):
            result = runner.invoke(app, ['plugin', 'list'], obj=test_config)

        assert result.exit_code == 0
        assert 'stub' in result.output

    @staticmethod
    def test_quiet_suppresses_informational_output(test_config) -> None:
        """``--quiet`` suppresses informational plugin listing output."""
        runner = CliRunner()
        with patch(
            'porringer.console.command.plugin.PluginCommands.list',
            new=AsyncMock(return_value=_FAKE_PLUGINS),
        ):
            result = runner.invoke(app, ['--quiet', 'plugin', 'list'], obj=test_config)

        assert result.exit_code == 0
        assert 'stub' not in result.output

    @staticmethod
    def test_no_color_flag_is_accepted(test_config) -> None:
        """``--no-color`` runs without error and still produces output."""
        runner = CliRunner()
        with patch(
            'porringer.console.command.plugin.PluginCommands.list',
            new=AsyncMock(return_value=_FAKE_PLUGINS),
        ):
            result = runner.invoke(app, ['--no-color', 'plugin', 'list'], obj=test_config)

        assert result.exit_code == 0
        assert 'stub' in result.output

    @staticmethod
    def test_env_info_json_without_plugins(monkeypatch: pytest.MonkeyPatch, test_config) -> None:
        """``env info --json --no-plugins`` emits diagnostics without plugin discovery."""
        monkeypatch.setenv('PIPX_HOME', str(test_config.local_configuration.cache_directory / 'pipx'))
        runner = CliRunner()

        with patch('porringer.console.command.env.ensure_system_path') as sync_path:
            result = runner.invoke(app, ['env', 'info', '--json', '--no-plugins'], obj=test_config)

        assert result.exit_code == 0
        payload = json.loads(result.output)
        assert payload['system']['porringer_version']
        assert payload['system']['python_executable']
        assert payload['directories']['cache'] == str(test_config.local_configuration.cache_directory)
        assert payload['environment']['PIPX_HOME'] == str(test_config.local_configuration.cache_directory / 'pipx')
        assert payload['plugins'] == []
        sync_path.assert_called_once()
        assert payload == IsPartialDict(
            directories=IsPartialDict(cache=str(test_config.local_configuration.cache_directory)),
            environment=IsPartialDict(PIPX_HOME=str(test_config.local_configuration.cache_directory / 'pipx')),
            path=IsPartialDict(added_entries=[]),
            plugins=[],
            runtime_context={},
            system=IsPartialDict(
                frozen=False,
                porringer_version=IsStr(min_length=1),
                python_executable=IsStr(min_length=1),
                system=IsStr(min_length=1),
            ),
        )
        assert payload['path']['before'] == payload['path']['after']


class TestSyncProgressTracker:
    """Tests for sync CLI progress bookkeeping."""

    @staticmethod
    def test_duplicate_action_labels_use_action_refs() -> None:
        """Actions with the same display label do not collide in progress state."""
        progress = _FakeProgress()
        state = sync_command._ProgressState(total_actions=EXPECTED_DUPLICATE_ACTIONS)
        tracker = sync_command._ProgressTracker(
            progress=cast(Any, progress),
            setup_params=SetupParameters(),
            state=state,
        )
        first_ref = ActionRef.from_indices(0, 0)
        second_ref = ActionRef.from_indices(0, 1)
        first_action = SetupAction(description='same label')
        second_action = SetupAction(description='same label')

        tracker.handle_progress_event(ActionStartedEvent(action=first_action, action_ref=first_ref))
        tracker.handle_progress_event(ActionStartedEvent(action=second_action, action_ref=second_ref))

        assert set(state.active_tasks) == {'0:0', '0:1'}
        assert len(progress.tasks) == EXPECTED_DUPLICATE_ACTIONS

        tracker.handle_progress_event(
            ActionCompletedEvent(
                action=first_action,
                result=SetupActionResult(action=first_action, success=True),
                action_ref=first_ref,
            )
        )
        tracker.handle_progress_event(
            ActionCompletedEvent(
                action=second_action,
                result=SetupActionResult(action=second_action, success=True),
                action_ref=second_ref,
            )
        )

        assert state.active_tasks == {}
        assert state.completed == EXPECTED_DUPLICATE_ACTIONS


class TestSyncProgressOutputTail:
    """Tests for surfacing buffered subprocess output when an action fails."""

    @staticmethod
    def test_failed_action_prints_buffered_output_tail() -> None:
        """Streamed output lines are printed under a failed action."""
        progress = _FakeProgress()
        state = sync_command._ProgressState(total_actions=1)
        tracker = sync_command._ProgressTracker(
            progress=cast(Any, progress),
            setup_params=SetupParameters(),
            state=state,
        )
        ref = ActionRef.from_indices(0, 0)
        action = SetupAction(description='pdm install')

        tracker.handle_progress_event(ActionStartedEvent(action=action, action_ref=ref))
        tracker.handle_progress_event(
            ActionProgressEvent(
                action=action,
                action_ref=ref,
                progress=ActionProgress(
                    action=action, phase='install', output='Lockfile hash mismatch', channel='stderr'
                ),
            )
        )
        tracker.handle_progress_event(
            ActionProgressEvent(
                action=action,
                action_ref=ref,
                progress=ActionProgress(action=action, phase='install', output='run pdm lock', channel='stderr'),
            )
        )
        tracker.handle_progress_event(
            ActionCompletedEvent(
                action=action,
                result=SetupActionResult(action=action, success=False, message='Project install failed (exit 1)'),
                action_ref=ref,
            )
        )

        printed = '\n'.join(progress.console.printed)
        assert 'Lockfile hash mismatch' in printed
        assert 'run pdm lock' in printed
        assert ref.action_id not in state.output_tails

    @staticmethod
    def test_successful_action_does_not_print_output_tail() -> None:
        """Buffered output is discarded (not printed) when the action succeeds."""
        progress = _FakeProgress()
        state = sync_command._ProgressState(total_actions=1)
        tracker = sync_command._ProgressTracker(
            progress=cast(Any, progress),
            setup_params=SetupParameters(),
            state=state,
        )
        ref = ActionRef.from_indices(0, 0)
        action = SetupAction(description='pdm install')

        tracker.handle_progress_event(ActionStartedEvent(action=action, action_ref=ref))
        tracker.handle_progress_event(
            ActionProgressEvent(
                action=action,
                action_ref=ref,
                progress=ActionProgress(
                    action=action, phase='install', output='Resolving dependencies...', channel='stdout'
                ),
            )
        )
        tracker.handle_progress_event(
            ActionCompletedEvent(
                action=action,
                result=SetupActionResult(action=action, success=True),
                action_ref=ref,
            )
        )

        assert progress.console.printed == []
        assert state.output_tails == {}


class TestCLILoggingLevels:
    """Verify --verbose and --debug flags wire to logger.setLevel."""

    @pytest.fixture(autouse=True)
    @staticmethod
    def _mock_plugin_list():
        """Bypass real plugin discovery so the tests run in milliseconds."""
        with patch('porringer.backend.command.plugin.PluginCommands.list', new_callable=AsyncMock) as plugin_list:
            plugin_list.return_value = _FAKE_PLUGINS
            yield

    @staticmethod
    def test_default_level_is_warning() -> None:
        """With no flags the porringer logger is set to WARNING."""
        runner = CliRunner()
        result = runner.invoke(app, ['plugin', 'list'])

        assert result.exit_code == 0

        logger = logging.getLogger('porringer')
        assert logger.level == logging.WARNING

    @staticmethod
    def test_verbose_sets_info() -> None:
        """A single -v sets the logger to INFO."""
        runner = CliRunner()
        result = runner.invoke(app, ['-v', 'plugin', 'list'])

        assert result.exit_code == 0

        logger = logging.getLogger('porringer')
        assert logger.level == logging.INFO

    @staticmethod
    def test_double_verbose_sets_debug() -> None:
        """-vv sets the logger to DEBUG."""
        runner = CliRunner()
        result = runner.invoke(app, ['-vv', 'plugin', 'list'])

        assert result.exit_code == 0

        logger = logging.getLogger('porringer')
        assert logger.level == logging.DEBUG

    @staticmethod
    def test_debug_flag_sets_debug() -> None:
        """--debug sets the logger to DEBUG."""
        runner = CliRunner()
        result = runner.invoke(app, ['--debug', 'plugin', 'list'])

        assert result.exit_code == 0

        logger = logging.getLogger('porringer')
        assert logger.level == logging.DEBUG
