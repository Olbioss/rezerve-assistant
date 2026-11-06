"""deploy/setup-gcp.sh, run against a stand-in gcloud: what a new project
gets, what a second run leaves alone, and who may do what."""

from pathlib import Path

import pytest

from tests.fakes.gcloud import FakeGcloud

SCRIPT = str(Path(__file__).parents[2] / "deploy" / "setup-gcp.sh")
PROJECT = "rezerve-assistant"
NUMBER = "123456789012"
BILLING = "0A0A0A-0A0A0A-0A0A0A"
RUNTIME = f"serviceAccount:assistant-runtime@{PROJECT}.iam.gserviceaccount.com"
TASKS = f"assistant-tasks@{PROJECT}.iam.gserviceaccount.com"
DEPLOYER = f"assistant-deployer@{PROJECT}.iam.gserviceaccount.com"
POOL = f"projects/{NUMBER}/locations/global/workloadIdentityPools/github"

# Every grant the service needs, and none more: (on what, to whom, which role).
GRANTS = {
    (f"projects {PROJECT}", RUNTIME, "roles/cloudtasks.enqueuer"),
    (f"service-accounts {TASKS}", RUNTIME, "roles/iam.serviceAccountUser"),
    ("secrets assistant-config", RUNTIME, "roles/secretmanager.secretAccessor"),
    (
        f"service-accounts {TASKS}",
        f"serviceAccount:service-{NUMBER}@gcp-sa-cloudtasks.iam.gserviceaccount.com",
        "roles/iam.serviceAccountUser",
    ),
    (f"projects {PROJECT}", f"serviceAccount:{DEPLOYER}", "roles/run.admin"),
    (
        f"service-accounts assistant-runtime@{PROJECT}.iam.gserviceaccount.com",
        f"serviceAccount:{DEPLOYER}",
        "roles/iam.serviceAccountUser",
    ),
    ("repositories assistant", f"serviceAccount:{DEPLOYER}", "roles/artifactregistry.writer"),
    ("secrets assistant-config", f"serviceAccount:{DEPLOYER}", "roles/secretmanager.viewer"),
    (
        f"service-accounts {DEPLOYER}",
        f"principalSet://iam.googleapis.com/{POOL}/attribute.repository/Olbioss/rezerve-assistant",
        "roles/iam.workloadIdentityUser",
    ),
}

EVERYTHING = [
    f"projects describe {PROJECT}",
    f"billing projects describe {PROJECT}",
    "artifacts repositories describe assistant",
    "tasks queues describe turns",
    "tasks queues describe reminders",
    *[
        f"iam service-accounts describe {name}@{PROJECT}.iam.gserviceaccount.com"
        for name in ("assistant-runtime", "assistant-tasks", "assistant-deployer")
    ],
    "secrets describe assistant-config",
    "iam workload-identity-pools describe github",
    "iam workload-identity-pools providers describe github",
]


@pytest.fixture
def gcloud(tmp_path: Path) -> FakeGcloud:
    fake = FakeGcloud(tmp_path)
    fake.answers(f"projects describe {PROJECT}", NUMBER)
    return fake


def setup(gcloud: FakeGcloud, **env: str) -> str:
    done = gcloud.run(SCRIPT, **({"PROJECT": PROJECT, "BILLING_ACCOUNT": BILLING} | env))
    assert done.returncode == 0, done.stderr
    return done.stdout


def flag(call: str, name: str) -> str:
    return next(part.split("=", 1)[1] for part in call.split(" --")[1:] if part.startswith(name))


def grants(gcloud: FakeGcloud) -> set[tuple[str, str, str]]:
    found: set[tuple[str, str, str]] = set()
    for call in gcloud.changes():
        command, binding, rest = call.partition(" add-iam-policy-binding ")
        if binding:
            kind, target = command.split(" ")[-1], rest.split(" ")[0]
            found.add((f"{kind} {target}", flag(call, "member="), flag(call, "role=")))
    return found


def created(gcloud: FakeGcloud) -> list[str]:
    return [
        call.split(" --")[0]
        for call in gcloud.changes()
        if " create" in call.split(" --")[0] and "identity create" not in call
    ]


def test_a_new_project_gets_everything_the_service_needs(gcloud: FakeGcloud) -> None:
    out = setup(gcloud)
    assert created(gcloud) == [
        f"projects create {PROJECT}",
        "artifacts repositories create assistant",
        "tasks queues create turns",
        "tasks queues create reminders",
        "iam service-accounts create assistant-runtime",
        "iam service-accounts create assistant-tasks",
        "iam service-accounts create assistant-deployer",
        "secrets create assistant-config",
        "iam workload-identity-pools create github",
        "iam workload-identity-pools providers create-oidc github",
        "billing budgets create",
    ]
    changes = gcloud.changes()
    assert (
        f"billing projects link {PROJECT} --billing-account={BILLING} --quiet --format=none"
        in changes
    )
    [turns] = [call for call in changes if call.startswith("tasks queues create turns")]
    # Spec §9: about 2 a second, 3 at once, 6 attempts, backing off 2 to 60 s.
    for rate in (
        "--max-dispatches-per-second=2",
        "--max-concurrent-dispatches=3",
        "--max-attempts=6",
        "--min-backoff=2s",
        "--max-backoff=60s",
    ):
        assert rate in turns
    [registry] = [call for call in changes if call.startswith("artifacts repositories create")]
    assert "--disable-vulnerability-scanning" in registry
    [secret] = [call for call in changes if call.startswith("secrets create")]
    assert "--locations=europe-west1" in secret
    [provider] = [call for call in changes if " providers create-oidc " in call]
    assert flag(provider, "attribute-condition=") == (
        "assertion.repository == 'Olbioss/rezerve-assistant' && assertion.ref == 'refs/heads/main'"
    )
    [budget] = [call for call in changes if call.startswith("billing budgets create")]
    assert flag(budget, "budget-amount=") == "1"
    assert flag(budget, "filter-projects=") == f"projects/{PROJECT}"
    assert grants(gcloud) == GRANTS
    assert f"GCP_WIF_PROVIDER={POOL}/providers/github" in out
    assert f'"PUBLIC_BASE_URL": "https://assistant-{NUMBER}.europe-west1.run.app",' in out


def test_a_second_run_creates_nothing_and_applies_the_rest_again(gcloud: FakeGcloud) -> None:
    gcloud.exists(*EVERYTHING)
    gcloud.answers(f"billing projects describe {PROJECT}", "True")
    gcloud.answers("billing budgets list", "billingAccounts/0A0A0A/budgets/b1")
    setup(gcloud)
    assert created(gcloud) == []
    changes = gcloud.changes()
    assert not any(call.startswith("billing projects link") for call in changes)
    assert any(call.startswith("tasks queues update turns --location") for call in changes)
    assert any(" providers update-oidc github " in call for call in changes)
    assert grants(gcloud) == GRANTS


def test_a_dry_run_changes_nothing_and_shows_each_change(gcloud: FakeGcloud) -> None:
    out = setup(gcloud, DRY_RUN="1")
    assert gcloud.changes() == []
    assert f"would run: gcloud projects create {PROJECT}" in out
    # The project does not exist yet, so neither does its number.
    assert '"PUBLIC_BASE_URL": "https://assistant-PROJECT_NUMBER.europe-west1.run.app",' in out


def test_without_a_billing_account_nothing_is_touched(gcloud: FakeGcloud) -> None:
    done = gcloud.run(SCRIPT, PROJECT=PROJECT)
    assert done.returncode != 0
    assert "BILLING_ACCOUNT" in done.stderr
    assert gcloud.calls() == []
