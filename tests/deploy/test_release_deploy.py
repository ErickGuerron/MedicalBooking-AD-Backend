"""deploy/release-deploy.sh against stub docker and curl: no registry, no VM, no database.

Needs bash, flock and python3, so it runs on Linux (CI and the VMs) and is skipped elsewhere.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "deploy" / "release-deploy.sh"
REPO = "meliodasdev/medicalbooking-backend"
COMPOSE = "/opt/medicalbooking/deploy/vm1/compose.yaml"

pytestmark = pytest.mark.skipif(
    os.name == "nt" or not all(shutil.which(tool) for tool in ("bash", "flock", "python3")),
    reason="needs bash, flock and python3 (Linux)",
)

DOCKER_STUB = """#!/usr/bin/env bash
echo "docker $* IMAGE=${MEDICALBOOKING_IMAGE:-}" >> "$STUB_LOG"
case "$1" in
  pull) exit 0 ;;
  images) cat "$STUB_IMAGES"; exit 0 ;;
  ps) cat "$STUB_CONTAINERS"; exit 0 ;;
  inspect)
    # Container id "ct-<image id>" maps to that image id.
    echo "${@: -1}" | sed "s/^ct-//"; exit 0 ;;
  image)
    if [ "$2" = "prune" ]; then exit 0; fi
    if [ "$2" = "rm" ]; then exit 0; fi
    image="${@: -1}"
    echo "${REPO}@sha256:digest-${image##*:}"
    exit 0 ;;
  compose)
    case "$*" in
      *"migrate run"*)
        if [ -n "${STUB_FAIL_MIGRATE:-}" ]; then exit 1; fi
        exit 0 ;;
      *"up -d"*)
        service="${@: -1}"
        if [ -n "${STUB_FAIL_SERVICE:-}" ] && [ "$service" = "$STUB_FAIL_SERVICE" ] \\
           && [[ "$MEDICALBOOKING_IMAGE" == *"$STUB_FAIL_ON"* ]]; then
          exit 1
        fi
        exit 0 ;;
    esac
    exit 0 ;;
