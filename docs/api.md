---
icon: lucide/braces
---

# API Surface

Porringer exposes public Python APIs for applications, downstream clients, and plugin authors. Prefer the imports documented here over console modules or internal backend helpers.

!!! note "Stability policy"

    Import paths documented on this page are intended for downstream applications and plugin authors. Modules under `porringer.backend.command.core`, `porringer.console`, and `porringer.utility` are implementation details unless a symbol is re-exported here or from `porringer.schema`.

## Top-Level Imports

The top-level package re-exports the common application entry points:

| Import | Use |
| --- | --- |
| `API` | Programmatic entry point for sync, package, extension, project, tool, profile, and client operations. |
| `DiscoveredPlugins` | Reusable plugin discovery result for repeated operations. |
| `LocalConfiguration` | Caller-provided cache and configuration paths. |
| `RuntimeContext` | Resolved runtime executable paths. |
| `SetupManifest` | Manifest model. |
| `SetupParameters` | Shared parameters for inspect and run operations. |
| `__version__` | Installed package version string. |
| `PorringerError` | Base exception for Porringer-raised errors. |
| `ManifestError` | Manifest load, parse, or validation failures. |
| `ManifestValidationCode` | Machine-readable manifest diagnostic codes. |
| `SetupError`, `ProcessError`, `PluginError`, `NotSupportedError`, `UpdateError`, `CommandTimeoutError`, `PluginDependencyError` | Specific exception subclasses. |

```python
import asyncio
from pathlib import Path

from porringer import API, LocalConfiguration, SetupParameters


async def main() -> None:
    api = API(LocalConfiguration())
    report = await api.sync.inspect(SetupParameters(paths=Path('.')))
    print(report.summary.actions)


asyncio.run(main())
```

## API Namespaces

`API(LocalConfiguration())` creates namespace objects for backend operations:

| Namespace | Main operations |
| --- | --- |
| `api.sync` | Inspect manifests and run them with optional progress events. Use `inspect_paths([...])` for a stateless per-directory dashboard view. |
| `api.package` | Check package updates through environment plugins. |
| `api.extension` | List, install, upgrade, and uninstall Porringer extension packages. |
| `api.tool` | Check, upgrade, or uninstall packages and tools for the current project. |
| `api.profile` | Resolve, inspect, and run HTTPS setup profiles. |

Long-lived clients should discover plugins once and pass the result into repeated calls:

```python
import asyncio
from pathlib import Path

from porringer import API, LocalConfiguration, SetupParameters
from porringer.schema import InspectionMode


async def main() -> None:
    api = API(LocalConfiguration())
    plugins = await API.discover_plugins(resolve_runtime=False)

    report = await api.sync.inspect(
        SetupParameters(paths=Path('.'), inspection_mode=InspectionMode.FAST),
        plugins=plugins,
    )

    print(report.status, report.summary.actions)


asyncio.run(main())
```

## Schema Package

`porringer.schema` is the flat import surface for CLI, API, and downstream data contracts. It includes manifest models, setup parameters, inspection reports, run reports, progress events, observability envelopes, project/tool/profile reports, and update-check results.

Prefer importing schemas from `porringer.schema` instead of individual schema modules:

```python
from porringer.schema import (
    ActionProgress,
    DirectoryStatus,
    FollowUpAction,
    InspectionMode,
    ResultEnvelope,
    SetupParameters,
    SyncInspectionReport,
    SyncRunReport,
    progress_event_snapshot,
)
```

## Plugin Author Surface

Plugin authors should build on `porringer.core.plugin_schema` and `porringer.core.schema`.

| Surface | Use when implementing |
| --- | --- |
| `Environment` | Package manager plugins such as `pip`, `npm`, `apt`, or `winget`. |
| `ProjectInstaller` | The project-install capability. Mix into a package plugin (e.g. `uv`, `npm`, `pnpm`) so one plugin drives both phases, or subclass `ProjectEnvironment` for a project-only tool such as `pdm` or `poetry`. |
| `ScmEnvironment` | Source-control plugins such as `git`. |
| `RuntimeProvider` | Plugins that discover or install language runtimes. |
| `RuntimeConsumer` | Plugins that can target a resolved runtime executable. |
| `PluginManager` | Plugins that install and upgrade extension packages through a host tool. |
| `ToolBasedPlugin` | Shared base for plugins driven by command-line tools. |

```python
from porringer.core.plugin_schema import Environment
from porringer.core.plugin_schema.environment import CheckUpdatesParameters
from porringer.core.schema import Package


class MyEnvironment(Environment):
    async def check_updates(self, params: CheckUpdatesParameters) -> list[Package]:
        return []
```

Environment plugins must use async package queries and update checks. They should use the helper command methods provided by the base classes instead of blocking subprocess calls.

## Test Support

`porringer.test.*` is a public plugin-testing toolkit shipped with the package. Plugin packages should use it to verify conformance with Porringer's runtime contracts. It does not belong in production code.

| Module | Contents |
| --- | --- |
| `porringer.test.pytest.tests` | Abstract pytest base classes: `EnvironmentUnitTests`, `EnvironmentIntegrationTests`, `ProjectEnvironmentTests`, `ScmEnvironmentTests`, `RuntimeProviderTests`. |
| `porringer.test.pytest.shared` | Abstract base classes and session-scope fixtures. |
| `porringer.test.pytest.variants` | Parametrize helpers for environment, project-environment, and SCM variants. |
| `porringer.test.pytest.plugin` | pytest plugin that registers Porringer fixtures. |
| `porringer.test.mock.environment` | `MockEnvironment`, a lightweight in-process environment stub. |
| `porringer.test.mock.project_environment` | `MockProjectEnvironment` stub. |
| `porringer.test.mock.scm` | `MockScmEnvironment` stub. |
| `porringer.test.mock.subprocess` | `MockSubprocessEnvironment` for command-list output tests without subprocesses. |

A plugin package typically inherits from an abstract test base and supplies the plugin type through a fixture:

```python
# tests/test_my_plugin.py
import pytest
from porringer.test.pytest.tests import EnvironmentUnitTests

from my_package.plugin import MyEnvironment


class TestMyEnvironment(EnvironmentUnitTests[MyEnvironment]):
    @pytest.fixture(name='plugin_type', scope='session')
    def fixture_plugin_type(self) -> type[MyEnvironment]:
        return MyEnvironment
```

The base tests cover command construction, tool availability checks, non-empty command lists, shell-injection resistance, and protocol conformance.

Downstream applications should not depend on `porringer.console.*`, `porringer.backend.command.core.*`, or `porringer.test.*` at runtime.
