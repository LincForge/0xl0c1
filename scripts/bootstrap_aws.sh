#!/usr/bin/env bash
# One-shot, run by the maintainer under their own AWS credentials.
#
# Order (chicken-and-egg: App Runner service creation fails if the ECR image tag
# it points at doesn't exist yet):
#   1. Deploy the stack (us-west-2) with CreateService=false — this creates the
#      ECR repo, the path-token secret, the security groups, the RDS subnet
#      group, the RDS Postgres instance, the VPC connector and the IAM roles,
#      but NOT the App Runner Service/AutoScalingConfiguration (those are
#      Condition-gated on CreateService in infra/app-runner.yaml). The RDS
#      instance is the long pole here: budget 8-12 minutes.
#   2. Build and push a :latest image to the now-existing ECR repo.
#   3. Re-deploy the stack with CreateService=true — this creates the Service,
#      pointing at the image pushed in step 2.
#   4. Print the connector URL (no billing stack: the account already carries the
#      f3-nation-mcp $10 billing alarm in us-east-1).
#
# All `aws cloudformation deploy` calls carry --no-fail-on-empty-changeset:
# without it, re-running this script after a full successful run (e.g. to pick up
# a config-only change, or just by habit) exits non-zero on any stack whose
# desired state already matches reality — a no-op changeset is not a failure.
set -euo pipefail

cd "$(dirname "$0")/.."

REGION=${AWS_REGION:-us-west-2}
STACK=${STACK:-loci-0xl0c1}   # CloudFormation stack names must START WITH A LETTER, so not "0xl0c1".
ECR_REPO=0xl0c1   # ECR repository names must be lowercase; "0xl0c1" qualifies.

# App Runner VPC connectors are not offered in every AZ. In us-west-2 the
# default VPC has a subnet in usw2-az4, which App Runner rejects with
# "Subnets in the following Availability Zones are not supported". Filter by
# AZ *ID* (stable across accounts), not AZ name (which is account-scrambled).
UNSUPPORTED_AZ_IDS=${UNSUPPORTED_AZ_IDS:-usw2-az4}

# ---------------------------------------------------------------------------
# Account guard. Everything below creates real, billable, named resources (an
# ECR repo, IAM roles, Secrets Manager entries, an RDS instance, an App Runner
# service). It runs against whatever the caller's default AWS profile resolves
# to — and the maintainer's shell is known to carry a default profile for a
# DIFFERENT account. So: never guess. Show the identity, make the human name it,
# and refuse outright in a non-interactive shell where nobody is there to read
# the prompt.
# ---------------------------------------------------------------------------
IDENTITY=$(aws sts get-caller-identity --output json)
ACCOUNT=$(printf '%s' "$IDENTITY" | python3 -c 'import json,sys; print(json.load(sys.stdin)["Account"])')
CALLER_ARN=$(printf '%s' "$IDENTITY" | python3 -c 'import json,sys; print(json.load(sys.stdin)["Arn"])')
ALIAS=$(aws iam list-account-aliases --query 'AccountAliases[0]' --output text 2>/dev/null || echo "None")

echo "== 0/4: account guard =="
echo "  Account: $ACCOUNT"
echo "  Alias:   $ALIAS"
echo "  Caller:  $CALLER_ARN"
echo "  Region:  $REGION"

if [[ -n "${EXPECTED_ACCOUNT:-}" ]]; then
  if [[ "$EXPECTED_ACCOUNT" != "$ACCOUNT" ]]; then
    echo "STOP: EXPECTED_ACCOUNT=$EXPECTED_ACCOUNT but the credentials resolve to $ACCOUNT." >&2
    exit 1
  fi
  echo "  Confirmed via EXPECTED_ACCOUNT."
elif [[ -t 0 ]]; then
  read -r -p "  Type the account id above to provision into it: " TYPED
  if [[ "$TYPED" != "$ACCOUNT" ]]; then
    echo "STOP: '$TYPED' does not match $ACCOUNT — nothing was created." >&2
    exit 1
  fi
else
  echo "STOP: non-interactive shell and EXPECTED_ACCOUNT is unset. Re-run with" >&2
  echo "      EXPECTED_ACCOUNT=<account-id> so the target account is explicit." >&2
  exit 1
fi

# Tripwire, checked SEPARATELY from the confirmation above: the failure mode we
# are actually defending against is a confident maintainer confirming the wrong
# account id, which the confirmation alone cannot catch. Give it the DNS zones
# that identify an account this service must never be deployed into, and it will
# stop if it sees one:
#
#   FORBIDDEN_ZONES=example.com,example.org EXPECTED_ACCOUNT=... ./scripts/bootstrap_aws.sh
#
# Leave FORBIDDEN_ZONES unset and the tripwire is skipped (the confirmation above
# still applies).
if [[ -n "${FORBIDDEN_ZONES:-}" ]]; then
  ZONES=$(aws route53 list-hosted-zones --query 'HostedZones[].Name' --output text 2>/dev/null || echo "")
  IFS=',' read -r -a _FORBIDDEN <<< "$FORBIDDEN_ZONES"
  for MARKER in "${_FORBIDDEN[@]}"; do
    MARKER="${MARKER// /}"
    [[ -z "$MARKER" ]] && continue
    if [[ "$ZONES" == *"$MARKER"* ]]; then
      if [[ "${I_KNOW_THIS_IS_THE_RIGHT_ACCOUNT:-}" == "1" ]]; then
        echo "  WARNING: account $ACCOUNT hosts '$MARKER' — proceeding on an explicit override." >&2
      else
        echo "STOP: account $ACCOUNT hosts the Route 53 zone '$MARKER', which you listed in" >&2
        echo "      FORBIDDEN_ZONES as belonging to an account this service must not use." >&2
        echo "      If this really is correct, re-run with I_KNOW_THIS_IS_THE_RIGHT_ACCOUNT=1." >&2
        exit 1
      fi
    fi
  done
