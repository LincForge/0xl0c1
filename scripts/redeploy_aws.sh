#!/usr/bin/env bash
# Repeat deployment for the existing App Runner stack.
#
# Unlike bootstrap_aws.sh, this never sets CreateService=false and therefore
# never tears down the running service. It reads the already-gitignored .env,
# stores the two Ambiguous values in Secrets Manager, pushes a unique image tag,
# and updates the existing CloudFormation stack in one rollout.
set -euo pipefail

cd "$(dirname "$0")/.."

REGION=${AWS_REGION:-us-west-2}
STACK=${STACK:-loci-0xl0c1}
ECR_REPO=${ECR_REPO:-0xl0c1}
ENV_FILE=${ENV_FILE:-.env}
TAG=${IMAGE_TAG:-mvp-$(git rev-parse --short HEAD)}

for command in aws docker python3 curl; do
  command -v "$command" >/dev/null || {
    echo "STOP: required command '$command' is not installed." >&2
    exit 1
  }
done

[[ -f "$ENV_FILE" ]] || {
  echo "STOP: $ENV_FILE is missing; copy .env.example and add the demo values." >&2
  exit 1
}

set -a
# shellcheck disable=SC1090
source "$ENV_FILE"
set +a
: "${AMBIGUOUS_API_KEY:?AMBIGUOUS_API_KEY is required in $ENV_FILE}"
: "${LOCI_AMBIGUOUS_SHEET_ID:?LOCI_AMBIGUOUS_SHEET_ID is required in $ENV_FILE}"

ACCOUNT=$(aws sts get-caller-identity --query Account --output text)
CALLER=$(aws sts get-caller-identity --query Arn --output text)
echo "AWS account: $ACCOUNT"
echo "Caller:      $CALLER"
echo "Region:      $REGION"
echo "Stack:       $STACK"

if [[ -n "${EXPECTED_ACCOUNT:-}" ]]; then
  [[ "$EXPECTED_ACCOUNT" == "$ACCOUNT" ]] || {
    echo "STOP: EXPECTED_ACCOUNT=$EXPECTED_ACCOUNT, but AWS resolved account $ACCOUNT." >&2
    exit 1
  }
elif [[ -t 0 ]]; then
  read -r -p "Type the account id above to continue: " CONFIRMED_ACCOUNT
  [[ "$CONFIRMED_ACCOUNT" == "$ACCOUNT" ]] || {
    echo "STOP: account confirmation did not match." >&2
    exit 1
  }
else
  echo "STOP: set EXPECTED_ACCOUNT=$ACCOUNT for a non-interactive deployment." >&2
  exit 1
fi

# Reuse the deployed network parameters rather than rediscovering or replacing
# any RDS/VPC resources.
VPC=$(aws cloudformation describe-stacks --region "$REGION" --stack-name "$STACK" \
  --query "Stacks[0].Parameters[?ParameterKey=='VpcId'].ParameterValue | [0]" --output text)
SUBNETS=$(aws cloudformation describe-stacks --region "$REGION" --stack-name "$STACK" \
  --query "Stacks[0].Parameters[?ParameterKey=='SubnetIds'].ParameterValue | [0]" --output text)
[[ -n "$VPC" && "$VPC" != "None" && -n "$SUBNETS" && "$SUBNETS" != "None" ]] || {
  echo "STOP: could not read VpcId/SubnetIds from stack $STACK." >&2
  exit 1
}

TEMP_DIR=$(mktemp -d -t 0xl0c1-redeploy.XXXXXX)
trap 'rm -rf "$TEMP_DIR"' EXIT
chmod 700 "$TEMP_DIR"
printf '%s' "$AMBIGUOUS_API_KEY" > "$TEMP_DIR/ambiguous-api-key"
printf '%s' "$LOCI_AMBIGUOUS_SHEET_ID" > "$TEMP_DIR/ambiguous-sheet-id"
chmod 600 "$TEMP_DIR/ambiguous-api-key" "$TEMP_DIR/ambiguous-sheet-id"

