#!/usr/bin/env bash
# Google Cloud for the assistant (spec §9), set up once. Running it again is
# safe: what exists is kept, and the queue rates, cleanup policy and grants
# are applied again.
#
#   PROJECT=<project id> BILLING_ACCOUNT=<id from `gcloud billing accounts list`> \
#     deploy/setup-gcp.sh
#
# DRY_RUN=1 shows each change and makes none. GITHUB_REPO (default
# Olbioss/rezerve-assistant) is the repository whose main branch deploys.
set -euo pipefail

: "${PROJECT:?Set PROJECT to the Google Cloud project id.}"
: "${BILLING_ACCOUNT:?Set BILLING_ACCOUNT; gcloud billing accounts list shows it.}"
REGION="${REGION:-europe-west1}"
GITHUB_REPO="${GITHUB_REPO:-Olbioss/rezerve-assistant}"
HERE="$(cd "$(dirname "$0")" && pwd)"

SERVICE=assistant
REPOSITORY=assistant
SECRET=assistant-config
RUNTIME="assistant-runtime@$PROJECT.iam.gserviceaccount.com"
TASKS="assistant-tasks@$PROJECT.iam.gserviceaccount.com"
DEPLOYER="assistant-deployer@$PROJECT.iam.gserviceaccount.com"

# A change: made, or with DRY_RUN=1 only shown.
change() {
  if [[ "${DRY_RUN:-}" == 1 ]]; then
    echo "would run: gcloud $*"
  else
    echo "+ gcloud $*"
    gcloud "$@" --quiet --format=none
  fi
}

# Whether a describe finds it.
exists() {
  gcloud "$@" --quiet >/dev/null 2>&1
}

# The project, on the billing account.
if ! exists projects describe "$PROJECT"; then
  change projects create "$PROJECT"
fi
billing="$(gcloud billing projects describe "$PROJECT" --format='value(billingEnabled)' 2>/dev/null || true)"
if [[ "$billing" != True ]]; then
  change billing projects link "$PROJECT" --billing-account="$BILLING_ACCOUNT"
fi
# A new project's number is not known until it exists.
NUMBER="$(gcloud projects describe "$PROJECT" --format='value(projectNumber)' 2>/dev/null || echo PROJECT_NUMBER)"

change services enable run.googleapis.com cloudtasks.googleapis.com \
  artifactregistry.googleapis.com secretmanager.googleapis.com iam.googleapis.com \
  iamcredentials.googleapis.com sts.googleapis.com cloudresourcemanager.googleapis.com \
  billingbudgets.googleapis.com --project="$PROJECT"

# The images, the last three kept: Artifact Registry's free 0.5 GB. Scanning
# would be billed.
if ! exists artifacts repositories describe "$REPOSITORY" --location="$REGION" --project="$PROJECT"; then
  change artifacts repositories create "$REPOSITORY" --location="$REGION" \
    --repository-format=docker --disable-vulnerability-scanning --project="$PROJECT"
fi
change artifacts repositories set-cleanup-policies "$REPOSITORY" --location="$REGION" \
  --policy="$HERE/cleanup-policy.json" --no-dry-run --project="$PROJECT"

# The queues, at spec §9's rates; reminders keep Cloud Tasks' defaults.
if exists tasks queues describe turns --location="$REGION" --project="$PROJECT"; then
  verb=update
else
  verb=create
fi
change tasks queues "$verb" turns --location="$REGION" --max-dispatches-per-second=2 \
  --max-concurrent-dispatches=3 --max-attempts=6 --min-backoff=2s --max-backoff=60s \
  --project="$PROJECT"
if ! exists tasks queues describe reminders --location="$REGION" --project="$PROJECT"; then
  change tasks queues create reminders --location="$REGION" --project="$PROJECT"
fi

account() {
  if ! exists iam service-accounts describe "$1@$PROJECT.iam.gserviceaccount.com" --project="$PROJECT"; then
    change iam service-accounts create "$1" --display-name="$2" --project="$PROJECT"
  fi
}
account assistant-runtime "The assistant service"
account assistant-tasks "Cloud Tasks calling the assistant back"
account assistant-deployer "GitHub Actions deploying the assistant"

# Every setting, as one JSON object; deploy/push-config.sh adds its versions.
if ! exists secrets describe "$SECRET" --project="$PROJECT"; then
  change secrets create "$SECRET" --replication-policy=user-managed --locations="$REGION" \
    --project="$PROJECT"
