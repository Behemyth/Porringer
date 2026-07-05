"""Core helpers and types for scm.

Plugin utilities for source-control management (SCM) environments.

An `ScmEnvironment` plugin wraps an SCM tool (e.g. Git) and
provides clone and presence-check operations.  The sync engine invokes
SCM actions after project sync, so repositories are cloned after dependency
synchronisation.
"""

import logging
from abc import abstractmethod
from collections.abc import Callable
from pathlib import Path
from urllib.parse import urlparse

from pydantic import Field

from porringer.core.plugin_schema.tool_based import ToolBasedPlugin
from porringer.core.schema import Ecosystem, PackageRef, PluginKind, PorringerModel
from porringer.schema import ActionProgress, SetupAction
from porringer.schema.execution import CloneStatus, CloneStatusKind

logger = logging.getLogger(__name__)


class CloneParameters(PorringerModel):
    """Parameters for a repository clone operation."""

    url: str = Field(description='The repository URL to clone')
    destination: Path = Field(description='Local filesystem path for the clone')
    dry: bool = Field(default=False, description='If True, rehearses the operation without modifying the filesystem')
    progress_callback: Callable[[ActionProgress], None] | None = Field(
        default=None,
        exclude=True,
        description='Optional callback for reporting clone progress (line-by-line subprocess output).',
    )


