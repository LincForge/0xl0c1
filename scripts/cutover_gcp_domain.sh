#!/usr/bin/env bash
# Cut loci.lincspace.ai and loci.lincinnovations.com over to GCP Cloud Run (loci-0xl0c1, us-west1).
#
# Note on AWS profiles:
#   - Compute stack (loci-0xl0c1) lived in the `linc` AWS account (520646548387) — now deleted.
#   - Route 53 hosted zones (`lincspace.ai` and `lincinnovations.com`) live in the `personal`
#     AWS account (924712643509, `arn:aws:iam::924712643509:user/andrey_admin`).
#   - Authenticate the personal account first: `aws login --profile personal`
set -euo pipefail

PROJECT=${PROJECT:-loci-0xl0c1}
REGION=${REGION:-us-west1}
SERVICE=${SERVICE:-loci}
DNS_PROFILE=${DNS_PROFILE:-personal}
LINCSPACE_ZONE=${LINCSPACE_ZONE:-Z08793601NE35IJQTEV9Q}

echo "1. Ensuring Cloud Run domain mapping for loci.lincinnovations.com..."
gcloud beta run domain-mappings create \
    --project="$PROJECT" \
    --region="$REGION" \
    --service="$SERVICE" \
    --domain="loci.lincinnovations.com" \
    --quiet 2>/dev/null || true

echo "2. Ensuring Cloud Run domain mapping for loci.lincspace.ai..."
gcloud beta run domain-mappings create \
    --project="$PROJECT" \
    --region="$REGION" \
    --service="$SERVICE" \
    --domain="loci.lincspace.ai" \
    --quiet 2>/dev/null || echo "   (Note: run 'gcloud domains verify lincspace.ai' once in Search Console so Cloud Run issues the cert for loci.lincspace.ai)"

upsert_cname() {
  local zone_id="$1"
  local fqdn="$2"
  cat > /tmp/loci-gcp-r53.json <<EOF
{
  "Comment": "Cut ${fqdn} over to GCP Cloud Run (loci-0xl0c1)",
  "Changes": [
    {
      "Action": "UPSERT",
      "ResourceRecordSet": {
        "Name": "${fqdn}",
        "Type": "CNAME",
        "TTL": 300,
        "ResourceRecords": [
          {
            "Value": "ghs.googlehosted.com."
          }
        ]
      }
    }
  ]
}
EOF
  aws route53 change-resource-record-sets \
      --profile "$DNS_PROFILE" \
      --hosted-zone-id "$zone_id" \
      --change-batch file:///tmp/loci-gcp-r53.json
}

echo "3. Updating Route 53 CNAME for loci.lincspace.ai -> ghs.googlehosted.com. (Zone: $LINCSPACE_ZONE)..."
upsert_cname "$LINCSPACE_ZONE" "loci.lincspace.ai"

LINCINNOV_ZONE=$(aws route53 list-hosted-zones-by-name \
    --profile "$DNS_PROFILE" \
    --dns-name "lincinnovations.com." \
    --max-items 1 \
    --query "HostedZones[?Name=='lincinnovations.com.'].Id | [0]" \
    --output text 2>/dev/null | sed 's|/hostedzone/||')

if [[ -n "$LINCINNOV_ZONE" && "$LINCINNOV_ZONE" != "None" ]]; then
  echo "4. Updating Route 53 CNAME for loci.lincinnovations.com -> ghs.googlehosted.com. (Zone: $LINCINNOV_ZONE)..."
  upsert_cname "$LINCINNOV_ZONE" "loci.lincinnovations.com"
fi

echo "Done. Poll certificate readiness with:"
echo "  gcloud beta run domain-mappings describe --domain=loci.lincinnovations.com --region=$REGION --project=$PROJECT"
echo "  gcloud beta run domain-mappings describe --domain=loci.lincspace.ai --region=$REGION --project=$PROJECT"
