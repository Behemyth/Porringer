"""CLI command implementation for discovery.

Plugin discovery helpers.

Provides `discover_all_plugins` which scans the `porringer.environment`
and `porringer.scm` entry-point groups and partitions them into a
`DiscoveredPlugins` container.  Extracted from the sync module so that
both `manifest` and `execution` can import it without circular
dependencies.

Plugin *scan* results (the ``PluginInformation`` metadata returned by
``Builder.find_plugins()``) are cached so that repeatedly calling
``discover_all_plugins()`` does not re-scan entry points.  Plugin
*instances* are created fresh on every call so that accidentally
mutated state on a plugin object cannot leak between API calls.
"""

import importlib
import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import cast

from porringer.backend.builder import Builder, PluginInformation
from porringer.core.plugin_schema.environment import Environment
from porringer.core.plugin_schema.manifest import ManifestContributor
from porringer.core.plugin_schema.project_environment import ProjectInstaller
from porringer.core.plugin_schema.runtime import RuntimeConsumer, RuntimeContext, RuntimeProvider
from porringer.core.plugin_schema.scm import ScmEnvironment
from porringer.core.plugin_schema.tool_based import ToolBasedPlugin
from porringer.core.schema import Plugin
from porringer.schema.plugin import PluginCapability

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class DiscoveredPlugins:
    """Result of discovering every plugin at once.

    Plugins live in two entry-point groups: ``porringer.environment``
    (partitioned by capability into package :attr:`environments` and
    project-install :attr:`project_environments`) and
    ``porringer.scm`` (:attr:`scm_environments`). A plugin that both
    installs packages and implements the project-install capability
    (e.g. ``uv``) appears in both :attr:`environments` and
    :attr:`project_environments`.

    Provides :attr:`all_plugins` for a merged view and :meth:`copy`
    for creating an independent set of plugin instances.

    When the optional ``_*_infos`` fields are populated (the normal
    production path), :meth:`copy` constructs **fresh** plugin objects
    from the stored metadata so that per-run mutation cannot leak
    between callers.  When infos are absent (the test convenience
    path), :meth:`copy` shallow-copies the dict containers instead.
    """

    environments: dict[str, Environment]
    project_environments: dict[str, ProjectInstaller]
    scm_environments: dict[str, ScmEnvironment]

    # Optional factory metadata — populated by production discovery,
    # omitted by test helpers that construct instances directly.
    _env_infos: list[PluginInformation[Environment]] | None = field(default=None, repr=False)
    _proj_infos: list[PluginInformation[ProjectInstaller]] | None = field(default=None, repr=False)
    _scm_infos: list[PluginInformation[ScmEnvironment]] | None = field(default=None, repr=False)

    load_errors: dict[str, str] = field(default_factory=dict, repr=False)
    """Entry-point names that failed to load, mapped to their error message.

    Populated by production discovery when a plugin import fails (e.g.
    broken dependency, syntax error in plugin code).  Callers that need
    to surface plugin failures to end-users should inspect this dict
    instead of relying solely on log output.
    """

    runtime_context: RuntimeContext | None = field(default=None, repr=False)
    """Resolved interpreter paths for RuntimeConsumer plugins.

    Populated by :meth:`API.discover_plugins` so that callers can
    forward a single ``DiscoveredPlugins`` object to every operation
    without separately managing a ``RuntimeContext``.
    """

    def resolved_runtime(self, runtime_context: RuntimeContext | None = None) -> RuntimeContext | None:
        """Return the effective runtime context.

        Prefers an explicitly supplied *runtime_context* over the
        instance's stored :attr:`runtime_context`.  This collapses
        the ``if runtime_context is None: runtime_context =
        plugins.runtime_context`` pattern that appears across many
        call-sites.

        Args:
            runtime_context: Caller-supplied override.  When not
                ``None``, returned as-is.

        Returns:
            The best available :class:`RuntimeContext`, or ``None``
            when neither source provides one.
        """
        if runtime_context is not None:
            return runtime_context
        return self.runtime_context

    @property
    def all_plugins(self) -> dict[str, Environment | ProjectInstaller | ScmEnvironment]:
        """Merged view of every discovered plugin keyed by canonical name."""
        return {**self.environments, **self.project_environments, **self.scm_environments}

    def copy(self) -> DiscoveredPlugins:
        """Return an independent copy with freshly constructed plugin instances.

        When factory metadata (``_*_infos``) is available, every plugin
        is re-instantiated so that accidental mutable state on an
        instance cannot leak between execution runs.  The cheap
        ``__init__`` (sets one field) makes this negligible compared
        to the entry-point scan that produced the infos.

        When infos are absent (e.g. hand-built ``DiscoveredPlugins``
        in tests), the dict containers are shallow-copied and plugin
        instances are shared — identical to the pre-factory behaviour.
        """
        if self._env_infos is not None and self._proj_infos is not None and self._scm_infos is not None:
            result = _build_from_infos(self._env_infos, self._proj_infos, self._scm_infos)
            result.runtime_context = self.runtime_context
            result.load_errors = dict(self.load_errors)
            return result
        return DiscoveredPlugins(
            environments=dict(self.environments),
            project_environments=dict(self.project_environments),
            scm_environments=dict(self.scm_environments),
            load_errors=dict(self.load_errors),
            runtime_context=self.runtime_context,
        )

    # -- Capability introspection --------------------------------------

    _CAPABILITY_MAP: tuple[tuple[type, PluginCapability], ...] = (
        (RuntimeConsumer, PluginCapability.RUNTIME_CONSUMER),
        (ManifestContributor, PluginCapability.MANIFEST_CONTRIBUTOR),
        (RuntimeProvider, PluginCapability.RUNTIME_PROVIDER),
    )

    def capabilities(self, plugin_name: str) -> set[PluginCapability]:
        """Return the set of capabilities implemented by *plugin_name*.

        Probes the instantiated plugin object for each known protocol
        mixin (``RuntimeConsumer``, ``ManifestContributor``,
        ``RuntimeProvider``) via ``isinstance``.

        Args:
            plugin_name: Canonical plugin name to inspect.

        Returns:
            A (possibly empty) set of capabilities.

        Raises:
            KeyError: If *plugin_name* is not found among discovered
                plugins.
        """
        plugin = self.all_plugins.get(plugin_name)
        if plugin is None:
            raise KeyError(f"Plugin '{plugin_name}' not found among discovered plugins")
        return {cap for proto, cap in self._CAPABILITY_MAP if isinstance(plugin, proto)}


