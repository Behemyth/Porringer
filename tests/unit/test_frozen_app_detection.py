"""Helpers for test frozen app detection.

Tests for python_command() behaviour in frozen (PyInstaller) applications
and isolated application venvs (pipx-installed porringer).

When ``sys.frozen`` is ``True``, ``sys.executable`` points to the
packaged binary (e.g. ``synodic.exe``) rather than a Python
interpreter.  Similarly, when porringer runs from a pipx-managed venv
(marked by ``pipx_metadata.json`` in the venv prefix),
``sys.executable`` is porringer's private interpreter with none of the
user's packages.  ``python_command()`` must detect both cases and fall
back to a Python found on ``PATH`` instead of blindly using
``sys.executable``.
"""

import sys
from pathlib import Path
from unittest.mock import patch

from porringer.core.plugin_schema import python_environment as _python_environment
from porringer.core.plugin_schema.runtime import RuntimeContext
from tests.conftest import FROZEN_EXE, frozen_context
from tests.fixtures.mock_plugins import MOCK_DIST, MOCK_RUNTIME_EXE, MockPythonEnv

_SYSTEM_PYTHON = r'C:\Python314\python.exe'


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestPythonCommandFrozenApp:
    """python_command() must handle sys.frozen gracefully."""

    @staticmethod
    def test_frozen_no_runtime_uses_which() -> None:
        """In a frozen app with no runtime override, prefer shutil.which('python')."""
        env = MockPythonEnv(MOCK_DIST)
        empty_ctx = RuntimeContext()

        with frozen_context(which_result=_SYSTEM_PYTHON):
            result = env.python_command(empty_ctx)

        assert result == _SYSTEM_PYTHON

    @staticmethod
    def test_frozen_with_runtime_context_uses_override() -> None:
        """Runtime context override takes priority even in frozen apps."""
        env = MockPythonEnv(MOCK_DIST)
        rc = RuntimeContext(executables={'python': MOCK_RUNTIME_EXE})

        with frozen_context():
            result = env.python_command(rc)

        assert result == str(MOCK_RUNTIME_EXE)

    @staticmethod
    def test_frozen_no_which_falls_back_to_sys_executable() -> None:
        """When shutil.which also fails, gracefully degrade to sys.executable."""
        env = MockPythonEnv(MOCK_DIST)
        empty_ctx = RuntimeContext()

        with (
            patch.object(sys, 'frozen', True, create=True),
            patch.object(sys, 'executable', FROZEN_EXE),
            patch('porringer.core.plugin_schema.python_environment.shutil.which', return_value=None),
        ):
            result = env.python_command(empty_ctx)

        assert result == FROZEN_EXE

    @staticmethod
    def test_not_frozen_ignores_which() -> None:
        """When not frozen, python_command() returns sys.executable as before."""
        env = MockPythonEnv(MOCK_DIST)
        empty_ctx = RuntimeContext()

        original_exe = sys.executable
        # Ensure sys.frozen is absent (the normal case)
        with (
            patch.object(sys, 'frozen', False, create=True),
            patch('porringer.core.plugin_schema.python_environment.shutil.which', return_value=_SYSTEM_PYTHON),
        ):
            result = env.python_command(empty_ctx)

        assert result == original_exe

    @staticmethod
    def test_frozen_none_runtime_context_uses_which() -> None:
        """With runtime_context=None (not just empty), still applies frozen fallback."""
        env = MockPythonEnv(MOCK_DIST)

        with frozen_context(which_result=_SYSTEM_PYTHON):
            result = env.python_command(None)

        assert result == _SYSTEM_PYTHON


class TestIsolatedAppVenv:
    """pipx-isolated venv detection and interpreter fallback.

    Regression tests for presence detection probing porringer's own
    isolated pipx venv (which has none of the user's packages) instead
    of the user's default interpreter.
    """

    @staticmethod
    def test_pipx_marker_detected(tmp_path: Path) -> None:
        """A pipx_metadata.json in sys.prefix marks the venv as isolated."""
        (tmp_path / 'pipx_metadata.json').write_text('{}', encoding='utf-8')

        with patch.object(sys, 'prefix', str(tmp_path)):
            assert _python_environment._running_in_isolated_app_venv() is True

    @staticmethod
    def test_plain_venv_not_detected(tmp_path: Path) -> None:
        """A venv without the pipx marker is not isolated."""
        with patch.object(sys, 'prefix', str(tmp_path)):
            assert _python_environment._running_in_isolated_app_venv() is False

    @staticmethod
    def test_isolated_venv_prefers_path_python(tmp_path: Path) -> None:
        """In a pipx-isolated venv, _default_python() returns the PATH python."""
        (tmp_path / 'pipx_metadata.json').write_text('{}', encoding='utf-8')

        with (
            patch.object(sys, 'prefix', str(tmp_path)),
            patch(
                'porringer.core.plugin_schema.python_environment.shutil.which',
                return_value=_SYSTEM_PYTHON,
            ),
        ):
            assert _python_environment._default_python() == _SYSTEM_PYTHON

    @staticmethod
    def test_isolated_venv_no_path_python_falls_back(tmp_path: Path) -> None:
        """When no python is on PATH, degrade to sys.executable."""
        (tmp_path / 'pipx_metadata.json').write_text('{}', encoding='utf-8')

        with (
            patch.object(sys, 'prefix', str(tmp_path)),
            patch('porringer.core.plugin_schema.python_environment.shutil.which', return_value=None),
        ):
            assert _python_environment._default_python() == sys.executable

    @staticmethod
    def test_normal_environment_keeps_sys_executable(tmp_path: Path) -> None:
        """Without isolation markers, _default_python() is sys.executable."""
        with (
            patch.object(sys, 'prefix', str(tmp_path)),
            patch(
                'porringer.core.plugin_schema.python_environment.shutil.which',
                return_value=_SYSTEM_PYTHON,
            ),
        ):
            assert _python_environment._default_python() == sys.executable

    @staticmethod
    def test_python_command_uses_path_python_in_isolated_venv(tmp_path: Path) -> None:
        """python_command() routes through the isolated-venv fallback."""
        env = MockPythonEnv(MOCK_DIST)
        (tmp_path / 'pipx_metadata.json').write_text('{}', encoding='utf-8')

        with (
            patch.object(sys, 'prefix', str(tmp_path)),
            patch(
                'porringer.core.plugin_schema.python_environment.shutil.which',
                return_value=_SYSTEM_PYTHON,
            ),
        ):
            result = env.python_command(RuntimeContext())

        assert result == _SYSTEM_PYTHON

    @staticmethod
    def test_runtime_context_overrides_isolation_fallback(tmp_path: Path) -> None:
        """A resolved runtime context wins over the isolated-venv fallback."""
        env = MockPythonEnv(MOCK_DIST)
        (tmp_path / 'pipx_metadata.json').write_text('{}', encoding='utf-8')
        rc = RuntimeContext(executables={'python': MOCK_RUNTIME_EXE})

        with patch.object(sys, 'prefix', str(tmp_path)):
            result = env.python_command(rc)

        assert result == str(MOCK_RUNTIME_EXE)
