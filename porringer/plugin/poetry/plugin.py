"""Plugin integration for plugin.

Plugin implementation for Poetry project environment.
"""

from typing import override

from porringer.core.plugin_schema.project_environment import (
    ProjectCommandPlan,
    ProjectEnvironment,
    ProjectInstallParameters,
)
from porringer.core.plugin_schema.runtime import RuntimeContext
from porringer.core.schema import Ecosystem


class PoetryEnvironment(ProjectEnvironment):
    """Project environment managed by Poetry.

    Delegates venv creation, dependency resolution, and lock-file
    synchronisation to `poetry install`.

    Overrides `sync()` because Poetry requires a separate
    `poetry env use <path>` step to select a non-default interpreter,
    unlike PDM/uv which accept `--python` inline.
    """

    _project_evidence_files = ('poetry.lock',)
    _pyproject_tool_tables = (('tool', 'poetry'),)

    @staticmethod
    @override
    def ecosystem() -> Ecosystem:
        """Poetry belongs to the `python` ecosystem."""
        return Ecosystem('python')

    @classmethod
    @override
    def consumed_runtime_kind(cls) -> str:
        """Poetry consumes a Python runtime."""
        return 'python'

    @classmethod
    @override
    def tool_name(cls) -> str:
        """Poetry wraps the `poetry` CLI."""
        return 'poetry'

    @override
    def project_install_command(self, *, runtime_context: RuntimeContext | None = None) -> list[str]:
        """Return the bare `poetry install` command.

        Poetry does not accept `--python` inline; runtime selection
        is handled by a separate `poetry env use` step in `sync()`.
        """
        return [self.tool_name(), self._install_verb]

    @classmethod
    @override
    def command_plan(cls, search_from, *, runtime_context: RuntimeContext | None = None) -> ProjectCommandPlan:
        """Build Poetry's sync plan, including runtime selection when needed."""
        directory = cls.resolve_project_root(search_from) or search_from
        steps: list[list[str]] = []
        if runtime_context is not None:
            exe = runtime_context.get(cls.consumed_runtime_kind())
            if exe is not None:
                steps.append(['poetry', 'env', 'use', str(exe)])
        steps.append(['poetry', cls._install_verb])
        return ProjectCommandPlan(directory=directory, argv=steps[-1], steps=steps)

    @override
    async def install_project(self, params: ProjectInstallParameters) -> bool:
        """Runs `poetry install` in the project directory.

        If a runtime provider has resolved a Python interpreter, calls
        `poetry env use <path>` first so that Poetry targets the
        correct runtime.

        Args:
            params: Sync parameters.

        Returns:
            True on success.
        """
        # Poetry requires `env use` to select a non-default interpreter
        if params.runtime_context is not None:
            exe = params.runtime_context.get(self.consumed_runtime_kind())
            if exe is not None:
                env_args = ['poetry', 'env', 'use', str(exe)]
                if not await self._run_project_install(env_args, params.directory):
                    return False

        args = list(self.project_install_command(runtime_context=params.runtime_context))
        if params.dry:
            args.append('--dry-run')
        return await self._run_project_install(args, params.directory)