esac
exit 0
"""

CURL_STUB = """#!/usr/bin/env bash
if [ -n "${STUB_HUB_DOWN:-}" ]; then exit 22; fi
cat "$STUB_TAGS_FILE"
"""


@pytest.fixture
def env(tmp_path: Path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name, body in (("docker", DOCKER_STUB), ("curl", CURL_STUB)):
        stub = bin_dir / name
        stub.write_text(body, encoding="utf-8")
        stub.chmod(0o755)
    log = tmp_path / "calls.log"
    tags = tmp_path / "tags.json"
    state = tmp_path / "state"
    images = tmp_path / "images.txt"
    images.write_text("", encoding="utf-8")
    containers = tmp_path / "containers.txt"
    containers.write_text("", encoding="utf-8")
    values = {
        "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
        "STUB_LOG": str(log),
        "STUB_TAGS_FILE": str(tags),
        "STUB_IMAGES": str(images),
        "STUB_CONTAINERS": str(containers),
        "MEDILO_STATE_DIR": str(state),
        "MEDILO_WAIT_TIMEOUT": "1",
        "REPO": REPO,
    }
    return {
        "values": values,
        "log": log,
        "tags": tags,
        "state": state,
        "images": images,
        "containers": containers,
        "tmp": tmp_path,
    }


def _publish(env, names: list[str]) -> None:
    body = {"results": [{"name": name} for name in names], "next": None}
    env["tags"].write_text(json.dumps(body), encoding="utf-8")


def _run(env, *args: str, extra: dict[str, str] | None = None) -> subprocess.CompletedProcess:
    variables = {**os.environ, **env["values"], **(extra or {})}
    # Fixed program and arguments chosen by this test; bash resolves from the test PATH on purpose.
    return subprocess.run(  # noqa: S603
        ["bash", str(SCRIPT), COMPOSE, *args],  # noqa: S607
        env=variables,
        capture_output=True,
        text=True,
        timeout=60,
    )


def _up_calls(env) -> list[tuple[str, str]]:
    """(service, image) of every `compose up` call, in order."""
    result = []
    for call in _calls(env):
        if " up -d " not in call:
            continue
        command, _, image = call.partition(" IMAGE=")
        result.append((command.split()[-1], image))
    return result


def _calls(env) -> list[str]:
    if not env["log"].exists():
        return []
    return env["log"].read_text(encoding="utf-8").splitlines()


def test_deploys_the_highest_semver_release_not_the_latest_pushed(env) -> None:
    _publish(env, ["develop", "1.9.0", "1.10.0", "0.9.9", "develop-3374c5c"])

    result = _run(env)

    assert result.returncode == 0, result.stderr
    assert "pull meliodasdev/medicalbooking-backend:1.10.0" in _calls(env)[0]
    up = _up_calls(env)
    assert [service for service, _ in up] == ["replica-a", "replica-b"]
    assert (env["state"] / "current").read_text().strip() == (f"{REPO}@sha256:digest-1.10.0")


def test_does_nothing_when_the_release_is_already_running(env) -> None:
    _publish(env, ["1.10.0"])
    env["state"].mkdir(parents=True)
    (env["state"] / "current").write_text(f"{REPO}@sha256:digest-1.10.0\n")

    result = _run(env)

    assert result.returncode == 0
    assert "up to date: 1.10.0" in result.stdout
    assert not [call for call in _calls(env) if " up -d " in call]


def test_migrations_run_once_before_any_replica_is_touched(env) -> None:
    _publish(env, ["1.10.0"])

    result = _run(env, "--migrate")

    assert result.returncode == 0, result.stderr
    calls = _calls(env)
    migrate_at = next(i for i, call in enumerate(calls) if "--profile migrate run" in call)
    first_replica_at = next(i for i, call in enumerate(calls) if " up -d " in call)
    assert migrate_at < first_replica_at


def test_a_failed_migration_leaves_the_replicas_alone(env) -> None:
    _publish(env, ["1.10.0"])

    result = _run(env, "--migrate", extra={"STUB_FAIL_MIGRATE": "1"})

    assert result.returncode != 0
    assert "replicas were not touched" in result.stdout
    assert not [call for call in _calls(env) if " up -d " in call]
    assert not (env["state"] / "current").exists()


def test_an_unhealthy_replica_rolls_every_updated_replica_back(env) -> None:
    _publish(env, ["1.9.0", "1.10.0"])
    env["state"].mkdir(parents=True)
    (env["state"] / "current").write_text(f"{REPO}@sha256:digest-1.9.0\n")
    extra = {"STUB_FAIL_SERVICE": "replica-b", "STUB_FAIL_ON": "1.10.0"}

    result = _run(env, extra=extra)

    assert result.returncode != 0
    assert "rolling back to" in result.stdout
    up_calls = _up_calls(env)
    # new on replica-a, new on replica-b (fails), then back to the running 1.9.0 on both.
    assert up_calls == [
        ("replica-a", f"{REPO}@sha256:digest-1.10.0"),
        ("replica-b", f"{REPO}@sha256:digest-1.10.0"),
        ("replica-a", f"{REPO}@sha256:digest-1.9.0"),
        ("replica-b", f"{REPO}@sha256:digest-1.9.0"),
    ]
    assert (env["state"] / "current").read_text().strip() == (f"{REPO}@sha256:digest-1.9.0")


def test_no_release_published_is_not_an_error(env) -> None:
    _publish(env, ["develop", "develop-3374c5c"])

    result = _run(env)

    assert result.returncode == 0
    assert "no X.Y.Z release published yet" in result.stdout
    assert not [call for call in _calls(env) if call.startswith("docker pull")]


def test_an_unreachable_registry_fails_without_touching_replicas(env) -> None:
    _publish(env, ["1.10.0"])

    result = _run(env, extra={"STUB_HUB_DOWN": "1"})

    assert result.returncode == 1
    assert "could not read release tags" in result.stdout
    assert not _calls(env)


def _image_lines(env, *lines: str) -> None:
    env["images"].write_text("\n".join(lines) + "\n", encoding="utf-8")


def _running(env, *image_ids: str) -> None:
    env["containers"].write_text("".join(f"ct-{i}\n" for i in image_ids), encoding="utf-8")


def _removed(env) -> list[str]:
    return [call.split()[3] for call in _calls(env) if call.startswith("docker image rm ")]


def test_unused_images_of_the_backend_are_removed_and_used_ones_kept(env) -> None:
    _publish(env, ["1.10.0"])
    _image_lines(
        env,
        f"sha256:old111 {REPO}",
        f"sha256:old222 {REPO}",
        "sha256:third33 other/app",
        "sha256:run444 " + REPO,
    )
    _running(env, "sha256:run444")

    result = _run(env)

    assert result.returncode == 0, result.stderr
    assert _removed(env) == ["sha256:old111", "sha256:old222"]


def test_pruning_also_runs_when_the_release_is_already_running(env) -> None:
    _publish(env, ["1.10.0"])
    env["state"].mkdir(parents=True)
    (env["state"] / "current").write_text(f"{REPO}@sha256:digest-1.10.0\n")
    _image_lines(env, f"sha256:leftover {REPO}")
    _running(env, "sha256:run444")

    result = _run(env)

    assert result.returncode == 0
    assert "up to date: 1.10.0" in result.stdout
    assert _removed(env) == ["sha256:leftover"]


def test_nothing_is_removed_when_the_rollout_fails(env) -> None:
    _publish(env, ["1.10.0"])
    _image_lines(env, f"sha256:old111 {REPO}")
    _running(env, "sha256:run444")

    result = _run(env, extra={"STUB_FAIL_SERVICE": "replica-a", "STUB_FAIL_ON": "1.10.0"})

    assert result.returncode != 0
    assert _removed(env) == []
