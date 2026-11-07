"""Setup must restart the configured HA instance, even with a local Docker daemon."""

from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
from unittest.mock import Mock

import pytest


@pytest.mark.parametrize("docker_enabled", [False, True])
@pytest.mark.parametrize("restart_status", [200, 0, 403])
def test_restart_targets_configured_home_assistant(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, docker_enabled: bool, restart_status: int
) -> None:
    script = Path(__file__).resolve().parents[1] / "skill/hermes-voice-setup/scripts/ha_setup.py"
    spec = importlib.util.spec_from_file_location("ha_setup", script)
    assert spec and spec.loader
    setup = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(setup)

    env = tmp_path / ".env"
    env.write_text("API_SERVER_KEY=setup-test-placeholder\n")
    token = tmp_path / "ha_token"
    token.write_text("dummy-token")
    config = tmp_path / "config"
    auth = config / ".storage" / "auth"
    auth.parent.mkdir(parents=True)
    auth.write_text('{"long_lived_access_token": []}')
    source = tmp_path / "component"
    source.mkdir()
    (source / "__init__.py").write_text("")
    args = [
        str(script),
        "--hermes-env",
        str(env),
        "--hermes-url",
        "http://hermes.test/v1",
        "--ha-url",
        "https://ha.test",
        "--token-file",
        str(token),
        "--config-dir",
        str(config),
        "--component-src",
        str(source),
    ]
    if docker_enabled:
        args.append("--docker")
    monkeypatch.setattr(sys, "argv", args)
    monkeypatch.setattr(setup.time, "sleep", lambda _: None)
    monkeypatch.setattr(setup, "wait_ha", Mock(return_value=True))
    docker = Mock(side_effect=AssertionError("must not operate a local container"))
    monkeypatch.setattr(setup, "docker", docker)
    calls = []
    restarted = False

    def http(method: str, url: str, **kwargs: object) -> tuple[int, object]:
        nonlocal restarted
        calls.append((method, url, kwargs))
        if url.endswith("/api/services/homeassistant/restart"):
            restarted = True
            return restart_status, []
        if url.endswith("/flow_handlers"):
            return 200, ["hermes_voice"] if restarted else []
        if url.endswith("/api/onboarding") or url.endswith("/api/config/config_entries/entry"):
            return 200, []
        if method == "GET" and url.endswith("/api/config"):
            return 200, {"version": "2026.10.4"}  # satisfies the minimum-version check
        if method == "GET" and url.endswith("/api/"):
            return 200, {}
        raise AssertionError(f"unexpected HTTP request {method} {url}")

    monkeypatch.setattr(setup, "http", http)
    # Stop after integration loading without creating an agent or calling a model.
    monkeypatch.setattr(setup, "act", lambda _: not restarted)
    if restart_status == 403:
        with pytest.raises(SystemExit):
            setup.main()
    else:
        setup.main()
    assert [(method, url, kwargs.get("token")) for method, url, kwargs in calls if method == "POST"] == [
        ("POST", "https://ha.test/api/services/homeassistant/restart", "dummy-token")
    ]
    docker.assert_not_called()


def test_ha_version_at_least() -> None:
    script = Path(__file__).resolve().parents[1] / "skill/hermes-voice-setup/scripts/ha_setup.py"
    spec = importlib.util.spec_from_file_location("ha_setup_version", script)
    assert spec and spec.loader
    setup = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(setup)

    at_least = setup.ha_version_at_least
    assert at_least("2026.10.0", setup.MIN_HA_VERSION)
    assert at_least("2026.10.4", setup.MIN_HA_VERSION)
    assert at_least("2027.1.0", setup.MIN_HA_VERSION)
    # The whole point: 2026.9 sorts below 2026.10 (string compare would say otherwise).
    assert not at_least("2026.9.2", setup.MIN_HA_VERSION)
    assert not at_least("2026.9", setup.MIN_HA_VERSION)


def test_manifest_declares_homeassistant_floor() -> None:
    import json

    script = Path(__file__).resolve().parents[1] / "skill/hermes-voice-setup/scripts/ha_setup.py"
    spec = importlib.util.spec_from_file_location("ha_setup_floor", script)
    assert spec and spec.loader
    setup = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(setup)

    manifest = json.loads(
        (Path(__file__).resolve().parents[1] / "custom_components/hermes_voice/manifest.json").read_text()
    )
    # The manifest floor and the setup script's constant must agree.
    assert manifest["homeassistant"] == setup.MIN_HA_VERSION == "2026.10.0"
