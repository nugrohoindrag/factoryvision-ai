#!/usr/bin/env bash
# Build and deploy fv-app to Cloud Run, then wire the Pub/Sub push subscriptions to its URL.
# Usage: PROJECT=<project-id> infra/deploy.sh   (run from the repo root after infra/setup.sh)
set -euo pipefail
: "${PROJECT:?set PROJECT}"
REGION=${REGION:-us-central1}
IMAGE=${REGION}-docker.pkg.dev/${PROJECT}/fv/fv-app:$(git rev-parse --short HEAD)
SA=fv-app@${PROJECT}.iam.gserviceaccount.com
PUSH_SA=fv-push@${PROJECT}.iam.gserviceaccount.com
MODEL_LOCATION=${FV_MODEL_LOCATION:-global}    # the Gemini models are served from "global", not us-central1
MIN_INSTANCES=${MIN_INSTANCES:-0}               # 1 during judging if cold starts hurt (PRD §8)

gcloud builds submit --tag "$IMAGE" .

gcloud run deploy fv-app --image "$IMAGE" --region "$REGION" --service-account "$SA" \
  --allow-unauthenticated --max-instances 3 --min-instances "$MIN_INSTANCES" --memory 2Gi --cpu 2 --timeout 300 \
  --set-env-vars "GOOGLE_CLOUD_PROJECT=$PROJECT,GOOGLE_CLOUD_LOCATION=$MODEL_LOCATION,FV_REGION=$REGION,FV_MODEL_LOCATION=$MODEL_LOCATION,GOOGLE_GENAI_USE_VERTEXAI=TRUE,FV_AI=vertex,FV_WAREHOUSE=bigquery,FV_STORE=firestore,FV_PUBSUB=gcp,FV_PHOTOS_REQUIRE_REVIEW=true"

URL=$(gcloud run services describe fv-app --region "$REGION" --format 'value(status.url)')
gcloud run services update fv-app --region "$REGION" \
  --update-env-vars "FV_PUSH_AUDIENCE=$URL/pubsub/commands,FV_PUSH_SERVICE_ACCOUNT=$PUSH_SA" >/dev/null

# agent-commands -> /pubsub/commands, authenticated with an OIDC token from fv-push.
gcloud pubsub subscriptions describe app-commands >/dev/null 2>&1 || \
  gcloud pubsub subscriptions create app-commands --topic agent-commands \
    --push-endpoint "$URL/pubsub/commands" --push-auth-service-account "$PUSH_SA" --push-auth-token-audience "$URL/pubsub/commands" --ack-deadline 30

echo "fv-app: $URL"
echo "smoke: FV_E2E_BASE_URL=$URL pytest -m e2e backend/tests/e2e"
