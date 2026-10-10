#!/usr/bin/env bash
#
# One-time AWS setup: S3 bucket, DynamoDB table, ECR repo, and the two IAM roles
# App Runner needs. Idempotent — safe to re-run after a partial failure.
#
# App Runner needs *two* roles and conflating them is the usual way a first deploy
# fails:
#   * an ACCESS role (trusted by build.apprunner.amazonaws.com) to pull from ECR, and
#   * an INSTANCE role (trusted by tasks.apprunner.amazonaws.com) that the running
#     container assumes to reach S3 and DynamoDB.
#
# Usage:  ./scripts/aws_bootstrap.sh [bucket-suffix]

set -euo pipefail

REGION="${AWS_REGION:-ap-south-1}"       # Mumbai — nearest region to Delhi
TABLE="${DRAINWATCH_TABLE:-drainwatch-readings}"
REPO="${DRAINWATCH_ECR_REPO:-drainwatch}"
ACCESS_ROLE="DrainWatchAppRunnerECRAccess"
INSTANCE_ROLE="DrainWatchAppRunnerInstance"

ACCOUNT_ID="$(aws sts get-caller-identity --query Account --output text)"
echo "account ${ACCOUNT_ID}  region ${REGION}"

# Bucket names are global, so this one carries a suffix. Resolving it needs care: a bare
# `date +%s` default would mint a NEW bucket on every run, and the instance role's policy
# would then point at the new bucket while a running service still wrote to the old one —
# a silent half-broken deploy. So an existing bucket is discovered and reused.
if [ -n "${DRAINWATCH_BUCKET:-}" ]; then
  BUCKET="${DRAINWATCH_BUCKET}"
elif [ "$#" -ge 1 ]; then
  BUCKET="drainwatch-media-$1"
else
  BUCKET="$(aws s3api list-buckets \
    --query "Buckets[?starts_with(Name, 'drainwatch-media-')].Name | [0]" \
    --output text 2>/dev/null || true)"
  if [ -z "${BUCKET}" ] || [ "${BUCKET}" = "None" ]; then
    BUCKET="drainwatch-media-$(date +%s)"
  else
    echo "reusing existing bucket ${BUCKET}"
  fi
fi

# ---------- S3 ----------
if aws s3api head-bucket --bucket "${BUCKET}" 2>/dev/null; then
  echo "bucket ${BUCKET} exists"
else
  echo "creating bucket ${BUCKET}"
  # us-east-1 rejects a LocationConstraint; every other region requires one.
  if [ "${REGION}" = "us-east-1" ]; then
    aws s3api create-bucket --bucket "${BUCKET}" --region "${REGION}"
  else
    aws s3api create-bucket --bucket "${BUCKET}" --region "${REGION}" \
      --create-bucket-configuration "LocationConstraint=${REGION}"
  fi
  # Bucket stays private; the API hands out presigned URLs instead.
  aws s3api put-public-access-block --bucket "${BUCKET}" \
    --public-access-block-configuration \
    "BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true"
fi

# ---------- DynamoDB ----------
# drain_id partition + "<captured_at>#<reading_id>" sort key: one Query returns a
# drain's history already in chronological order, no GSI needed.
if aws dynamodb describe-table --table-name "${TABLE}" --region "${REGION}" >/dev/null 2>&1; then
  echo "table ${TABLE} exists"
else
  echo "creating table ${TABLE}"
  aws dynamodb create-table \
    --table-name "${TABLE}" \
    --region "${REGION}" \
    --attribute-definitions AttributeName=drain_id,AttributeType=S AttributeName=sk,AttributeType=S \
    --key-schema AttributeName=drain_id,KeyType=HASH AttributeName=sk,KeyType=RANGE \
    --billing-mode PAY_PER_REQUEST >/dev/null
  aws dynamodb wait table-exists --table-name "${TABLE}" --region "${REGION}"
fi

# ---------- ECR ----------
if aws ecr describe-repositories --repository-names "${REPO}" --region "${REGION}" >/dev/null 2>&1; then
  echo "ecr repo ${REPO} exists"
else
  echo "creating ecr repo ${REPO}"
  aws ecr create-repository --repository-name "${REPO}" --region "${REGION}" \
    --image-scanning-configuration scanOnPush=false >/dev/null
fi

# ---------- IAM ----------
create_role () {
  local name="$1" principal="$2"
  if aws iam get-role --role-name "${name}" >/dev/null 2>&1; then
    echo "role ${name} exists"
    return
  fi
  echo "creating role ${name}"
  aws iam create-role --role-name "${name}" --assume-role-policy-document "{
    \"Version\": \"2012-10-17\",
    \"Statement\": [{
      \"Effect\": \"Allow\",
      \"Principal\": {\"Service\": \"${principal}\"},
      \"Action\": \"sts:AssumeRole\"
    }]
  }" >/dev/null
}

create_role "${ACCESS_ROLE}" "build.apprunner.amazonaws.com"
aws iam attach-role-policy --role-name "${ACCESS_ROLE}" \
  --policy-arn "arn:aws:iam::aws:policy/service-role/AWSAppRunnerServicePolicyForECRAccess"

create_role "${INSTANCE_ROLE}" "tasks.apprunner.amazonaws.com"
aws iam put-role-policy --role-name "${INSTANCE_ROLE}" \
  --policy-name DrainWatchDataAccess \
  --policy-document "{
    \"Version\": \"2012-10-17\",
    \"Statement\": [
      {
        \"Effect\": \"Allow\",
        \"Action\": [\"s3:PutObject\", \"s3:GetObject\"],
        \"Resource\": \"arn:aws:s3:::${BUCKET}/*\"
      },
      {
        \"Effect\": \"Allow\",
        \"Action\": [\"dynamodb:PutItem\", \"dynamodb:Query\", \"dynamodb:Scan\"],
        \"Resource\": \"arn:aws:dynamodb:${REGION}:${ACCOUNT_ID}:table/${TABLE}\"
      }
    ]
  }"

cat <<EOF

Done. Save these — scripts/deploy.sh reads them from the environment:

  export AWS_REGION=${REGION}
  export DRAINWATCH_BUCKET=${BUCKET}
  export DRAINWATCH_TABLE=${TABLE}
  export DRAINWATCH_ECR_REPO=${REPO}
  export DRAINWATCH_ACCESS_ROLE_ARN=arn:aws:iam::${ACCOUNT_ID}:role/${ACCESS_ROLE}
  export DRAINWATCH_INSTANCE_ROLE_ARN=arn:aws:iam::${ACCOUNT_ID}:role/${INSTANCE_ROLE}

IAM role propagation lags by a few seconds; if the first deploy fails on permissions,
wait ~15s and re-run it.
EOF
