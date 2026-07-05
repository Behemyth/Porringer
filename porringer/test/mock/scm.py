"""Tests covering the scm behavior.

Mock SCM environment data.
"""

from pathlib import Path
from typing import override

from porringer.core.plugin_schema.scm import CloneParameters, ScmEnvironment
from porringer.core.schema import Ecosystem


class MockScm(ScmEnvironment):
    """Mocked SCM environment plugin for testing."""

    @classmethod
    @override
    def tool_name(cls) -> str:
        """Returns the mock tool name."""
        return 'mock-scm'

    @staticmethod
    @override
    def ecosystem() -> Ecosystem:
        """Mock manages the `git` ecosystem."""
        return Ecosystem('git')

    @override
    async def clone(self, params: CloneParameters) -> bool:
        """No-op clone for testing."""
        return True

    @override
    async def get_remote_urls(self, destination: Path) -> dict[str, str]:
        """Always returns empty dict for testing."""
        return {}

    @override
    async def find_repo_root(self, path: Path) -> Path | None:
        """Always returns None for testing."""
        return None
