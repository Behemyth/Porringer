"""Helpers for test update detection.

Tests for update detection during inspection.

Covers ``SkipReason.UPDATE_AVAILABLE`` / ``ALREADY_INSTALLED`` paths,
version fields on ``SetupActionResult``, and version propagation on
``Upgrade`` results.

Resolution primitives live in ``test_update_detection_resolution.py``.
"""

from dataclasses import replace

from porringer.backend.command.core.presence import inspect_action
from porringer.core.plugin_schema.environment import CheckUpdatesParameters
from porringer.core.schema import Ecosystem, Package, PluginKind
from porringer.schema import (
    InspectionMode,
    SetupAction,
    SetupParameters,
    SkipReason,
    SyncStrategy,
)
from tests.unit.update_detection_helpers import (
    make_action as _make_action,
)
from tests.unit.update_detection_helpers import (
    make_env as _make_env,
)


class TestInspectionUpdateAvailable:
    """The main integration of update detection in the inspection flow."""

    @staticmethod
    async def test_fast_inspect_skips_presence_and_update_checks() -> None:
        """Fast inspect returns action shape without package/update probing."""
        action = _make_action()
        env = _make_env(
            installed=[Package(name='ruff', version='0.8.0')],
            updates=[Package(name='ruff', version='0.9.0')],
        )
        params = SetupParameters(inspection_mode=InspectionMode.FAST)

        result = await inspect_action(action, {'pip': env}, parameters=params)

        env.packages.assert_not_called()
        env.check_updates.assert_not_called()
        assert result.success is True
        assert result.skipped is False
        assert result.installed_version is None

    @staticmethod
    async def test_update_available_when_check_enabled() -> None:
        """When a newer version exists, result is UPDATE_AVAILABLE."""
        action = _make_action()
        env = _make_env(
            installed=[Package(name='ruff', version='0.8.0')],
            updates=[Package(name='ruff', version='0.9.0')],
        )
        envs = {'pip': env}
        params = SetupParameters()

        result = await inspect_action(action, envs, parameters=params)

        assert result.skipped is True
        assert result.skip_reason == SkipReason.UPDATE_AVAILABLE
        assert result.installed_version == '0.8.0'
        assert result.available_version == '0.9.0'
        assert result.message is not None

    @staticmethod
    async def test_already_installed_when_no_update() -> None:
        """When no newer version exists, result is ALREADY_INSTALLED."""
        action = _make_action()
        env = _make_env(
            installed=[Package(name='ruff', version='0.8.0')],
            updates=[Package(name='ruff', version='0.8.0')],
        )
        envs = {'pip': env}
        params = SetupParameters()

        result = await inspect_action(action, envs, parameters=params)

        assert result.skipped is True
        assert result.skip_reason == SkipReason.ALREADY_INSTALLED
        assert result.installed_version == '0.8.0'
        assert result.available_version is None

    @staticmethod
    async def test_always_checks_for_updates() -> None:
        """Update checks are always performed for installed packages."""
        action = _make_action()
        env = _make_env(
            installed=[Package(name='ruff', version='0.8.0')],
            updates=[Package(name='ruff', version='0.9.0')],
        )
        envs = {'pip': env}
        params = SetupParameters()

        result = await inspect_action(action, envs, parameters=params)

        env.check_updates.assert_called_once()
        assert result.skip_reason == SkipReason.UPDATE_AVAILABLE

    @staticmethod
    async def test_already_installed_when_no_parameters() -> None:
        """Legacy callers passing parameters=None still check for updates."""
        action = _make_action()
        env = _make_env(
            installed=[Package(name='ruff', version='0.8.0')],
            updates=[Package(name='ruff', version='0.9.0')],
        )
        envs = {'pip': env}

        result = await inspect_action(action, envs)

        assert result.skipped is True
        assert result.skip_reason == SkipReason.UPDATE_AVAILABLE
        env.check_updates.assert_called_once()

    @staticmethod
    async def test_plugin_no_op_check_updates_falls_back() -> None:
        """Plugins that return [] from check_updates → ALREADY_INSTALLED."""
        action = _make_action()
        env = _make_env(
            installed=[Package(name='ruff', version='0.8.0')],
            updates=[],  # no-op plugin
        )
        envs = {'pip': env}
        params = SetupParameters()

        result = await inspect_action(action, envs, parameters=params)

        assert result.skip_reason == SkipReason.ALREADY_INSTALLED

    @staticmethod
    async def test_per_action_prereleases_forwarded() -> None:
        """Per-action include_prereleases is threaded to check_updates."""
        action = replace(_make_action(), include_prereleases=True)
        env = _make_env(
            installed=[Package(name='ruff', version='0.8.0')],
            updates=[Package(name='ruff', version='0.9.0a1')],
        )
        envs = {'pip': env}
        params = SetupParameters()

        await inspect_action(action, envs, parameters=params)

        call_args = env.check_updates.call_args
        check_params: CheckUpdatesParameters = call_args[0][0]
        assert check_params.include_prereleases is True


