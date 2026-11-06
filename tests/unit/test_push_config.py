"""deploy/push-config.sh, against a stand-in gcloud and the real config
check: a config with problems is never uploaded."""

import json
from pathlib import Path

import pytest

from tests.fakes.gcloud import FakeGcloud

SCRIPT = str(Path(__file__).parents[2] / "deploy" / "push-config.sh")
EXAMPLE = Path(__file__).parents[2] / "deploy" / "config.example.json"
PROJECT = "rezerve-assistant"
NUMBER = "123456789012"


@pytest.fixture
def gcloud(tmp_path: Path) -> FakeGcloud:
    fake = FakeGcloud(tmp_path)
    fake.exists(f"projects describe {PROJECT}", "secrets versions describe latest")
    fake.answers(f"projects describe {PROJECT}", NUMBER)
    fake.answers("secrets versions describe latest", "4")
    fake.answers("secrets versions list assistant-config", "4\n3\n2\n1")
    return fake


def config(tmp_path: Path, **overrides: str) -> str:
    """The example as setup-gcp.sh's output and real secrets fill it in."""
    example = json.loads(EXAMPLE.read_text(encoding="utf-8"))
    values = {key: value.replace("REPLACE_ME", "x1") for key, value in example.items()} | {
        "PUBLIC_BASE_URL": f"https://assistant-{NUMBER}.europe-west1.run.app",
        "GCP_PROJECT": PROJECT,
        "TASKS_INVOKER_SA": f"assistant-tasks@{PROJECT}.iam.gserviceaccount.com",
    }
    path = tmp_path / "config.json"
    path.write_text(json.dumps(values | overrides), encoding="utf-8")
    return str(path)


def test_the_config_becomes_the_running_services_and_old_versions_go(
    tmp_path: Path, gcloud: FakeGcloud
) -> None:
    gcloud.exists("run services describe assistant")
    path = config(tmp_path)
    done = gcloud.run(SCRIPT, path, PROJECT=PROJECT)
    assert done.returncode == 0, done.stdout + done.stderr
    assert "The config is version 4." in done.stdout
    assert gcloud.changes() == [
        f"secrets versions add assistant-config --data-file={path} --project={PROJECT} --quiet",
        "run services update assistant --region=europe-west1 "
        f"--project={PROJECT} --update-secrets=/secrets/config.json=assistant-config:4 --quiet",
        "run services update-traffic assistant --to-latest --region=europe-west1 "
        f"--project={PROJECT} --quiet",
        # Version 3 stays for a rollback.
        f"secrets versions destroy 2 --secret=assistant-config --project={PROJECT} --quiet",
        f"secrets versions destroy 1 --secret=assistant-config --project={PROJECT} --quiet",
    ]


def test_after_a_rollback_the_config_pushed_is_the_one_served(
    tmp_path: Path, gcloud: FakeGcloud
) -> None:
    # A rollback pins traffic to a revision, and Cloud Run keeps that split for
    # every later revision. Unless traffic follows the newest again, the new
    # config never serves, and two pushes later the pinned revision's version
    # is destroyed under it.
    gcloud.exists("run services describe assistant")
    done = gcloud.run(SCRIPT, config(tmp_path), PROJECT=PROJECT)
    assert done.returncode == 0, done.stdout + done.stderr
    changes = gcloud.changes()
    [update] = [i for i, c in enumerate(changes) if c.startswith("run services update ")]
    [latest] = [i for i, c in enumerate(changes) if c.startswith("run services update-traffic")]
    [first_destroyed] = [i for i, c in enumerate(changes) if " destroy 2 " in c]
    assert update < latest < first_destroyed
    assert " --to-latest " in changes[latest]


def test_before_the_first_deploy_it_is_only_uploaded(tmp_path: Path, gcloud: FakeGcloud) -> None:
    done = gcloud.run(SCRIPT, config(tmp_path), PROJECT=PROJECT)
    assert done.returncode == 0, done.stdout + done.stderr
    assert not any(call.startswith("run services update") for call in gcloud.changes())


def test_a_config_with_problems_is_never_uploaded(tmp_path: Path, gcloud: FakeGcloud) -> None:
    # Copied from another project, its address would have Cloud Tasks call
    # back a service that is not this one.
    elsewhere = "https://assistant-999.europe-west1.run.app"
    done = gcloud.run(SCRIPT, config(tmp_path, PUBLIC_BASE_URL=elsewhere), PROJECT=PROJECT)
    assert done.returncode != 0
    assert (
        f"PUBLIC_BASE_URL: should be https://assistant-{NUMBER}.europe-west1.run.app" in done.stdout
    )
    assert gcloud.changes() == []