put_secret() {
  local name=$1 file=$2 arn
  if arn=$(aws secretsmanager describe-secret --region "$REGION" --secret-id "$name" \
      --query ARN --output text 2>/dev/null); then
    aws secretsmanager put-secret-value --region "$REGION" --secret-id "$name" \
      --secret-string "file://$file" >/dev/null
  else
    arn=$(aws secretsmanager create-secret --region "$REGION" --name "$name" \
      --secret-string "file://$file" --query ARN --output text)
  fi
  printf '%s' "$arn"
}

echo "Uploading Ambiguous runtime secrets..."
API_SECRET_ARN=$(put_secret 0xl0c1/ambiguous-api-key "$TEMP_DIR/ambiguous-api-key")
SHEET_SECRET_ARN=$(put_secret 0xl0c1/ambiguous-sheet-id "$TEMP_DIR/ambiguous-sheet-id")

REPO_URI="$ACCOUNT.dkr.ecr.$REGION.amazonaws.com/$ECR_REPO"
echo "Building $REPO_URI:$TAG..."
aws ecr get-login-password --region "$REGION" |
  docker login --username AWS --password-stdin "$ACCOUNT.dkr.ecr.$REGION.amazonaws.com"
docker build --platform linux/amd64 --tag "$REPO_URI:$TAG" .
docker push "$REPO_URI:$TAG"

python3 - "$VPC" "$SUBNETS" "$TAG" "$API_SECRET_ARN" "$SHEET_SECRET_ARN" \
  > "$TEMP_DIR/parameters.json" <<'PY'
import json
import sys

vpc, subnets, tag, api_secret, sheet_secret = sys.argv[1:]
print(json.dumps([
    {"ParameterKey": "VpcId", "ParameterValue": vpc},
    {"ParameterKey": "SubnetIds", "ParameterValue": subnets},
    {"ParameterKey": "ImageTag", "ParameterValue": tag},
    {"ParameterKey": "CreateService", "ParameterValue": "true"},
    {"ParameterKey": "AmbiguousApiKeySecretArn", "ParameterValue": api_secret},
    {"ParameterKey": "AmbiguousSheetIdSecretArn", "ParameterValue": sheet_secret},
]))
PY

echo "Updating App Runner through CloudFormation..."
aws cloudformation deploy --region "$REGION" --stack-name "$STACK" \
  --template-file infra/app-runner.yaml \
  --capabilities CAPABILITY_NAMED_IAM \
  --no-fail-on-empty-changeset \
  --parameter-overrides "file://$TEMP_DIR/parameters.json"

SERVICE_URL=$(aws cloudformation describe-stacks --region "$REGION" --stack-name "$STACK" \
  --query "Stacks[0].Outputs[?OutputKey=='ServiceUrl'].OutputValue | [0]" --output text)
TOKEN=$(aws secretsmanager get-secret-value --region "$REGION" \
  --secret-id 0xl0c1/path-token --query SecretString --output text)

echo "Waiting for the new health response..."
for _ in $(seq 1 36); do
  if HEALTH=$(curl --fail --silent --show-error --max-time 10 \
      "https://$SERVICE_URL/health" 2>/dev/null) &&
      [[ "$HEALTH" == *'"backend":"ambiguous_sheets"'* ]] &&
      [[ "$HEALTH" == *'"configured":true'* ]]; then
    echo "$HEALTH"
    echo "viewer: https://$SERVICE_URL/loci-$TOKEN/"
    echo "MCP:    https://$SERVICE_URL/loci-$TOKEN/mcp"
    exit 0
  fi
  sleep 5
done

echo "STOP: deployment completed, but the Ambiguous health response was not ready." >&2
echo "Check App Runner deployment and application logs before a demo take." >&2
exit 1
