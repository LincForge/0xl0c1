#!/usr/bin/env bash
# loci.lincspace.ai -> App Runner. Compute in LINC (profile linc), DNS in personal (default profile).
#
# Cross-account on purpose: the App Runner service lives in the LINC account
# (520646548387) but the lincspace.ai hosted zone lives in the personal account,
# which the DEFAULT profile resolves to. Every compute call below carries
# --profile linc; the single route53 call deliberately does not.
set -euo pipefail

DOMAIN=${DOMAIN:-loci.lincspace.ai}
ZONE=${ZONE:-Z08793601NE35IJQTEV9Q}
REGION=${AWS_REGION:-us-west-2}
STACK=${STACK:-loci-0xl0c1}

ARN=$(aws cloudformation describe-stacks --profile linc --region "$REGION" --stack-name "$STACK" \
      --query "Stacks[0].Outputs[?OutputKey=='ServiceArn'].OutputValue" --output text)
[[ -n "$ARN" && "$ARN" != "None" ]] || { echo "STOP: no ServiceArn output on $STACK." >&2; exit 1; }
echo "service: $ARN"

# idempotent-ish: a second run just re-reads the existing association
aws apprunner associate-custom-domain --profile linc --region "$REGION" --service-arn "$ARN" \
    --domain-name "$DOMAIN" --no-enable-www-subdomain >/dev/null 2>&1 || true

# The certificate validation records are minted asynchronously; poll briefly for them.
for _ in $(seq 1 24); do
  OUT=$(aws apprunner describe-custom-domains --profile linc --region "$REGION" --service-arn "$ARN")
  if printf '%s' "$OUT" | python3 -c 'import json,sys; d=json.load(sys.stdin); sys.exit(0 if any(c.get("CertificateValidationRecords") for c in d["CustomDomains"]) else 1)'; then
    break
  fi
  echo "  waiting for certificate validation records..."
  sleep 5
done

TARGET=$(printf '%s' "$OUT" | python3 -c 'import json,sys; print(json.load(sys.stdin)["DNSTarget"])')
echo "dns target: $TARGET"

python3 - "$OUT" "$DOMAIN" "$TARGET" > /tmp/loci-r53.json <<'EOF'
import json, sys
out, domain, target = json.loads(sys.argv[1]), sys.argv[2], sys.argv[3]
changes = [{"Action": "UPSERT", "ResourceRecordSet": {"Name": domain, "Type": "CNAME", "TTL": 300,
            "ResourceRecords": [{"Value": target}]}}]
seen = set()
for cd in out["CustomDomains"]:
    for rec in cd.get("CertificateValidationRecords", []):
        key = (rec["Name"], rec["Type"])
        if key in seen:
            continue
        seen.add(key)
        changes.append({"Action": "UPSERT", "ResourceRecordSet": {"Name": rec["Name"], "Type": rec["Type"],
                        "TTL": 300, "ResourceRecords": [{"Value": rec["Value"]}]}})
print(json.dumps({"Comment": "0xl0c1 custom domain", "Changes": changes}))
EOF

# NOTE: no --profile here. The hosted zone is in the PERSONAL account, which the
# default profile resolves to.
aws route53 change-resource-record-sets --hosted-zone-id "$ZONE" --change-batch file:///tmp/loci-r53.json
echo "records written. poll:"
echo "  aws apprunner describe-custom-domains --profile linc --region $REGION --service-arn $ARN --query 'CustomDomains[].Status'"
