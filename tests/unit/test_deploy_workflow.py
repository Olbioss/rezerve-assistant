"""The deploy job in .github/workflows/ci.yml, read as text: what it runs, in
what order."""

from pathlib import Path

WORKFLOW = Path(__file__).parents[2] / ".github" / "workflows" / "ci.yml"


def deploy_job() -> str:
    return WORKFLOW.read_text(encoding="utf-8").split("\n  deploy:\n", 1)[1]


def test_a_push_to_main_serves_the_new_revision_even_after_a_rollback() -> None:
    # A rollback pins traffic, and Cloud Run keeps that split for every later
    # revision: the deploy would go green, /health answered by the old one.
    job = deploy_job()
    deployed = job.index("gcloud run deploy assistant")
    latest = job.index("gcloud run services update-traffic assistant --to-latest")
    health = job.index("/health")
    assert deployed < latest < health
