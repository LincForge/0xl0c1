#!/usr/bin/env bash
# Google Cloud mirror of scripts/bootstrap_aws.sh: Cloud Run + Cloud SQL Postgres 16.
#
# Same image, same three tools, same env contract (db.py reads LOCI_DATABASE_URL;
# server.py reads LOCI_PATH_TOKEN and LOCI_PORT). Nothing in the code changes.
# Cloud Run reaches Cloud SQL over the unix socket it mounts at
# /cloudsql/<project>:<region>:<instance>, which psycopg accepts as `?host=`.
#
# This is a SECOND graph, not a replica of the AWS one: it has its own database
# and its own path token. Say so on camera if both are shown.
#
# Order:
#   1. Project + billing + APIs, Artifact Registry repo, Secret Manager entries
#      (path token, database URL), Cloud SQL instance (async, ~8-10 min).
#   2. Build and push the linux/amd64 image.
#   3. IAM for the runtime service account (Cloud SQL client, secret accessor).
#   4. Wait for Cloud SQL, create the `loci` database, deploy Cloud Run with the
#      instance attached and the secrets injected as env vars. Print the URLs.
#
# Idempotent: every create is skipped when the resource already exists. Re-run
# after a failure and it resumes.
#
# Usage:
#   ./scripts/bootstrap_gcp.sh                       # full run
#   IMAGE_ONLY=1 ./scripts/bootstrap_gcp.sh          # rebuild + push + redeploy only
#
# Requires: gcloud (authenticated), docker (running), python3.
set -euo pipefail

PROJECT="${PROJECT:-loci-0xl0c1}"
REGION="${REGION:-us-west1}"
BILLING_ACCOUNT="${BILLING_ACCOUNT:-}"        # e.g. 01383A-XXXXXX-XXXXXX; required only on first run
INSTANCE=loci
SERVICE=loci
AR_REPO=loci
IMAGE="$REGION-docker.pkg.dev/$PROJECT/$AR_REPO/0xl0c1:latest"
CONN="$PROJECT:$REGION:$INSTANCE"

say() { printf '\n== %s ==\n' "$*"; }

# ---- account guard (same idea as bootstrap_aws.sh: name the target, never guess)
ACCOUNT=$(gcloud config get-value account 2>/dev/null)
say "0/4: account guard"
echo "  gcloud account: $ACCOUNT"
echo "  project:        $PROJECT ($REGION)"
if [[ -z "${EXPECTED_PROJECT:-}" ]]; then
  if [[ -t 0 ]]; then
    read -r -p "  Type the project id above to provision into it: " TYPED
    [[ "$TYPED" == "$PROJECT" ]] || { echo "STOP: '$TYPED' != $PROJECT — nothing was created." >&2; exit 1; }
  else
    echo "STOP: non-interactive and EXPECTED_PROJECT unset. Re-run with EXPECTED_PROJECT=$PROJECT." >&2; exit 1
  fi
elif [[ "$EXPECTED_PROJECT" != "$PROJECT" ]]; then
  echo "STOP: EXPECTED_PROJECT=$EXPECTED_PROJECT but PROJECT=$PROJECT." >&2; exit 1
fi

build_and_push() {
  say "2/4: build + push $IMAGE"
  gcloud auth configure-docker "$REGION-docker.pkg.dev" --quiet >/dev/null
  # linux/amd64: Cloud Run is x86_64; an Apple Silicon default build will not start.
  docker build --platform linux/amd64 -t "$IMAGE" "$(dirname "$0")/.."
  docker push "$IMAGE"
}

