#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PROJECT_ID="${1:-${GCP_PROJECT_ID:-}}"
REGION="${2:-${GCP_REGION:-europe-west1}}"
REPOSITORY="${ARTIFACT_REPOSITORY:-instatrack}"
SERVICE_NAME="${SERVICE_NAME:-instatrack-crm}"

if [[ -z "$PROJECT_ID" ]]; then
  echo "Usage: ./scripts/deploy_gcp.sh GCP_PROJECT_ID [REGION]"
  exit 2
fi
if ! [[ "$PROJECT_ID" =~ ^[a-z][a-z0-9-]{4,28}[a-z0-9]$ ]]; then
  echo "'$PROJECT_ID' does not look like a valid Google Cloud project ID."
  exit 2
fi
for tool in gcloud terraform python3; do
  if ! command -v "$tool" >/dev/null 2>&1; then
    echo "$tool is required. Run this script in Google Cloud Shell, where these tools are preinstalled."
    exit 1
  fi
done

echo "Deploying InstaTrack to project $PROJECT_ID in $REGION"
gcloud config set project "$PROJECT_ID" >/dev/null
gcloud services enable \
  artifactregistry.googleapis.com \
  cloudbuild.googleapis.com \
  cloudscheduler.googleapis.com \
  iam.googleapis.com \
  run.googleapis.com \
  secretmanager.googleapis.com \
  storage.googleapis.com \
  sqladmin.googleapis.com

if ! gcloud artifacts repositories describe "$REPOSITORY" --location "$REGION" >/dev/null 2>&1; then
  gcloud artifacts repositories create "$REPOSITORY" \
    --repository-format=docker \
    --location="$REGION" \
    --description="InstaTrack container images"
fi

BUILD_SERVICE_ACCOUNT="$(gcloud builds get-default-service-account --project "$PROJECT_ID" --format='value(serviceAccountEmail)')"
if [[ -z "$BUILD_SERVICE_ACCOUNT" ]]; then
  echo "Google Cloud did not return a default Cloud Build service account. Run one build in the project, then retry."
  exit 1
fi
gcloud artifacts repositories add-iam-policy-binding "$REPOSITORY" \
  --location="$REGION" \
  --member="serviceAccount:$BUILD_SERVICE_ACCOUNT" \
  --role="roles/artifactregistry.writer" >/dev/null

TAG="$(git -C "$ROOT_DIR" rev-parse --short HEAD 2>/dev/null || date -u +%Y%m%d%H%M%S)"
API_IMAGE="$REGION-docker.pkg.dev/$PROJECT_ID/$REPOSITORY/api:$TAG"
COLLECTOR_IMAGE="$REGION-docker.pkg.dev/$PROJECT_ID/$REPOSITORY/collector:$TAG"
STATE_BUCKET="$PROJECT_ID-instatrack-tfstate"

if ! gcloud storage buckets describe "gs://$STATE_BUCKET" >/dev/null 2>&1; then
  gcloud storage buckets create "gs://$STATE_BUCKET" \
    --location="$REGION" \
    --uniform-bucket-level-access
  gcloud storage buckets update "gs://$STATE_BUCKET" --versioning
fi

echo "Building the web application..."
gcloud builds submit "$ROOT_DIR" --tag "$API_IMAGE"
echo "Building the Scrapling collector..."
gcloud builds submit "$ROOT_DIR" \
  --config "$ROOT_DIR/cloudbuild.collector.yaml" \
  --substitutions="_IMAGE=$COLLECTOR_IMAGE"

if [[ -z "${TEAM_PASSWORD_HASH:-}" ]]; then
  if [[ -z "${TEAM_PASSWORD:-}" ]]; then
    read -r -s -p "Choose the shared CRM password: " TEAM_PASSWORD
    echo
  fi
  if [[ ${#TEAM_PASSWORD} -lt 8 ]]; then
    echo "The team password must contain at least 8 characters."
    exit 2
  fi
  DEPLOY_VENV="$ROOT_DIR/.deploy/venv"
  if [[ ! -x "$DEPLOY_VENV/bin/python" ]]; then
    python3 -m venv "$DEPLOY_VENV"
    "$DEPLOY_VENV/bin/python" -m pip install --quiet argon2-cffi==25.1.0
  fi
  TEAM_PASSWORD_HASH="$(printf '%s' "$TEAM_PASSWORD" | "$DEPLOY_VENV/bin/python" -c 'from argon2 import PasswordHasher; import sys; print(PasswordHasher().hash(sys.stdin.read()))')"
  unset TEAM_PASSWORD
fi

export TF_VAR_project_id="$PROJECT_ID"
export TF_VAR_region="$REGION"
export TF_VAR_service_name="$SERVICE_NAME"
export TF_VAR_api_image="$API_IMAGE"
export TF_VAR_collector_image="$COLLECTOR_IMAGE"
export TF_VAR_team_password_hash="$TEAM_PASSWORD_HASH"

terraform -chdir="$ROOT_DIR/infra/terraform" init -reconfigure -backend-config="bucket=$STATE_BUCKET"
terraform -chdir="$ROOT_DIR/infra/terraform" apply -auto-approve

# These narrowly scoped grants allow an optional Cloud Build GitHub trigger to
# update only the existing Cloud Run workloads on later pushes.
gcloud projects add-iam-policy-binding "$PROJECT_ID" \
  --member="serviceAccount:$BUILD_SERVICE_ACCOUNT" \
  --role="roles/run.developer" >/dev/null
for runtime_account in \
  "instatrack-api@$PROJECT_ID.iam.gserviceaccount.com" \
  "instatrack-collector@$PROJECT_ID.iam.gserviceaccount.com"; do
  gcloud iam service-accounts add-iam-policy-binding "$runtime_account" \
    --member="serviceAccount:$BUILD_SERVICE_ACCOUNT" \
    --role="roles/iam.serviceAccountUser" >/dev/null
done

APPLICATION_URL="$(terraform -chdir="$ROOT_DIR/infra/terraform" output -raw application_url)"
echo
echo "Deployment complete: $APPLICATION_URL"
echo "The database, secrets, twice-daily schedule, web service, and Scrapling job are ready."