def _build_instances[T: Plugin](infos: list[PluginInformation[T]]) -> dict[str, T]:
    """Build a name-keyed dict of fresh plugin instances from scan metadata.

    Args:
        infos: Plugin information list from ``Builder.find_plugins()``.

    Returns:
        Dict mapping canonical plugin name to a new plugin instance.
    """
    instances = Builder.build_plugins(infos)
    return {info.name: inst for info, inst in zip(infos, instances, strict=True)}


def _build_from_infos(
    env_infos: list[PluginInformation[Environment]],
    proj_infos: list[PluginInformation[ProjectInstaller]],
    scm_infos: list[PluginInformation[ScmEnvironment]],
) -> DiscoveredPlugins:
    """Construct a ``DiscoveredPlugins`` with fresh instances from cached scan metadata.

    Args:
        env_infos: Environment plugin scan results.
        proj_infos: Project-install plugin scan results.
        scm_infos: SCM-environment plugin scan results.

    Returns:
        A fully populated ``DiscoveredPlugins`` carrying both
        instances and their factory metadata.
    """
    environments = _build_instances(env_infos)
    project_environments = _build_instances(proj_infos)

    # Capability augmentation: a package plugin (Environment) that also
    # implements the project-install capability is exposed in both the
    # package and project dicts so a single plugin can drive both phases.
    for name, env in environments.items():
        if isinstance(env, ProjectInstaller):
            project_environments.setdefault(name, env)

    return DiscoveredPlugins(
        environments=environments,
        project_environments=project_environments,
        scm_environments=_build_instances(scm_infos),
        _env_infos=env_infos,
        _proj_infos=proj_infos,
        _scm_infos=scm_infos,
    )


