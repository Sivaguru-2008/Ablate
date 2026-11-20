#!/usr/bin/env bash
# Deploy Ablate as a Cloud Run Job, run it once, and list the uploaded report.
#
#   gcloud auth login                     # once, in your own terminal
#   PROJECT=my-project BUCKET=my-ablate-reports deploy/cloud_run_job.sh
#
# Optional: REGION (default asia-south1), JOB (default ablate-gate),
#           AGENT=adk with SECRET=<Secret Manager secret holding GOOGLE_API_KEY>,
#           MODEL, K, RPM (passed to the job as ABLATE_* variables).
#
# The image is built remotely by Cloud Build from this directory
# (`--source .`), so no local Docker is needed. Cloud Run, Cloud Build and
# Artifact Registry need a billing account on the project; one short scripted
# run stays inside their free tiers.
set -euo pipefail

: "${PROJECT:?set PROJECT to your Google Cloud project id}"
: "${BUCKET:?set BUCKET to the Cloud Storage bucket for reports (created if missing)}"
REGION="${REGION:-asia-south1}"
JOB="${JOB:-ablate-gate}"
AGENT="${AGENT:-scripted}"
cd "$(dirname "$0")/.."

gcloud config set project "$PROJECT" >/dev/null
echo "== enabling APIs"
gcloud services enable run.googleapis.com cloudbuild.googleapis.com artifactregistry.googleapis.com storage.googleapis.com

echo "== bucket gs://$BUCKET"
gcloud storage buckets describe "gs://$BUCKET" >/dev/null 2>&1 \
  || gcloud storage buckets create "gs://$BUCKET" --location="$REGION" --uniform-bucket-level-access

# The job runs as the project's default compute service account; let it write reports.
PROJECT_NUMBER="$(gcloud projects describe "$PROJECT" --format='value(projectNumber)')"
RUNTIME_SA="${PROJECT_NUMBER}-compute@developer.gserviceaccount.com"
gcloud storage buckets add-iam-policy-binding "gs://$BUCKET" \
  --member="serviceAccount:${RUNTIME_SA}" --role="roles/storage.objectCreator" >/dev/null

ENV_VARS="ABLATE_BUCKET=gs://${BUCKET},ABLATE_AGENT=${AGENT}"
[ -n "${MODEL:-}" ] && ENV_VARS="${ENV_VARS},ABLATE_MODEL=${MODEL}"
[ -n "${K:-}" ] && ENV_VARS="${ENV_VARS},ABLATE_K=${K}"
[ -n "${RPM:-}" ] && ENV_VARS="${ENV_VARS},ABLATE_RPM=${RPM}"
SECRET_ARGS=()
if [ "$AGENT" = "adk" ]; then
  : "${SECRET:?AGENT=adk needs SECRET, a Secret Manager secret holding the Gemini API key}"
  gcloud services enable secretmanager.googleapis.com
  gcloud secrets add-iam-policy-binding "$SECRET" --member="serviceAccount:${RUNTIME_SA}" \
    --role="roles/secretmanager.secretAccessor" >/dev/null
  SECRET_ARGS=(--set-secrets "GOOGLE_API_KEY=${SECRET}:latest")
fi

echo "== deploying job $JOB (remote build)"
gcloud run jobs deploy "$JOB" --source . --region "$REGION" \
  --set-env-vars "$ENV_VARS" "${SECRET_ARGS[@]}" \
  --cpu 2 --memory 2Gi --task-timeout 3600 --max-retries 0

echo "== executing"
gcloud run jobs execute "$JOB" --region "$REGION" --wait

echo "== report"
RUN_ID="$(gcloud storage cat "gs://${BUCKET}/ablate-runs/LATEST")"
gcloud storage ls "gs://${BUCKET}/ablate-runs/${RUN_ID}/"
gcloud storage cat "gs://${BUCKET}/ablate-runs/${RUN_ID}/pr_comment.md"