deploy() {
  say "4/4: deploy Cloud Run service $SERVICE"
  gcloud run deploy "$SERVICE" --project "$PROJECT" --region "$REGION" \
    --image "$IMAGE" --port 8130 --allow-unauthenticated \
    --add-cloudsql-instances "$CONN" \
    --set-secrets "LOCI_DATABASE_URL=loci-db-url:latest,LOCI_PATH_TOKEN=loci-path-token:latest" \
    --min-instances 1 --max-instances 3 --cpu 1 --memory 512Mi --timeout 300 \
    --quiet
  URL=$(gcloud run services describe "$SERVICE" --project "$PROJECT" --region "$REGION" --format='value(status.url)')
  TOKEN=$(gcloud secrets versions access latest --secret loci-path-token --project "$PROJECT")
  {
    echo "MCP=$URL/loci-$TOKEN/mcp"
    echo "VIEWER=$URL/loci-$TOKEN/"
    echo "HEALTH=$URL/health"
    echo "PROJECT=$PROJECT"
    echo "REGION=$REGION"
    echo "IMAGE=$IMAGE"
    echo "SQL=$CONN"
  } > "$(dirname "$0")/../.loci-gcp-url"
  echo "  health: $(curl -fsS "$URL/health" || echo UNREACHABLE)"
  echo "  connector URL written to .loci-gcp-url (gitignored; treat as a password)"
}

if [[ "${IMAGE_ONLY:-}" == "1" ]]; then
  build_and_push; deploy; exit 0
fi

# ---- 1/4: project, billing, APIs, registry, secrets, SQL (async)
say "1/4: project + APIs + registry + secrets + Cloud SQL"
if ! gcloud projects describe "$PROJECT" >/dev/null 2>&1; then
  gcloud projects create "$PROJECT" --name="0xL0C1 Loci" --quiet
fi
if [[ "$(gcloud billing projects describe "$PROJECT" --format='value(billingEnabled)')" != "True" ]]; then
  [[ -n "$BILLING_ACCOUNT" ]] || { echo "STOP: billing not linked and BILLING_ACCOUNT unset." >&2; exit 1; }
  gcloud billing projects link "$PROJECT" --billing-account="$BILLING_ACCOUNT" >/dev/null
fi
gcloud services enable run.googleapis.com sqladmin.googleapis.com artifactregistry.googleapis.com \
  secretmanager.googleapis.com compute.googleapis.com --project "$PROJECT" >/dev/null
gcloud artifacts repositories describe "$AR_REPO" --location "$REGION" --project "$PROJECT" >/dev/null 2>&1 ||
  gcloud artifacts repositories create "$AR_REPO" --repository-format=docker --location "$REGION" --project "$PROJECT"

if ! gcloud secrets describe loci-path-token --project "$PROJECT" >/dev/null 2>&1; then
  python3 -c 'import secrets,string;a=string.ascii_lowercase+string.digits;print("".join(secrets.choice(a) for _ in range(24)),end="")' |
    gcloud secrets create loci-path-token --data-file=- --project "$PROJECT"
fi
if ! gcloud sql instances describe "$INSTANCE" --project "$PROJECT" >/dev/null 2>&1; then
  DBPW=$(python3 -c 'import secrets,string;a=string.ascii_letters+string.digits;print("".join(secrets.choice(a) for _ in range(32)),end="")')
  gcloud sql instances create "$INSTANCE" --project "$PROJECT" --region "$REGION" \
    --database-version=POSTGRES_16 --tier=db-f1-micro --edition=ENTERPRISE --storage-size=10 \
    --root-password="$DBPW" --async --quiet
  printf 'postgresql://postgres:%s@/loci?host=/cloudsql/%s' "$DBPW" "$CONN" |
    gcloud secrets create loci-db-url --data-file=- --project "$PROJECT"
fi

build_and_push

# ---- 3/4: IAM for the runtime SA
say "3/4: IAM"
PN=$(gcloud projects describe "$PROJECT" --format='value(projectNumber)')
SA="$PN-compute@developer.gserviceaccount.com"
for role in roles/cloudsql.client roles/secretmanager.secretAccessor; do
  gcloud projects add-iam-policy-binding "$PROJECT" --member="serviceAccount:$SA" --role="$role" --quiet --format=none
done

# ---- 4/4: wait for SQL, create db, deploy
say "waiting for Cloud SQL $INSTANCE"
until [[ "$(gcloud sql instances describe "$INSTANCE" --project "$PROJECT" --format='value(state)')" == "RUNNABLE" ]]; do
  printf '.'; sleep 20
done; echo
gcloud sql databases describe loci --instance "$INSTANCE" --project "$PROJECT" >/dev/null 2>&1 ||
  gcloud sql databases create loci --instance "$INSTANCE" --project "$PROJECT"

deploy
