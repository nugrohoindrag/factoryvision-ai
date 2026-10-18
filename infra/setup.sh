#!/usr/bin/env bash
# One-time Google Cloud setup for Factory Vision AI (idempotent; safe to re-run).
# Usage: PROJECT=<project-id> infra/setup.sh
# Needs: gcloud + bq CLIs, `gcloud auth login` and `gcloud auth application-default login`.
set -euo pipefail
: "${PROJECT:?set PROJECT to the Google Cloud project id}"
REGION=${REGION:-us-central1}
SA=fv-app@${PROJECT}.iam.gserviceaccount.com
PUSH_SA=fv-push@${PROJECT}.iam.gserviceaccount.com

gcloud config set project "$PROJECT"
gcloud config set run/region "$REGION"

echo "== APIs"
# compute: no VM is created, but Cloud Build in new projects builds as the Compute Engine default service account.
gcloud services enable compute.googleapis.com aiplatform.googleapis.com run.googleapis.com cloudbuild.googleapis.com \
  artifactregistry.googleapis.com bigquery.googleapis.com firestore.googleapis.com pubsub.googleapis.com \
  storage.googleapis.com secretmanager.googleapis.com logging.googleapis.com cloudtrace.googleapis.com

echo "== Service accounts (least privilege, Technical Guideline §12)"
gcloud iam service-accounts describe "$SA" >/dev/null 2>&1 || gcloud iam service-accounts create fv-app --display-name "fv-app (Cloud Run)"
gcloud iam service-accounts describe "$PUSH_SA" >/dev/null 2>&1 || gcloud iam service-accounts create fv-push --display-name "Pub/Sub push to fv-app"
for role in roles/aiplatform.user roles/bigquery.dataViewer roles/bigquery.jobUser roles/datastore.user \
            roles/pubsub.publisher roles/logging.logWriter roles/cloudtrace.agent; do
  gcloud projects add-iam-policy-binding "$PROJECT" --member "serviceAccount:$SA" --role "$role" --condition=None >/dev/null
done

echo "== Artifact Registry"
gcloud artifacts repositories describe fv --location "$REGION" >/dev/null 2>&1 || \
  gcloud artifacts repositories create fv --repository-format docker --location "$REGION"

echo "== Firestore (native mode)"
gcloud firestore databases describe --database="(default)" >/dev/null 2>&1 || \
  gcloud firestore databases create --location "$REGION" --type firestore-native

echo "== Cloud Storage"
for b in docs qc-photos twin-assets frames; do
  gcloud storage buckets describe "gs://${PROJECT}-fv-${b}" >/dev/null 2>&1 || \
    gcloud storage buckets create "gs://${PROJECT}-fv-${b}" --location "$REGION" --uniform-bucket-level-access
done
LC=$(mktemp); echo '{"rule":[{"action":{"type":"Delete"},"condition":{"age":7}}]}' > "$LC"
gcloud storage buckets update "gs://${PROJECT}-fv-frames" --lifecycle-file="$LC"

echo "== BigQuery dataset fv: Alembic schema, seed load, BQML model"
PY=${PY:-python}  # e.g. PY=../.venv/Scripts/python on Windows
( cd backend && GOOGLE_CLOUD_PROJECT="$PROJECT" $PY -m simulator.seed.generator > /dev/null \
  && GOOGLE_CLOUD_PROJECT="$PROJECT" FV_REGION="$REGION" $PY -m app.db.load_bigquery )
bq --project_id "$PROJECT" --location "$REGION" query --use_legacy_sql=false < infra/bq_forecast.sql

echo "== Pub/Sub topics (push subscriptions are created by infra/deploy.sh once the URL exists)"
for t in plant-events agent-commands; do
  gcloud pubsub topics describe "$t" >/dev/null 2>&1 || gcloud pubsub topics create "$t"
done
# plant-events -> BigQuery (fv.plant_events), using the table schema.
PUBSUB_SA="service-$(gcloud projects describe "$PROJECT" --format='value(projectNumber)')@gcp-sa-pubsub.iam.gserviceaccount.com"
gcloud projects add-iam-policy-binding "$PROJECT" --member "serviceAccount:$PUBSUB_SA" --role roles/bigquery.dataEditor --condition=None >/dev/null
gcloud pubsub subscriptions describe bq-sink >/dev/null 2>&1 || \
  gcloud pubsub subscriptions create bq-sink --topic plant-events --bigquery-table "$PROJECT:fv.plant_events" --use-table-schema
# Rejected rows go to a dead-letter topic instead of retrying forever (read: gcloud pubsub subscriptions pull plant-events-dlq-pull).
gcloud pubsub topics describe plant-events-dlq >/dev/null 2>&1 || gcloud pubsub topics create plant-events-dlq
gcloud pubsub subscriptions describe plant-events-dlq-pull >/dev/null 2>&1 || gcloud pubsub subscriptions create plant-events-dlq-pull --topic plant-events-dlq
gcloud pubsub topics add-iam-policy-binding plant-events-dlq --member "serviceAccount:$PUBSUB_SA" --role roles/pubsub.publisher >/dev/null
gcloud pubsub subscriptions add-iam-policy-binding bq-sink --member "serviceAccount:$PUBSUB_SA" --role roles/pubsub.subscriber >/dev/null
gcloud pubsub subscriptions update bq-sink --dead-letter-topic plant-events-dlq --max-delivery-attempts 5 >/dev/null

echo "== Budget alerts: create them in the console (Billing > Budgets & alerts) at USD 50 / 100 / 200."
echo "done"
