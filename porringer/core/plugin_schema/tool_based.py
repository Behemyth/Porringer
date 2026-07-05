"""Core helpers and types for tool based.

Shared base for plugins backed by a command-line tool.

Provides the ``tool_name()`` / ``is_available()`` / ``tool_version()``
triple so that ``Environment``, ``ProjectEnvironment``, and
``ScmEnvironment`` share a single implementation instead of duplicating
the ``shutil.which`` logic.

The three async helper instance methods — ``_run_json_command``,
``_run_text_command``, and ``_run_bool_command`` — use native
``asyncio.create_subprocess_exec`` so that plugin I/O never blocks
the event loop.
"""

import json
import logging
import re
import shutil
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from packaging.version import InvalidVersion, Version

from porringer.core.path import ensure_system_path
from porringer.core.plugin_schema.runtime import RuntimeConsumer, RuntimeContext
from porringer.core.schema import Plugin
from porringer.utility.trace import CommandTrace
from porringer.utility.utility import CommandResult, run_command


def _log_nonzero_exit(logger: logging.Logger, program: str, result: CommandResult, *, check: bool) -> None:
    """Log a non-zero subprocess exit at error or warning level."""
    if check:
        logger.error('%s exited with code %d: %s', program, result.returncode, result.stderr)
    else:
        logger.warning('%s exited with code %d', program, result.returncode)


