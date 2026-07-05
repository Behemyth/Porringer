"""Helpers for test scm execution.

Tests for `_execute_scm_clone` threading a progress callback through
`CloneParameters` so streamed subprocess output reaches the CLI via
`ActionProgressEvent`, matching the package/project-install pattern.
"""

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock

from porringer.backend.command.core import execution
from porringer.core.plugin_schema.scm import CloneParameters
from porringer.core.schema import Ecosystem, PackageRef, PluginKind
from porringer.plugin.git.plugin import GitScm
from porringer.schema import ActionProgress, ActionProgressEvent, SetupAction, SetupParameters
from porringer.schema.execution import CloneStatus, CloneStatusKind


class TestExecuteScmCloneProgressThreading:
    """Verify the engine threads a working progress callback into CloneParameters."""

    @staticmethod
    async def test_clone_receives_progress_callback_that_emits_events() -> None:
        """clone() is called with a progress_callback that enqueues ActionProgressEvent."""
        action = SetupAction(
            description="Clone 'https://github.com/org/repo'",
            kind=PluginKind.SCM,
            ecosystem=Ecosystem('git'),
            installer='git',
            package=PackageRef.model_validate('https://github.com/org/repo'),
        )
        event_queue: asyncio.Queue = asyncio.Queue()

        scm = AsyncMock(spec=GitScm)
        scm.is_cloned = AsyncMock(return_value=CloneStatus(kind=CloneStatusKind.MISSING))
        scm.clone = AsyncMock(return_value=True)

        result = await execution._execute_scm_clone(
            action,
            {'git': scm},
            Path('/tmp/repo'),
            SetupParameters(),
            event_queue=event_queue,
        )

        assert result.success is True
        scm.clone.assert_awaited_once()
        params = scm.clone.await_args.args[0]
        assert isinstance(params, CloneParameters)
        assert params.url == 'https://github.com/org/repo'
        assert params.progress_callback is not None

        # Invoking the threaded callback (as the plugin would while streaming
        # subprocess output) must enqueue a matching ActionProgressEvent.
        params.progress_callback(
            ActionProgress(
                action=action,
                phase='cloning',
                output='Receiving objects: 50%',
                channel='stderr',
            )
        )
        event = event_queue.get_nowait()
        assert isinstance(event, ActionProgressEvent)
        assert event.action is action
        assert event.progress.output == 'Receiving objects: 50%'

    @staticmethod
    async def test_clone_failure_result_message() -> None:
        """A failed clone() call produces a failure result without raising."""
        action = SetupAction(
            description="Clone 'https://github.com/org/repo'",
            kind=PluginKind.SCM,
            ecosystem=Ecosystem('git'),
            installer='git',
            package=PackageRef.model_validate('https://github.com/org/repo'),
        )
        event_queue: asyncio.Queue = asyncio.Queue()

        scm = AsyncMock(spec=GitScm)
        scm.is_cloned = AsyncMock(return_value=CloneStatus(kind=CloneStatusKind.MISSING))
        scm.clone = AsyncMock(return_value=False)

        result = await execution._execute_scm_clone(
            action,
            {'git': scm},
            Path('/tmp/repo'),
            SetupParameters(),
            event_queue=event_queue,
        )

        assert result.success is False
        assert 'git' in result.message
