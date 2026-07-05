"""Public package exports for the core.plugin_schema module.

Plugin schema package for Porringer.

This package contains the schema definitions and base classes for plugins,
including environment plugins and their parameters.
"""

from porringer.core.plugin_schema.environment import Environment
from porringer.core.plugin_schema.manifest import ManifestContributor
from porringer.core.plugin_schema.project_environment import ProjectEnvironment
from porringer.core.plugin_schema.runtime import DefaultRuntimeExecutableProvider, RuntimeConsumer, RuntimeProvider
from porringer.core.plugin_schema.scm import ScmEnvironment
from porringer.core.plugin_schema.tool_based import ToolBasedPlugin

__all__ = [
    'Environment',
    'ManifestContributor',
    'ProjectEnvironment',
    'DefaultRuntimeExecutableProvider',
    'RuntimeConsumer',
    'RuntimeProvider',
    'ScmEnvironment',
    'ToolBasedPlugin',
]