fi

# The service queues turns and reminders, each call back signed as the tasks
# account, and reads its config.
change projects add-iam-policy-binding "$PROJECT" --member="serviceAccount:$RUNTIME" \
  --role=roles/cloudtasks.enqueuer --condition=None
change iam service-accounts add-iam-policy-binding "$TASKS" --member="serviceAccount:$RUNTIME" \
  --role=roles/iam.serviceAccountUser --project="$PROJECT"
change secrets add-iam-policy-binding "$SECRET" --member="serviceAccount:$RUNTIME" \
  --role=roles/secretmanager.secretAccessor --project="$PROJECT"
# Cloud Tasks mints those tokens itself: its service agent, made now if Google
# has not made it yet, acts as the tasks account.
change beta services identity create --service=cloudtasks.googleapis.com --project="$PROJECT"
change iam service-accounts add-iam-policy-binding "$TASKS" \
  --member="serviceAccount:service-$NUMBER@gcp-sa-cloudtasks.iam.gserviceaccount.com" \
  --role=roles/iam.serviceAccountUser --project="$PROJECT"
# The deployer: the service, the migration job and the service's public access;
# both run as the runtime account; images pushed; the config's version numbers
# read (never its values).
change projects add-iam-policy-binding "$PROJECT" --member="serviceAccount:$DEPLOYER" \
  --role=roles/run.admin --condition=None
change iam service-accounts add-iam-policy-binding "$RUNTIME" --member="serviceAccount:$DEPLOYER" \
  --role=roles/iam.serviceAccountUser --project="$PROJECT"
change artifacts repositories add-iam-policy-binding "$REPOSITORY" --location="$REGION" \
  --member="serviceAccount:$DEPLOYER" --role=roles/artifactregistry.writer --project="$PROJECT"
change secrets add-iam-policy-binding "$SECRET" --member="serviceAccount:$DEPLOYER" \
  --role=roles/secretmanager.viewer --project="$PROJECT"

# GitHub Actions deploys as the deployer, with no key: only from this
# repository's main branch.
POOL="projects/$NUMBER/locations/global/workloadIdentityPools/github"
if ! exists iam workload-identity-pools describe github --location=global --project="$PROJECT"; then
  change iam workload-identity-pools create github --location=global \
    --display-name="GitHub Actions" --project="$PROJECT"
fi
if exists iam workload-identity-pools providers describe github --workload-identity-pool=github \
  --location=global --project="$PROJECT"; then
  verb=update-oidc
else
  verb=create-oidc
fi
change iam workload-identity-pools providers "$verb" github --workload-identity-pool=github \
  --location=global --issuer-uri=https://token.actions.githubusercontent.com \
  --attribute-mapping=google.subject=assertion.sub,attribute.repository=assertion.repository,attribute.ref=assertion.ref \
  --attribute-condition="assertion.repository == '$GITHUB_REPO' && assertion.ref == 'refs/heads/main'" \
  --project="$PROJECT"
change iam service-accounts add-iam-policy-binding "$DEPLOYER" \
  --member="principalSet://iam.googleapis.com/$POOL/attribute.repository/$GITHUB_REPO" \
  --role=roles/iam.workloadIdentityUser --project="$PROJECT"

# An alert at half and all of one unit of the billing account's currency:
# the service is meant to cost nothing.
budgets="$(gcloud billing budgets list --billing-account="$BILLING_ACCOUNT" \
  --filter="displayName=$PROJECT" --format='value(name)' --billing-project="$PROJECT" 2>/dev/null || true)"
if [[ -z "$budgets" ]]; then
  change billing budgets create --billing-account="$BILLING_ACCOUNT" --display-name="$PROJECT" \
    --budget-amount=1 --threshold-rule=percent=0.5 --threshold-rule=percent=1.0 \
    --filter-projects="projects/$PROJECT" --billing-project="$PROJECT"
fi

cat <<DONE

Set up. GitHub, Settings > Secrets and variables > Actions > Variables:
  GCP_PROJECT=$PROJECT
  GCP_WIF_PROVIDER=$POOL/providers/github
deploy/config.json:
  "PUBLIC_BASE_URL": "https://$SERVICE-$NUMBER.$REGION.run.app",
  "GCP_PROJECT": "$PROJECT",
  "TASKS_INVOKER_SA": "$TASKS",
DONE
