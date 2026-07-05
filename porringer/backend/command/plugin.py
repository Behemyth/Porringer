"""CLI command implementation for plugin.

The plugin command module.

Lists *porringer extension* plugins (e.g. ``porringer-plugin-apt``)
that extend porringer's capabilities.  For operations on packages
*managed by* plugins (e.g. ``requests`` via pip), see :mod:`.package`.
"""

import builtins
import logging

from porringer.backend.builder import Builder
from porringer.backend.command.core.discovery import DiscoveredPlugins, discover_all_plugins
from porringer.backend.resolver import build_plugin_info
from porringer.core.plugin_schema.runtime import RuntimeContext
from porringer.core.schema import Plugin, PluginKind
from porringer.schema import PluginInfo

logger = logging.getLogger(__name__)


class PluginCommands:
    """Extension package commands.

    All methods are static — the class acts as a namespace and does
    not require instantiation.  Use `PluginCommands.list()` directly
    or via an `API` instance.
    """

    @staticmethod
    async def list(
        *,
        kinds: builtins.list[PluginKind] | None = None,
        plugins: DiscoveredPlugins | None = None,
        runtime_context: RuntimeContext | None = None,
    ) -> builtins.list[PluginInfo]:
        """Lists all registered plugins across every plugin group.

        Discovers `environment` (package / tool / runtime),
        `project_environment` (project sync), and `scm` (source control)
        plugins.  Results can be filtered by `kinds`.

        When *plugins* is provided, its environments, project
        environments, and SCM plugins are used directly — no
        entry-point scanning is performed.  ``runtime_context`` is
        extracted from ``plugins.runtime_context`` unless an explicit
        value is supplied.

        Args:
            kinds: Only include plugins matching these kinds. `None` returns all.
            plugins: Pre-discovered plugins from :meth:`API.discover_plugins`.
            runtime_context: Pre-resolved runtime context.  When
                ``None``, a context is resolved automatically from
                available runtime providers.

        Returns:
            A list of registered plugins, optionally filtered by kind.
        """
        logger.debug('Listing plugins')

        if plugins is not None:
            environments = plugins.environments
            projects = plugins.project_environments
            scm_plugins = plugins.scm_environments
            runtime_context = plugins.resolved_runtime(runtime_context)
        else:
            discovered = discover_all_plugins()
            environments = discovered.environments
            projects = discovered.project_environments
            scm_plugins = discovered.scm_environments

        # Auto-resolve runtime context when the caller did not supply one.
        if runtime_context is None:
            runtime_context = await Builder.resolve_runtime_context(environments)

        all_plugins: dict[str, Plugin] = {**environments, **projects, **scm_plugins}

        results = build_plugin_info(all_plugins, kinds=kinds, runtime_context=runtime_context)

        return results