class TestLatestStrategySkip:
    """Verify that LATEST strategy skips packages already at the latest version."""

    @staticmethod
    async def test_skips_when_at_latest() -> None:
        """LATEST strategy SKIPs with ALREADY_LATEST when no newer version exists."""
        action = _make_action()
        env = _make_env(
            installed=[Package(name='ruff', version='0.8.0')],
            updates=[Package(name='ruff', version='0.8.0')],  # same version → no update
        )
        envs = {'pip': env}
        params = SetupParameters(strategy=SyncStrategy.LATEST)

        result = await inspect_action(action, envs, parameters=params)

        assert result.skipped is True
        assert result.skip_reason == SkipReason.ALREADY_LATEST
        assert result.installed_version == '0.8.0'
        assert result.available_version is None

    @staticmethod
    async def test_upgrades_when_newer_available() -> None:
        """LATEST strategy attempts an UPGRADE when a newer version exists."""
        action = _make_action()
        env = _make_env(
            installed=[Package(name='ruff', version='0.8.0')],
            updates=[Package(name='ruff', version='0.9.0')],
        )
        envs = {'pip': env}
        params = SetupParameters(strategy=SyncStrategy.LATEST)

        result = await inspect_action(action, envs, parameters=params)

        # Inspection maps UPGRADE to a success (non-skipped) result
        assert result.skipped is False
        assert result.success is True

    @staticmethod
    async def test_latest_installs_when_not_present() -> None:
        """LATEST + not installed → INSTALL (not skipped)."""
        action = _make_action()
        env = _make_env(installed=[], updates=[])
        envs = {'pip': env}
        params = SetupParameters(strategy=SyncStrategy.LATEST)

        result = await inspect_action(action, envs, parameters=params)

        assert result.skipped is False
        assert result.success is True

    @staticmethod
    async def test_latest_falls_back_to_upgrade_on_check_error() -> None:
        """LATEST + installed + check_updates raises → UPGRADE (conservative)."""
        action = _make_action()
        env = _make_env(installed=[Package(name='ruff', version='0.8.0')])
        env.check_updates.side_effect = RuntimeError('network error')
        envs = {'pip': env}
        params = SetupParameters(strategy=SyncStrategy.LATEST)

        result = await inspect_action(action, envs, parameters=params)

        # Should not skip — falls back to upgrade attempt
        assert result.skipped is False
        assert result.success is True

    @staticmethod
    async def test_latest_skip_has_no_available_version() -> None:
        """When LATEST skips (already at latest), available_version should be None."""
        action = _make_action()
        env = _make_env(
            installed=[Package(name='ruff', version='0.8.0')],
            updates=[],  # empty → confirmed up-to-date
        )
        envs = {'pip': env}
        params = SetupParameters(strategy=SyncStrategy.LATEST)

        result = await inspect_action(action, envs, parameters=params)

        assert result.skipped is True
        assert result.skip_reason == SkipReason.ALREADY_LATEST
        assert result.available_version is None


class TestInspectionActionDispatch:
    """Verify the top-level inspect_action passes parameters through."""

    @staticmethod
    async def test_parameters_threaded_to_package_action() -> None:
        """Parameters are threaded through to the package action."""
        action = _make_action()
        env = _make_env(
            installed=[Package(name='ruff', version='0.8.0')],
            updates=[Package(name='ruff', version='0.9.0')],
        )
        envs = {'pip': env}
        params = SetupParameters()

        result = await inspect_action(action, envs, parameters=params)

        assert result.skip_reason == SkipReason.UPDATE_AVAILABLE

    @staticmethod
    async def test_project_action_ignores_parameters() -> None:
        """PROJECT actions always return success regardless of parameters."""
        action = SetupAction(
            description='Sync project',
            kind=PluginKind.PROJECT,
            ecosystem=Ecosystem('python'),
        )
        params = SetupParameters()
        result = await inspect_action(action, {}, parameters=params)
        assert result.success is True
        assert result.skipped is False


