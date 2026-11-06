#!/usr/bin/env bash
# The production config, checked, then uploaded as the secret's new version;
# a running service moves to it at once. The version before stays enabled for
# a rollback, older ones are destroyed: Secret Manager's free tier is six
# versions.
#
#   PROJECT=<project id> deploy/push-config.sh [deploy/config.json]
set -euo pipefail

: "${PROJECT:?Set PROJECT to the Google Cloud project id.}"
REGION="${REGION:-europe-west1}"
FILE="${1:-deploy/config.json}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SERVICE=assistant
SECRET=assistant-config

NUMBER="$(gcloud projects describe "$PROJECT" --format='value(projectNumber)')"
uv run --project "$ROOT" assistant config check "$FILE" \
  --expect="PUBLIC_BASE_URL=https://$SERVICE-$NUMBER.$REGION.run.app" \
  --expect="GCP_PROJECT=$PROJECT" \
  --expect="GCP_REGION=$REGION" \
  --expect="QUEUE_BACKEND=cloud_tasks" \
  --expect="TASKS_INVOKER_SA=assistant-tasks@$PROJECT.iam.gserviceaccount.com"

gcloud secrets versions add "$SECRET" --data-file="$FILE" --project="$PROJECT" --quiet
VERSION="$(gcloud secrets versions describe latest --secret="$SECRET" --project="$PROJECT" \
  --format='value(name.basename())')"
echo "The config is version $VERSION."

# Before the first deploy there is no service yet: the deploy pins the newest.
if gcloud run services describe "$SERVICE" --region="$REGION" --project="$PROJECT" >/dev/null 2>&1; then
  gcloud run services update "$SERVICE" --region="$REGION" --project="$PROJECT" \
    --update-secrets="/secrets/config.json=$SECRET:$VERSION" --quiet
fi

# Newest first: keep two.
gcloud secrets versions list "$SECRET" --project="$PROJECT" --filter="state=ENABLED" \
  --sort-by=~createTime --format='value(name.basename())' | tail -n +3 |
  while read -r old; do
    gcloud secrets versions destroy "$old" --secret="$SECRET" --project="$PROJECT" --quiet
  done