# ---------------------------------------------------------------------------
# Plugin cache — stores scan metadata, not instances
# ---------------------------------------------------------------------------

CACHE_TTL: float = 30.0  # seconds


@dataclass(slots=True)
class _ScanCache:
    """Mutable container for cached entry-point scan results.

    Only the lightweight ``PluginInformation`` lists are stored —
    plugin instances are constructed fresh on every
    :func:`discover_all_plugins` call.
    """

    env_infos: list[PluginInformation[Environment]] | None = None
    proj_infos: list[PluginInformation[ProjectInstaller]] | None = None
    scm_infos: list[PluginInformation[ScmEnvironment]] | None = None
    timestamp: float = 0.0
    lock: threading.Lock = field(default_factory=threading.Lock)


_cache = _ScanCache()

# Callbacks invoked by ``invalidate_plugin_cache()`` so that other
# modules (e.g. manifest contribution cache) can piggy-back on the
# same invalidation signal without creating circular imports.
_invalidation_hooks: list[Callable[[], None]] = []


def register_invalidation_hook(hook: Callable[[], None]) -> None:
    """Register a callback to be called on :func:`invalidate_plugin_cache`.

    Allows modules that maintain their own scan-derived caches
    (e.g. manifest contributions) to clear them whenever the
    plugin cache is invalidated, without introducing circular
    imports back into ``discovery``.

    Args:
        hook: Zero-argument callable invoked during invalidation.
    """
    _invalidation_hooks.append(hook)


def invalidate_plugin_cache() -> None:
    """Clear the in-memory plugin scan cache.

    Also calls ``importlib.invalidate_caches()`` so that
    distributions installed since the last scan are visible to
    the next :func:`discover_all_plugins` call, and invokes
    every callback registered via :func:`register_invalidation_hook`
    so that derived caches (e.g. manifest contributions) stay
    in sync.

    Call this when the process environment changes (e.g. after
    installing a new backend).
    """
    importlib.invalidate_caches()
    with _cache.lock:
        _cache.env_infos = None
        _cache.proj_infos = None
        _cache.scm_infos = None
        _cache.timestamp = 0.0
    for hook in _invalidation_hooks:
        hook()


def _scan_plugins[T: Plugin](
    group: str,
    base_class: type[T],
    **kwargs: bool,
) -> tuple[list[PluginInformation[T]], dict[str, str]]:
    """Scan entry points and return plugin metadata without instantiating.

    Does **not** call ``importlib.invalidate_caches()`` — that is the
    responsibility of :func:`invalidate_plugin_cache`, which callers
    invoke before requesting a fresh (non-cached) scan.  Keeping the
    invalidation out of the scan path avoids a race where one thread's
    ``invalidate_caches()`` corrupts another thread's in-flight
    ``entry_points()`` call (observed as flaky failures on Windows CI).

    Args:
        group: Entry-point group suffix (e.g. ``'environment'``).
        base_class: Expected base class for the plugins.
        **kwargs: Forwarded to ``Builder.find_plugins()``
            (e.g. ``check_dependencies=True``).

    Returns:
        Tuple of ``(infos, load_errors)`` — plugin information objects
        and a dict of entry-point names that failed to load.
    """
    return Builder.find_plugins(group, base_class, **kwargs)


