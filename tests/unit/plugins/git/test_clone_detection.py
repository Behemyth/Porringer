"""Helpers for test clone detection.

Unit tests for Git SCM clone detection and URL comparison.
"""

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from packaging.version import Version

from porringer.core.plugin_schema.scm import CloneParameters, ScmEnvironment
from porringer.core.schema import Distribution, PluginParameters
from porringer.plugin.git.plugin import GitScm
from porringer.schema import ActionProgress
from porringer.schema.execution import CloneStatusKind
from porringer.test.mock.subprocess import fake_proc as _fake_proc
from tests.fixtures.command_process import CommandProcess

_PARAMS = PluginParameters(distribution=Distribution(version=Version('0.0.0')))


# -- urls_match tests -------------------------------------------------


class TestUrlsMatch:
    """Tests for ScmEnvironment.urls_match."""

    @staticmethod
    @pytest.mark.parametrize(
        ('a', 'b'),
        [
            ('https://github.com/org/repo', 'https://github.com/org/repo'),
            ('https://github.com/org/repo.git', 'https://github.com/org/repo'),
            ('https://github.com/org/repo', 'https://github.com/org/repo.git'),
            ('https://github.com/org/repo.git', 'https://github.com/org/repo.git'),
            ('https://github.com/org/repo/', 'https://github.com/org/repo'),
            ('https://github.com/org/repo.git/', 'https://github.com/org/repo'),
        ],
    )
    def test_equivalent_urls(a: str, b: str) -> None:
        """URLs that differ only by trailing .git or / should match."""
        assert ScmEnvironment.urls_match(a, b) is True

    @staticmethod
    @pytest.mark.parametrize(
        ('a', 'b'),
        [
            ('https://github.com/org/repo', 'https://github.com/other/repo'),
            ('https://github.com/org/repo', 'https://gitlab.com/org/repo'),
            ('https://github.com/org/repo', 'http://github.com/org/repo'),
            ('https://github.com/org/repo', 'https://github.com/org/different'),
        ],
    )
    def test_different_urls(a: str, b: str) -> None:
        """URLs with different host, scheme, or path should not match."""
        assert ScmEnvironment.urls_match(a, b) is False

    @staticmethod
    @pytest.mark.parametrize(
        ('a', 'b'),
        [
            ('https://github.com/Org/Repo', 'https://github.com/org/repo'),
            ('https://github.com/ORG/REPO.git', 'https://github.com/org/repo'),
            ('https://GitHub.COM/org/repo', 'https://github.com/org/repo'),
            ('https://github.com/Behemyth/periapsis', 'https://github.com/behemyth/periapsis'),
        ],
    )
    def test_case_insensitive_host_and_path(a: str, b: str) -> None:
        """URLs that differ only by case in host/path should match."""
        assert ScmEnvironment.urls_match(a, b) is True


# -- GitScm.get_remote_urls tests -------------------------------------


class TestGetRemoteUrls:
    """Tests for GitScm.get_remote_urls."""

    @staticmethod
    async def test_returns_all_fetch_urls(tmp_path: Path) -> None:
        """get_remote_urls returns a dict of remote name → fetch URL."""
        scm = GitScm(_PARAMS)
        git_output = (
            'origin\thttps://github.com/fork/repo.git (fetch)\n'
            'origin\thttps://github.com/fork/repo.git (push)\n'
            'upstream\thttps://github.com/org/repo.git (fetch)\n'
            'upstream\thttps://github.com/org/repo.git (push)\n'
        )
        fake_proc = _fake_proc(returncode=0, stdout=git_output)
        with patch('asyncio.create_subprocess_exec', return_value=fake_proc) as mock_exec:
            result = await scm.get_remote_urls(tmp_path)
            mock_exec.assert_called_once()
        assert result == {
            'origin': 'https://github.com/fork/repo.git',
            'upstream': 'https://github.com/org/repo.git',
        }

    @staticmethod
    async def test_returns_empty_dict_on_failure(tmp_path: Path) -> None:
        """get_remote_urls returns empty dict when git exits non-zero."""
        scm = GitScm(_PARAMS)
        fake_proc = _fake_proc(returncode=1, stderr='error')
        with patch('asyncio.create_subprocess_exec', return_value=fake_proc):
            result = await scm.get_remote_urls(tmp_path)
        assert result == {}

    @staticmethod
    async def test_returns_empty_dict_on_missing_git(tmp_path: Path) -> None:
        """get_remote_urls returns empty dict when git is not found."""
        scm = GitScm(_PARAMS)
        with patch('asyncio.create_subprocess_exec', side_effect=FileNotFoundError):
            result = await scm.get_remote_urls(tmp_path)
        assert result == {}

    @staticmethod
    async def test_single_remote(tmp_path: Path) -> None:
        """get_remote_urls handles a single remote correctly."""
        scm = GitScm(_PARAMS)
        git_output = 'origin\thttps://github.com/org/repo.git (fetch)\norigin\thttps://github.com/org/repo.git (push)\n'
        fake_proc = _fake_proc(returncode=0, stdout=git_output)
        with patch('asyncio.create_subprocess_exec', return_value=fake_proc):
            result = await scm.get_remote_urls(tmp_path)
        assert result == {'origin': 'https://github.com/org/repo.git'}