fi
echo

# ---------------------------------------------------------------------------
# Resolve the default VPC and its App Runner-eligible subnets.
# ---------------------------------------------------------------------------
VPC=${VPC_ID:-$(aws ec2 describe-vpcs --region "$REGION" --query 'Vpcs[?IsDefault].VpcId' --output text)}
[[ -n "$VPC" && "$VPC" != "None" ]] || { echo "STOP: no default VPC in $REGION; set VPC_ID=." >&2; exit 1; }

SUBNETS=$(aws ec2 describe-subnets --region "$REGION" \
  --filters Name=vpc-id,Values="$VPC" \
  --query 'Subnets[].[SubnetId,AvailabilityZoneId]' --output text \
  | awk -v bad="$UNSUPPORTED_AZ_IDS" 'BEGIN{n=split(bad,b,",");}
      { skip=0; for(i=1;i<=n;i++){ gsub(/ /,"",b[i]); if(b[i]!="" && $2==b[i]) skip=1 }
        if(!skip) printf "%s%s", (c++?",":""), $1 } END{print ""}')
[[ -n "$SUBNETS" ]] || { echo "STOP: no eligible subnets in $VPC." >&2; exit 1; }

echo "  VPC:     $VPC"
echo "  Subnets: $SUBNETS  (excluded AZ ids: $UNSUPPORTED_AZ_IDS)"
echo

# `aws cloudformation deploy --parameter-overrides` shorthand splits on commas,
# so a List<AWS::EC2::Subnet::Id> value has to be either backslash-escaped or
# handed over as a JSON file. The file is unambiguous; use it.
PARAMS_FILE=$(mktemp -t 0xl0c1-params)
trap 'rm -f "$PARAMS_FILE"' EXIT
write_params() {  # $1 = CreateService value
  python3 - "$VPC" "$SUBNETS" "$1" > "$PARAMS_FILE" <<'PY'
import json, sys
vpc, subnets, create = sys.argv[1], sys.argv[2], sys.argv[3]
print(json.dumps([
    {"ParameterKey": "VpcId",         "ParameterValue": vpc},
    {"ParameterKey": "SubnetIds",     "ParameterValue": subnets},
    {"ParameterKey": "CreateService", "ParameterValue": create},
]))
PY
}

echo "== 1/4: deploy ${STACK} (${REGION}) with CreateService=false  [RDS: ~8-12 min] =="
write_params false
aws cloudformation deploy --region "$REGION" --stack-name "$STACK" \
  --template-file infra/app-runner.yaml --capabilities CAPABILITY_NAMED_IAM \
  --no-fail-on-empty-changeset \
  --parameter-overrides "file://$PARAMS_FILE"

REPO_URI="$ACCOUNT.dkr.ecr.$REGION.amazonaws.com/$ECR_REPO"

echo "== 2/4: build + push :latest to $REPO_URI =="
aws ecr get-login-password --region "$REGION" | docker login --username AWS --password-stdin "$ACCOUNT.dkr.ecr.$REGION.amazonaws.com"
# --platform linux/amd64: App Runner runs x86_64 instances; without this flag a
# maintainer building on Apple Silicon pushes an arm64 image App Runner can't run.
docker build --platform linux/amd64 -t "$REPO_URI:latest" .
docker push "$REPO_URI:latest"

echo "== 3/4: re-deploy ${STACK} with CreateService=true (creates the Service) =="
write_params true
aws cloudformation deploy --region "$REGION" --stack-name "$STACK" \
  --template-file infra/app-runner.yaml --capabilities CAPABILITY_NAMED_IAM \
  --no-fail-on-empty-changeset \
  --parameter-overrides "file://$PARAMS_FILE"

echo "== 4/4: connector URL =="
TOKEN=$(aws secretsmanager get-secret-value --region "$REGION" --secret-id 0xl0c1/path-token --query SecretString --output text)
URL=$(aws cloudformation describe-stacks --region "$REGION" --stack-name "$STACK" \
      --query "Stacks[0].Outputs[?OutputKey=='ServiceUrl'].OutputValue" --output text)
echo
echo "connector URL:  https://$URL/loci-$TOKEN/mcp"
echo "viewer:         https://$URL/loci-$TOKEN/"
echo "health:         https://$URL/health"
echo
echo "Stack outputs:"
aws cloudformation describe-stacks --region "$REGION" --stack-name "$STACK" --query 'Stacks[0].Outputs'
echo
echo "Redeploy after a code change is just:"
echo "  docker build --platform linux/amd64 -t $REPO_URI:latest . && docker push $REPO_URI:latest"
echo "(App Runner auto-deploys on a new :latest push.)"