class ToolBasedPlugin(Plugin):
    """Intermediate base for plugins backed by a CLI tool.

    Subclasses that override `tool_name()` to return a non-`None`
    string get automatic availability detection via `shutil.which`.
    Subclasses with `tool_name() → None` (the default) are always
    considered available.
    """

    @classmethod
    def auxiliary_tools(cls) -> Sequence[str]:
        """Return optional CLI tools that may be invoked as post-action side effects.

        Override to declare tools the plugin calls via ``shutil.which``
        outside its primary ``tool_name()``.  The test framework uses
        this to verify that the plugin tolerates each tool being absent.

        Returns:
            Tool names (empty by default).
        """
        return ()

    @classmethod
    def tool_name(cls) -> str | None:
        """Return the CLI executable name this plugin wraps.

        Override to declare which command-line tool the plugin uses.
        The base `is_available()` implementation uses this value
        with `shutil.which` to test whether the tool is on PATH.

        Returns `None` for plugins that are not backed by a single CLI
        tool (the default).  Those plugins are always considered available.
        """
        return None

    @classmethod
    def is_available(cls) -> bool:
        """Check if the underlying tool is available on the system.

        On the first call, synchronizes the process ``PATH`` with
        the operating system's authoritative state (e.g. the Windows
        registry) so that tools installed after process startup are
        discoverable.  When ``tool_name()`` returns a string,
        ``shutil.which`` is used to verify the executable exists on
        PATH.  When ``tool_name()`` returns ``None``, the plugin is
        always considered available.
        """
        ensure_system_path()

        name = cls.tool_name()
        if name is None:
            return True
        return shutil.which(name) is not None

    def query_availability(self, runtime_context: RuntimeContext | None = None) -> bool:
        """Unified availability check respecting platform, PATH, and runtime context.

        Encapsulates the full decision tree so that every call-site
        (``list_packages``, ``build_plugin_info``, ``BackendResolver``,
        ``_plugins_discovered_event``) shares one implementation:

        1. ``is_supported()`` — reject unsupported platforms immediately.
        2. When *runtime_context* is provided **and** the plugin is a
           ``RuntimeConsumer``, delegate to
           ``is_available_for(runtime_context)`` which can probe the
           *target* interpreter (e.g. ``python -m pip`` via pim).
        3. Otherwise fall back to the PATH-based ``is_available()``.

        Args:
            runtime_context: Resolved runtime paths for this execution
                run.  ``None`` means use PATH-only detection.

        Returns:
            ``True`` if the plugin can operate in the current context.
        """
        try:
            if not type(self).is_supported():
                return False
            if runtime_context is not None and isinstance(self, RuntimeConsumer):
                return self.is_available_for(runtime_context)
            return self.is_available()
        except Exception:
            logging.getLogger(__name__).warning(
                "Availability check failed for plugin '%s'",
                type(self).__name__,
                exc_info=True,
            )
            return False

    def tool_version(self) -> Version | None:
        """Returns the PEP 440 version of the underlying CLI tool.

        The default implementation runs `<tool_name> --version`, extracts the
        first version-like pattern from the combined stdout/stderr output, and
        parses it as a `Version`.

        Returns `None` when `tool_name()` is `None`, the subprocess
        fails, or the output cannot be parsed as a valid PEP 440 version.

        Subclasses may override this method if their tool's version output
        requires special parsing.

        Returns:
            The parsed tool version, or `None`.
        """
        name = type(self).tool_name()
        if name is None:
            return None

        args = [name, '--version']
        trace = CommandTrace.start(args)
        try:
            result = subprocess.run(
                args,
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
            trace.finish(returncode=result.returncode, stdout=result.stdout, stderr=result.stderr)
            output = result.stdout + result.stderr
        except (OSError, subprocess.SubprocessError) as exc:
            trace.finish(returncode=None, error=f'{type(exc).__name__}: {exc}')
            return None

        match = re.search(r'v?\d+\.\d+(?:\.\d+)*', output)
        if match is None:
            return None

        try:
            return Version(match.group(0))
        except InvalidVersion:
            return None

    @staticmethod
    async def _run_raw(
        args: list[str],
        *,
        timeout_seconds: float,
        logger: logging.Logger,
        cwd: Path | None = None,
        error_label: str | None = None,
    ) -> CommandResult | None:
        """Run *args* as a subprocess, returning the result or ``None`` on failure.

        Centralises the launch-failure handling shared by every plugin
        subprocess helper:

        * ``FileNotFoundError`` — the tool is not on PATH.
        * ``OSError`` / ``TimeoutError`` — the tool failed or timed out.

        Args:
            args: Command and arguments.
            timeout_seconds: Subprocess timeout in seconds.
            logger: Logger for failure diagnostics.
            cwd: Working directory for the subprocess.
            error_label: Label used in the failure message (defaults to
                the executable name, ``args[0]``).

        Returns:
            The :class:`CommandResult`, or ``None`` when the command
            could not be executed.
        """
        try:
            return await run_command(args, cwd=cwd, timeout=timeout_seconds)
        except FileNotFoundError:
            logger.warning('%s not found on PATH', args[0])
        except (OSError, TimeoutError) as e:
            logger.error('Failed to run %s: %s', error_label or args[0], e)
        return None

    async def _run_json_command(self, args: list[str], *, check: bool = False) -> Any | None:
        """Run a CLI command and parse its stdout as JSON.

        Uses ``asyncio.create_subprocess_exec`` so the event loop is
        never blocked by subprocess I/O.

        Centralises the common pattern of running a subprocess, reading
        its standard output, and parsing it as JSON while handling the
        three failure modes every plugin must deal with:

        * ``FileNotFoundError`` — the tool is not on PATH.
        * ``OSError`` / ``TimeoutError`` — the tool failed or timed out.
        * ``json.JSONDecodeError`` — the output was not valid JSON.

        When *check* is ``True``, a non-zero exit code is treated as an
        error and ``None`` is returned.

        Args:
            args: Command and arguments (e.g. ``['npm', 'ls', '-g', '--json']``).
            check: Whether to treat a non-zero exit code as an error.

        Returns:
            The parsed JSON value (dict, list, etc.), or ``None`` when
            the command cannot be executed or its output is not valid
            JSON.
        """
        logger = logging.getLogger(f'porringer.{type(self).tool_name()}.json_command')
        result = await self._run_raw(args, timeout_seconds=30, logger=logger)
        if result is None:
            return None
        if result.returncode != 0:
            _log_nonzero_exit(logger, args[0], result, check=check)
            return None
        try:
            return json.loads(result.stdout) if result.stdout.strip() else None
        except json.JSONDecodeError as e:
            logger.warning('Could not parse JSON output from %s: %s', args[0], e)
            return None

    async def _run_text_command(self, args: list[str], *, check: bool = False) -> str | None:
        """Run a CLI command and return its stdout as text.

        Uses ``asyncio.create_subprocess_exec`` so the event loop is
        never blocked by subprocess I/O.

        Centralises the common pattern of running a subprocess and
        returning its standard output while handling failure modes:

        * ``FileNotFoundError`` — the tool is not on PATH.
        * ``OSError`` / ``TimeoutError`` — the tool failed or timed out.

        When *check* is ``True``, a non-zero exit code is treated as
        an error and ``None`` is returned.

        Args:
            args: Command and arguments.
            check: Whether to treat a non-zero exit code as an error.

        Returns:
            The stdout string, or ``None`` on failure.
        """
        logger = logging.getLogger(f'porringer.{type(self).tool_name()}.text_command')
        result = await self._run_raw(args, timeout_seconds=30, logger=logger)
        if result is None:
            return None
        if result.returncode != 0:
            _log_nonzero_exit(logger, args[0], result, check=check)
            return None
        return result.stdout

    async def _run_bool_command(
        self,
        args: list[str],
        *,
        cwd: Path | None = None,
        label: str = 'command',
    ) -> bool:
        """Run a CLI command and return whether it succeeded.

        Uses ``asyncio.create_subprocess_exec`` so the event loop is
        never blocked by subprocess I/O.

        Logs stdout at info level and stderr at error level on failure.
        Returns ``True`` when the process exits with code 0.

        Args:
            args: Command and arguments.
            cwd: Working directory for the subprocess.
            label: Context label used in the logger name and error messages.

        Returns:
            ``True`` if the process exited cleanly, ``False`` otherwise.
        """
        logger = logging.getLogger(f'porringer.{type(self).tool_name()}.{label}')
        result = await self._run_raw(args, timeout_seconds=300, cwd=cwd, logger=logger, error_label=label)
        if result is None:
            return False
        logger.info(result.stdout)
        if result.returncode != 0:
            logger.error(result.stderr)
            return False
        return True


# ---------------------------------------------------------------------------
# Tool interpreter discovery
# ---------------------------------------------------------------------------


def find_tool_python(tool_name: str) -> str | None:
    """Find the Python interpreter inside a CLI tool's own environment.

    Works for tools installed via pipx, uv tool, or pip by inspecting
    the tool's executable:

    1. If the executable lives inside a virtual environment (has a
       ``pyvenv.cfg`` ancestor), the venv's ``python`` is returned.
    2. Otherwise, the executable (or binary launcher) is read and a
       ``#!`` shebang line is extracted to locate the interpreter.

    Returns ``None`` when the interpreter cannot be determined.
    """
    tool_path = shutil.which(tool_name)
    if tool_path is None:
        return None

    tool_exe = Path(tool_path).resolve()

    # Check if the tool itself lives inside a venv
    for parent in tool_exe.parents:
        if (parent / 'pyvenv.cfg').exists():
            python = parent / 'Scripts' / 'python.exe' if sys.platform == 'win32' else parent / 'bin' / 'python'
            if python.is_file():
                return str(python)
            break

    # Parse the executable for an embedded interpreter path (shebang)
    try:
        raw = tool_exe.read_bytes()
    except OSError:
        return None

    text = raw.decode('utf-8', errors='replace')
    match = re.search(r'#!([^\r\n]*[Pp]ython[^\r\n]*)', text)
    if not match:
        return None

    return _resolve_shebang(match.group(1).strip())


def _resolve_shebang(shebang: str) -> str | None:
    """Resolve a shebang string to a Python interpreter path."""
    # Handle "#!/usr/bin/env python3"
    if '/env ' in shebang or '\\env ' in shebang:
        return shutil.which(shebang.rsplit(maxsplit=1)[-1])

    # Direct path — try as-is, then with .exe suffix on Windows
    candidate = Path(shebang)
    if candidate.is_file():
        return str(candidate)
    if sys.platform == 'win32' and not shebang.lower().endswith('.exe'):
        candidate = candidate.with_suffix('.exe')
        if candidate.is_file():
            return str(candidate)

    return None