# -- GitScm.find_repo_root tests --------------------------------------


class TestFindRepoRoot:
    """Tests for GitScm.find_repo_root."""

    @staticmethod
    async def test_returns_root_when_inside_repo(tmp_path: Path) -> None:
        """find_repo_root returns the repository root path."""
        scm = GitScm(_PARAMS)
        root = str(tmp_path / 'my-repo')
        fake_proc = _fake_proc(returncode=0, stdout=f'{root}\n')
        with patch('asyncio.create_subprocess_exec', return_value=fake_proc):
            result = await scm.find_repo_root(tmp_path / 'my-repo' / 'subdir')
        assert result == Path(root)

    @staticmethod
    async def test_returns_none_when_not_in_repo(tmp_path: Path) -> None:
        """find_repo_root returns None when path is not inside a repository."""
        scm = GitScm(_PARAMS)
        fake_proc = _fake_proc(returncode=128, stderr='fatal: not a git repo')
        with patch('asyncio.create_subprocess_exec', return_value=fake_proc):
            result = await scm.find_repo_root(tmp_path)
        assert result is None

    @staticmethod
    async def test_returns_none_on_missing_git(tmp_path: Path) -> None:
        """find_repo_root returns None when git is not found."""
        scm = GitScm(_PARAMS)
        with patch('asyncio.create_subprocess_exec', side_effect=FileNotFoundError):
            result = await scm.find_repo_root(tmp_path)
        assert result is None


# -- ScmEnvironment.is_cloned tests (base class, using GitScm) --------


class TestIsCloned:
    """Tests for the base ScmEnvironment.is_cloned (exercised via GitScm)."""

    @staticmethod
    async def test_missing_when_no_repo_root(tmp_path: Path) -> None:
        """is_cloned returns MISSING when find_repo_root returns None."""
        scm = GitScm(_PARAMS)
        with patch.object(scm, 'find_repo_root', new_callable=AsyncMock, return_value=None):
            result = await scm.is_cloned('https://github.com/org/repo', tmp_path / 'nonexistent')
        assert result.kind == CloneStatusKind.MISSING
        assert result.remote_url is None

    @staticmethod
    async def test_cloned_when_origin_matches(tmp_path: Path) -> None:
        """is_cloned returns CLONED when origin remote URL matches."""
        scm = GitScm(_PARAMS)
        url = 'https://github.com/org/repo'
        remotes = {'origin': 'https://github.com/org/repo.git'}
        with (
            patch.object(scm, 'find_repo_root', new_callable=AsyncMock, return_value=tmp_path),
            patch.object(scm, 'get_remote_urls', new_callable=AsyncMock, return_value=remotes),
        ):
            result = await scm.is_cloned(url, tmp_path)
        assert result.kind == CloneStatusKind.CLONED
        assert result.remote_url == 'https://github.com/org/repo.git'
        assert result.matched_remote == 'origin'

    @staticmethod
    async def test_cloned_when_upstream_matches(tmp_path: Path) -> None:
        """is_cloned returns CLONED when upstream (not origin) matches — fork workflow."""
        scm = GitScm(_PARAMS)
        url = 'https://github.com/org/repo'
        remotes = {
            'origin': 'https://github.com/fork/repo.git',
            'upstream': 'https://github.com/org/repo.git',
        }
        with (
            patch.object(scm, 'find_repo_root', new_callable=AsyncMock, return_value=tmp_path),
            patch.object(scm, 'get_remote_urls', new_callable=AsyncMock, return_value=remotes),
        ):
            result = await scm.is_cloned(url, tmp_path)
        assert result.kind == CloneStatusKind.CLONED
        assert result.remote_url == 'https://github.com/org/repo.git'
        assert result.matched_remote == 'upstream'

    @staticmethod
    async def test_url_mismatch_when_no_remote_matches(tmp_path: Path) -> None:
        """is_cloned returns URL_MISMATCH when no remote URL matches."""
        scm = GitScm(_PARAMS)
        url = 'https://github.com/org/repo'
        remotes = {'origin': 'https://github.com/other/different.git'}
        with (
            patch.object(scm, 'find_repo_root', new_callable=AsyncMock, return_value=tmp_path),
            patch.object(scm, 'get_remote_urls', new_callable=AsyncMock, return_value=remotes),
        ):
            result = await scm.is_cloned(url, tmp_path)
        assert result.kind == CloneStatusKind.URL_MISMATCH
        assert result.remote_url == 'https://github.com/other/different.git'


