#!/usr/bin/env bash
# Cut loci.lincspace.ai and loci.lincinnovations.com over to GCP Cloud Run (loci-0xl0c1, us-west1).
#
# Prerequisites:
#   1. Active AWS CLI session (`aws login`) for the Route 53 hosted zones.
#   2. For loci.lincspace.ai: `gcloud domains verify lincspace.ai` completed once in Search Console.
#      (lincinnovations.com is already verified and mapped on Cloud Run in loci-0xl0c1.)
#   3. The existing AWS App Runner stack is intentionally kept running until cutover is verified.
set -euo pipefail

PROJECT=${PROJECT:-loci-0xl0c1}
REGION=${REGION:-us-west1}
SERVICE=${SERVICE:-loci}
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
    --quiet 2>/dev/null || echo "   (Note: run 'gcloud domains verify lincspace.ai' if lincspace.ai is not yet verified in Search Console)"

echo "3. Updating Route 53 CNAME for loci.lincspace.ai -> ghs.googlehosted.com. (Zone: $LINCSPACE_ZONE)..."
cat > /tmp/loci-gcp-r53.json <<'EOF'
{
  "Comment": "Cut loci.lincspace.ai over to GCP Cloud Run (loci-0xl0c1)",
  "Changes": [
    {
      "Action": "UPSERT",
      "ResourceRecordSet": {
        "Name": "loci.lincspace.ai",
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
    --hosted-zone-id "$LINCSPACE_ZONE" \
    --change-batch file:///tmp/loci-gcp-r53.json

echo "Done. Poll certificate readiness with:"
echo "  gcloud beta run domain-mappings describe --domain=loci.lincspace.ai --region=$REGION --project=$PROJECT"
echo "  gcloud beta run domain-mappings describe --domain=loci.lincinnovations.com --region=$REGION --project=$PROJECT"