class TestPrereleasePackagesOverride:
    """Verify that SetupParameters.prerelease_packages mutates actions."""

    @staticmethod
    async def test_override_sets_include_prereleases() -> None:
        """An action whose package is in prerelease_packages gets include_prereleases=True."""
        action = _make_action(name='ruff')
        assert not action.include_prereleases

        env = _make_env(
            installed=[Package(name='ruff', version='0.8.0')],
            updates=[Package(name='ruff', version='0.9.0a1')],
        )
        envs = {'pip': env}

        # Simulate what _load_manifests does: mutate the action
        params = SetupParameters(prerelease_packages={'ruff'})
        if params.prerelease_packages:
            overrides = {n.lower() for n in params.prerelease_packages}
            if action.package is not None and action.package.name.lower() in overrides:
                action = replace(action, include_prereleases=True)

        assert action.include_prereleases

        result = await inspect_action(action, envs, parameters=params)

        call_args = env.check_updates.call_args
        check_params: CheckUpdatesParameters = call_args[0][0]
        assert check_params.include_prereleases is True
        assert result.skip_reason == SkipReason.UPDATE_AVAILABLE

    @staticmethod
    def test_override_case_insensitive() -> None:
        """Override matching is case-insensitive."""
        action = _make_action(name='Ruff')
        params = SetupParameters(prerelease_packages={'ruff'})
        if params.prerelease_packages:
            overrides = {n.lower() for n in params.prerelease_packages}
            if action.package is not None and action.package.name.lower() in overrides:
                action = replace(action, include_prereleases=True)
        assert action.include_prereleases is True

    @staticmethod
    def test_no_override_when_not_in_set() -> None:
        """Actions whose package is not in the set are left unchanged."""
        action = _make_action(name='ruff')
        params = SetupParameters(prerelease_packages={'black'})
        if params.prerelease_packages:
            overrides = {n.lower() for n in params.prerelease_packages}
            if action.package is not None and action.package.name.lower() in overrides:
                action = replace(action, include_prereleases=True)
        assert action.include_prereleases is False

    @staticmethod
    async def test_override_threaded_to_check_for_newer_version() -> None:
        """Full pipeline: override → action mutation → check_updates receives True."""
        action = _make_action(name='ruff')
        env = _make_env(
            installed=[Package(name='ruff', version='0.8.0')],
            updates=[Package(name='ruff', version='0.9.0a1')],
        )
        envs = {'pip': env}

        # Apply override (mirrors _load_manifests logic)
        action = replace(action, include_prereleases=True)

        params = SetupParameters()
        result = await inspect_action(action, envs, parameters=params)

        call_args = env.check_updates.call_args
        check_params: CheckUpdatesParameters = call_args[0][0]
        assert check_params.include_prereleases is True
        assert result.skip_reason == SkipReason.UPDATE_AVAILABLE
        assert result.available_version == '0.9.0a1'


class TestUpgradeVersionFields:
    """Verify that Upgrade results carry installed and available version."""

    @staticmethod
    async def test_latest_upgrade_has_version_fields() -> None:
        """LATEST + installed + newer version → Upgrade result with version metadata."""
        action = _make_action()
        env = _make_env(
            installed=[Package(name='ruff', version='0.8.0')],
            updates=[Package(name='ruff', version='0.9.0')],
        )
        envs = {'pip': env}
        params = SetupParameters(strategy=SyncStrategy.LATEST)

        result = await inspect_action(action, envs, parameters=params)

        assert result.skipped is False
        assert result.success is True
        assert result.installed_version == '0.8.0'
        assert result.available_version == '0.9.0'

    @staticmethod
    async def test_extras_installed_skips_under_minimal() -> None:
        """Installed package with extras is skipped under MINIMAL strategy."""
        action = _make_action(name='ruff[extra1]')
        env = _make_env(
            installed=[Package(name='ruff', version='0.8.0')],
        )
        envs = {'pip': env}
        params = SetupParameters()

        result = await inspect_action(action, envs, parameters=params)

        assert result.skipped is True
        assert result.success is True
        assert result.installed_version == '0.8.0'