class ScmEnvironment(ToolBasedPlugin):
    """Plugin definition for source-control management environments.

    Unlike `Environment`,
    which installs individual packages, an `ScmEnvironment` clones
    repositories using an SCM tool such as Git.

    Subclasses **must** override `tool_name()`, `clone()`,
    `get_remote_urls()`, and `find_repo_root()`.

    The default `is_cloned()` implementation uses these hooks to
    check **all** remotes, so fork workflows (where the manifest URL
    matches ``upstream`` rather than ``origin``) are handled
    automatically.
    """

    # ------------------------------------------------------------------
    # Subclass hooks
    # ------------------------------------------------------------------

    @classmethod
    @abstractmethod
    def tool_name(cls) -> str:
        """Return the CLI executable name this plugin wraps.

        Used by `is_available()` to verify the tool is on PATH and
        by `clone_command()` to build the default command.
        """
        ...

    @abstractmethod
    async def clone(self, params: CloneParameters) -> bool:
        """Clone a repository per *params*.

        Implementers that run a CLI subprocess should observe its
        output line-by-line (via ``run_command``'s ``progress``
        parameter) and forward each line to
        ``params.progress_callback`` when set, so the CLI can surface
        output under the action when the clone fails.

        Args:
            params: Clone parameters (URL, destination, dry-run flag,
                optional progress callback).

        Returns:
            `True` on success, `False` on failure.
        """
        ...

    @abstractmethod
    async def get_remote_urls(self, destination: Path) -> dict[str, str]:
        """Return all remote fetch URLs for the repository at *destination*.

        Args:
            destination: Local path of an existing clone.

        Returns:
            A mapping of remote name to fetch URL.
            Empty dict if the repository has no remotes or the query fails.
        """
        ...

    @abstractmethod
    async def find_repo_root(self, path: Path) -> Path | None:
        """Find the SCM repository root that contains *path*.

        Walks up from *path* to locate the root of the repository.
        This handles the case where a manifest lives inside a
        subdirectory of an already-cloned repo.

        Args:
            path: A filesystem path that may be inside a repository.

        Returns:
            The repository root directory, or ``None`` if *path* is
            not inside a repository managed by this SCM tool.
        """
        ...

    # ------------------------------------------------------------------
    # Defaults (override only when the tool deviates from the pattern)
    # ------------------------------------------------------------------

    @staticmethod
    @abstractmethod
    def ecosystem() -> Ecosystem:
        """Return the ecosystem this SCM environment belongs to.

        Examples: `"git"`, `"hg"`.
        """
        ...

    @staticmethod
    def plugin_kind() -> PluginKind:
        """SCM environments always have kind `SCM`."""
        return PluginKind.SCM

    async def is_cloned(self, url: str, destination: Path) -> CloneStatus:
        """Check whether *url* has already been cloned at or above *destination*.

        The default implementation:

        1. Calls `find_repo_root()` to locate the actual repository
           root (handles nested manifests).
        2. Calls `get_remote_urls()` to query **all** remotes.
        3. If **any** remote's URL matches *url* (via `urls_match`),
           returns ``CLONED`` with the matched remote name.
        4. If the repository exists but no remote matches, returns
           ``URL_MISMATCH``.

        Subclasses may override for SCM-specific behaviour.

        Args:
            url: The expected repository URL.
            destination: Expected local path for the clone.

        Returns:
            A `CloneStatus` indicating the result.
        """
        # Walk up to find the actual repo root (handles nested manifests)
        repo_root = await self.find_repo_root(destination)

        if repo_root is None:
            return CloneStatus(kind=CloneStatusKind.MISSING)

        effective_root = repo_root if repo_root != destination else None

        remotes = await self.get_remote_urls(repo_root)
        if not remotes:
            return CloneStatus(kind=CloneStatusKind.URL_MISMATCH, repo_root=effective_root)

        for remote_name, remote_url in remotes.items():
            if self.urls_match(url, remote_url):
                return CloneStatus(
                    kind=CloneStatusKind.CLONED,
                    remote_url=remote_url,
                    matched_remote=remote_name,
                    repo_root=effective_root,
                )

        # Repo exists but no remote matches the manifest URL
        first_url = next(iter(remotes.values()))
        return CloneStatus(
            kind=CloneStatusKind.URL_MISMATCH,
            remote_url=first_url,
            repo_root=effective_root,
        )

    @staticmethod
    def urls_match(expected: str, actual: str) -> bool:
        """Compare two remote URLs after normalizing trivial differences.

        Strips trailing slashes and ``.git`` suffixes from the path
        component so that ``https://github.com/org/repo.git`` matches
        ``https://github.com/org/repo``.

        The comparison is **case-insensitive** for the hostname and
        path, since all major Git hosting platforms treat repository
        owner and name as case-insensitive.

        Subclasses may override this if their SCM tool uses a different
        URL convention or needs SSH↔HTTPS equivalence.

        Args:
            expected: The URL declared in the manifest.
            actual: The URL reported by the local clone's remote.

        Returns:
            ``True`` when the URLs are considered equivalent.
        """
        parsed_expected = urlparse(expected)
        parsed_actual = urlparse(actual)

        norm_path_expected = parsed_expected.path.rstrip('/').removesuffix('.git').lower()
        norm_path_actual = parsed_actual.path.rstrip('/').removesuffix('.git').lower()

        host_expected = (parsed_expected.hostname or '').lower()
        host_actual = (parsed_actual.hostname or '').lower()

        return (
            parsed_expected.scheme == parsed_actual.scheme
            and host_expected == host_actual
            and norm_path_expected == norm_path_actual
        )

    def clone_command(self, url: str, destination: Path) -> list[str]:
        """Return the CLI command for cloning a repository.

        Built from `tool_name()` and the provided URL/destination.
        This is used for displaying commands in dry-run / preview mode.

        Args:
            url: The repository URL.
            destination: Local filesystem path for the clone.

        Returns:
            The CLI command as a list of strings.
        """
        return [self.tool_name(), 'clone', url, str(destination)]

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _build_action(self, description: str, package: PackageRef | None = None) -> SetupAction:
        """Build a `SetupAction` populated from this plugin's metadata.

        Used to satisfy ``CommandProgress.action``'s type requirement
        when running a subprocess with line-by-line progress. The
        engine's own progress callback (threaded in via
        ``CloneParameters.progress_callback``) always wraps events
        using the *real* action it tracks — this synthetic action is
        never used for event routing.
        """
        return SetupAction(
            description=description,
            kind=self.plugin_kind(),
            ecosystem=self.ecosystem(),
            installer=self.tool_name(),
            package=package,
        )