def scan_environment_group() -> tuple[
    list[PluginInformation[Environment]],
    list[PluginInformation[ProjectInstaller]],
    dict[str, str],
]:
    """Scan the single ``environment`` entry-point group and partition it.

    There is no separate "project" plugin type — ``Environment`` and
    project-only ``ProjectInstaller`` plugins are all registered under
    ``porringer.environment``.  A plugin implementing both capabilities
    (e.g. ``uv``, ``npm``, ``pnpm``) is classified purely as an
    environment here; its project-install capability is exposed via the
    capability-augmentation step in :func:`_build_from_infos`.
    Project-only plugins (``pdm``, ``poetry``) are classified as
    project installers.

    Returns:
        Tuple of ``(env_infos, proj_infos, load_errors)``.
    """
    all_infos, load_errors = _scan_plugins('environment', ToolBasedPlugin, check_dependencies=True)

    env_infos: list[PluginInformation[Environment]] = []
    proj_infos: list[PluginInformation[ProjectInstaller]] = []
    for info in all_infos:
        if issubclass(info.type, Environment):
            env_infos.append(cast(PluginInformation[Environment], info))
        elif issubclass(info.type, ProjectInstaller):
            proj_infos.append(cast(PluginInformation[ProjectInstaller], info))
        else:
            logger.warning(
                "Incompatible plugin '%s' — expected an 'Environment' or 'ProjectInstaller' subclass", info.name
            )

    return env_infos, proj_infos, load_errors


def discover_environments() -> dict[str, Environment]:
    """Discover and build all environment plugins.

    Convenience wrapper around the ``environment`` group scan. Project-only
    plugins registered in the same group (e.g. ``pdm``, ``poetry``) are
    excluded without a warning since that is expected — see
    :func:`scan_environment_group`.
    """
    env_infos, _proj_infos, _load_errors = scan_environment_group()
    result = _build_instances(env_infos)
    logger.debug('Discovered %d environment plugin(s): %s', len(result), sorted(result))
    return result


def discover_all_plugins(*, use_cache: bool = False) -> DiscoveredPlugins:
    """Discover all plugin groups in one call.

    The expensive entry-point scan is cached for :data:`CACHE_TTL`
    seconds.  Plugin *instances* are always constructed fresh so that
    accidentally mutated state cannot leak between API calls.

    Args:
        use_cache: When ``True``, reuse cached scan results if they
            exist and are younger than :data:`CACHE_TTL` seconds.
            Callers on the hot path (preview, inspect) set this to
            ``True``.  Callers that need freshness after installing
            packages (execution phase transitions) leave it ``False``.

    Returns:
        A ``DiscoveredPlugins`` with fresh plugin instances and
        the scan metadata needed for subsequent :meth:`copy` calls.
    """
    with _cache.lock:
        if (
            use_cache
            and _cache.env_infos is not None
            and _cache.proj_infos is not None
            and _cache.scm_infos is not None
            and (time.monotonic() - _cache.timestamp) < CACHE_TTL
        ):
            logger.debug('Plugin scan cache hit — building fresh instances')
            return _build_from_infos(_cache.env_infos, _cache.proj_infos, _cache.scm_infos)

    env_infos, proj_infos, env_proj_errors = scan_environment_group()
    scm_infos, scm_errors = _scan_plugins('scm', ScmEnvironment)

    result = _build_from_infos(env_infos, proj_infos, scm_infos)
    result.load_errors = {**env_proj_errors, **scm_errors}
    if result.load_errors:
        logger.warning('Plugin load failures: %s', list(result.load_errors))
    logger.info(
        'Plugin discovery: %d environments, %d project, %d scm',
        len(result.environments),
        len(result.project_environments),
        len(result.scm_environments),
    )
    logger.debug(
        'Discovered plugins — environments: %s, project: %s, scm: %s',
        sorted(result.environments),
        sorted(result.project_environments),
        sorted(result.scm_environments),
    )

    with _cache.lock:
        _cache.env_infos = env_infos
        _cache.proj_infos = proj_infos
        _cache.scm_infos = scm_infos
        _cache.timestamp = time.monotonic()

    return result
