"""Plugin integration for plugin.

Git SCM plugin implementation.
"""

import asyncio
import logging
from pathlib import Path
from typing import override

from porringer.core.plugin_schema.scm import CloneParameters, ScmEnvironment
from porringer.core.schema import Ecosystem
from porringer.utility.utility import CommandProgress, run_command

logger = logging.getLogger(__name__)


class GitScm(ScmEnvironment):
    """SCM environment plugin for Git.

    Provides clone and presence-check operations using the `git`
    command-line tool.
    """

    @classmethod
    @override
    def tool_name(cls) -> str:
        """Return the Git CLI executable name."""
        return 'git'

    @staticmethod
    @override
    def ecosystem() -> Ecosystem:
        """Git belongs to the `git` ecosystem."""
        return Ecosystem('git')

    @override
    async def clone(self, params: CloneParameters) -> bool:
        """Clone a Git repository per *params*.

        Runs ``git clone --progress`` via ``run_command`` so that
        stderr progress lines (``Receiving objects: 42%``) stream
        through ``params.progress_callback`` in real time, matching
        the CLI's per-action output-tail surfacing on failure.

        Args:
            params: Clone parameters (URL, destination, dry-run flag,
                optional progress callback).

        Returns:
            `True` on success, `False` on failure.
        """
        if params.dry:
            logger.info('Would clone %s into %s', params.url, params.destination)
            return True

        action = self._build_action(f"Clone '{params.url}'")
        progress = CommandProgress(
            action=action,
            callback=params.progress_callback or (lambda _: None),
            phase='cloning',
        )
        result = await run_command(
            ['git', 'clone', '--progress', params.url, str(params.destination)],
            progress=progress,
            timeout_seconds=600.0,
        )
        if result.returncode != 0:
            logger.error(result.stderr)
        return result.returncode == 0

    @override
    def clone_command(self, url: str, destination: Path) -> list[str]:
        """Return the CLI command for cloning a repository, including ``--progress``.

        Overridden so the previewed command matches what ``clone()``
        actually runs.
        """
        return ['git', 'clone', '--progress', url, str(destination)]

    @override
    async def get_remote_urls(self, destination: Path) -> dict[str, str]:
        """Return all remote fetch URLs for the Git repository at *destination*.

        Parses ``git remote -v`` output, collecting only fetch URLs.

        Args:
            destination: Local path of an existing Git clone.

        Returns:
            A mapping of remote name to fetch URL.
        """
        try:
            proc = await asyncio.create_subprocess_exec(
                'git',
                '-C',
                str(destination),
                'remote',
                '-v',
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout_bytes, _ = await asyncio.wait_for(proc.communicate(), timeout=30)
            if proc.returncode != 0:
                return {}
            stdout = stdout_bytes.decode('utf-8', errors='replace') if stdout_bytes else ''
        except FileNotFoundError, OSError:
            return {}

        _min_remote_fields = 3  # "<name>\t<url> (fetch|push)" → at least 3 tokens
        remotes: dict[str, str] = {}
        for line in stdout.splitlines():
            parts = line.split()
            if len(parts) >= _min_remote_fields and parts[-1] == '(fetch)':
                remotes[parts[0]] = parts[1]
        return remotes

    @override
    async def find_repo_root(self, path: Path) -> Path | None:
        """Find the Git repository root that contains *path*.

        Uses ``git rev-parse --show-toplevel`` to locate the root.

        Args:
            path: A filesystem path that may be inside a Git repository.

        Returns:
            The repository root directory, or ``None`` if *path* is
            not inside a Git repository.
        """
        try:
            proc = await asyncio.create_subprocess_exec(
                'git',
                '-C',
                str(path),
                'rev-parse',
                '--show-toplevel',
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout_bytes, _ = await asyncio.wait_for(proc.communicate(), timeout=30)
            if proc.returncode == 0:
                stdout = stdout_bytes.decode('utf-8', errors='replace').strip() if stdout_bytes else ''
                return Path(stdout)
        except FileNotFoundError, OSError:
            pass
        return None
