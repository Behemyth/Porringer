"""Plugin integration for plugin.

Plugin implementation for pnpm environment.
"""

from pathlib import Path
from typing import override

from porringer.core.plugin_schema.environment import CheckUpdatesParameters, Environment
from porringer.core.plugin_schema.project_environment import NodeProjectInstaller
from porringer.core.plugin_schema.runtime import RuntimeContext
from porringer.core.schema import Ecosystem, Package, PackageRef


class PNPMEnvironment(Environment, NodeProjectInstaller):
    """Represents a Node.js environment managed by pnpm.

    Installs individual packages via ``pnpm add -g`` and also installs
    whole projects via ``pnpm install`` (the project-install capability).
    """

    _project_evidence_files = ('pnpm-lock.yaml',)
    _package_manager_names = ('pnpm@',)

    @staticmethod
    @override
    def ecosystem() -> Ecosystem:
        """Pnpm belongs to the `node` ecosystem."""
        return Ecosystem('node')

    @classmethod
    @override
    def tool_name(cls) -> str:
        """Pnpm wraps the `pnpm` CLI."""
        return 'pnpm'

    @override
    def install_command(
        self, package: PackageRef, *, include_prereleases: bool = False, runtime_context: RuntimeContext | None = None
    ) -> list[str]:
        """Returns the CLI command to install a package via pnpm."""
        return ['pnpm', 'add', '-g', package.specifier_for('at')]

    @override
    def upgrade_command(
        self, package: PackageRef, *, include_prereleases: bool = False, runtime_context: RuntimeContext | None = None
    ) -> list[str]:
        """Returns the CLI command to upgrade a package via pnpm."""
        if package.constraint:
            return ['pnpm', 'update', '-g', package.specifier_for('at')]
        return ['pnpm', 'update', '-g', '--latest', package.name]

    @override
    def uninstall_command(self, package: PackageRef, *, runtime_context: RuntimeContext | None = None) -> list[str]:
        """Returns the CLI command to uninstall a package via pnpm."""
        return ['pnpm', 'remove', '-g', package.name]

    @override
    async def check_updates(self, params: CheckUpdatesParameters) -> list[Package]:
        """Checks for available updates by querying the npm registry.

        pnpm uses the same npm registry; delegates to the shared
        ``_check_npm_registry`` helper.

        Args:
            params: The check parameters.

        Returns:
            A list of packages with their latest available version.
        """
        return await self._check_npm_registry(
            params.packages,
            include_prereleases=params.include_prereleases,
            max_concurrency=params.max_concurrency,
            http_client=params.http_client,
        )

    @override
    async def packages(
        self, *, project_path: Path | None = None, runtime_context: RuntimeContext | None = None
    ) -> list[Package]:
        """Gathers globally installed pnpm packages.

        Uses `pnpm list -g --json --depth=0` to list top-level global
        packages and parses the JSON output.  *project_path* is
        accepted for interface compatibility but ignored for now.

        Args:
            project_path: Unused.
            runtime_context: Unused.  pnpm is not Python-scoped.

        Returns:
            A list of installed packages.
        """
        entries = await self._run_json_command(['pnpm', 'list', '-g', '--json', '--depth=0'])
        if entries is None:
            return []
        # pnpm returns a JSON array; global store is usually the first entry
        if isinstance(entries, list) and entries:
            deps = entries[0].get('dependencies', {})
        elif isinstance(entries, dict):
            deps = entries.get('dependencies', {})
        else:
            return []
        return [
            Package(name=name, version=info.get('version')) for name, info in deps.items() if isinstance(info, dict)
        ]