# -- GitScm.clone tests -------------------------------------------------


class TestGitScmClone:
    """Tests for GitScm.clone (progress-streaming, per CloneParameters)."""

    @staticmethod
    async def test_dry_run_skips_subprocess() -> None:
        """dry=True returns True without running git."""
        scm = GitScm(_PARAMS)
        with patch('porringer.plugin.git.plugin.run_command', new_callable=AsyncMock) as mock_run:
            result = await scm.clone(
                CloneParameters(url='https://github.com/org/repo', destination=Path('/tmp/repo'), dry=True)
            )
        assert result is True
        mock_run.assert_not_awaited()

    @staticmethod
    async def test_streams_progress_and_succeeds(tmp_path: Path, command_process: CommandProcess) -> None:
        """A successful clone streams stderr progress lines through the callback."""
        destination = tmp_path / 'repo'
        command_process.script(
            ['git', 'clone', '--progress', 'https://github.com/org/repo', str(destination)],
            stderr=["Cloning into 'repo'...", 'Receiving objects: 100% (3/3), done.'],
            returncode=0,
        )
        events: list[ActionProgress] = []
        scm = GitScm(_PARAMS)

        result = await scm.clone(
            CloneParameters(
                url='https://github.com/org/repo',
                destination=destination,
                progress_callback=events.append,
            )
        )

        assert result is True
        assert [e.output for e in events] == [
            "Cloning into 'repo'...",
            'Receiving objects: 100% (3/3), done.',
        ]
        assert all(e.channel == 'stderr' for e in events)

    @staticmethod
    async def test_failed_clone_returns_false(tmp_path: Path, command_process: CommandProcess) -> None:
        """A non-zero exit returns False."""
        destination = tmp_path / 'repo'
        command_process.script(
            ['git', 'clone', '--progress', 'https://github.com/org/repo', str(destination)],
            stderr=['fatal: repository not found'],
            returncode=128,
        )

        scm = GitScm(_PARAMS)
        result = await scm.clone(CloneParameters(url='https://github.com/org/repo', destination=destination))

        assert result is False

    @staticmethod
    def test_clone_command_includes_progress_flag() -> None:
        """clone_command() previews the same --progress flag clone() actually uses."""
        scm = GitScm(_PARAMS)
        destination = Path('/tmp/repo')
        cmd = scm.clone_command('https://github.com/org/repo', destination)
        assert cmd == ['git', 'clone', '--progress', 'https://github.com/org/repo', str(destination)]

    @staticmethod
    async def test_url_mismatch_when_no_remotes(tmp_path: Path) -> None:
        """is_cloned returns URL_MISMATCH when repo has no remotes."""
        scm = GitScm(_PARAMS)
        with (
            patch.object(scm, 'find_repo_root', new_callable=AsyncMock, return_value=tmp_path),
            patch.object(scm, 'get_remote_urls', new_callable=AsyncMock, return_value={}),
        ):
            result = await scm.is_cloned('https://github.com/org/repo', tmp_path)
        assert result.kind == CloneStatusKind.URL_MISMATCH
        assert result.remote_url is None

    @staticmethod
    async def test_nested_manifest_uses_repo_root(tmp_path: Path) -> None:
        """is_cloned finds the repo root when destination is a subdirectory."""
        scm = GitScm(_PARAMS)
        repo_root = tmp_path / 'my-repo'
        subdir = repo_root / 'subdir'
        url = 'https://github.com/org/repo'
        remotes = {'origin': 'https://github.com/org/repo.git'}
        with (
            patch.object(scm, 'find_repo_root', new_callable=AsyncMock, return_value=repo_root),
            patch.object(scm, 'get_remote_urls', new_callable=AsyncMock, return_value=remotes),
        ):
            result = await scm.is_cloned(url, subdir)
        assert result.kind == CloneStatusKind.CLONED
        assert result.repo_root == repo_root
