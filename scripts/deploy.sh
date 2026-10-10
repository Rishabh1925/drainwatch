#!/usr/bin/env bash
#
# Build, push to ECR, and create-or-update the App Runner service.
# Run scripts/aws_bootstrap.sh first and export what it prints.
#
# Build for linux/amd64 explicitly: an M-series Mac defaults to arm64, and App Runner
# will accept the push and then fail to start the container with an architecture error
# that does not name the architecture. This is the single most common way this deploy
# wastes an hour.

set -euo pipefail

REGION="${AWS_REGION:-ap-south-1}"
REPO="${DRAINWATCH_ECR_REPO:-drainwatch}"
SERVICE="${DRAINWATCH_SERVICE:-drainwatch}"
TAG="${1:-$(date +%Y%m%d-%H%M%S)}"

: "${DRAINWATCH_BUCKET:?run scripts/aws_bootstrap.sh and export its output}"
: "${DRAINWATCH_TABLE:?run scripts/aws_bootstrap.sh and export its output}"
: "${DRAINWATCH_ACCESS_ROLE_ARN:?run scripts/aws_bootstrap.sh and export its output}"
: "${DRAINWATCH_INSTANCE_ROLE_ARN:?run scripts/aws_bootstrap.sh and export its output}"

ACCOUNT_ID="$(aws sts get-caller-identity --query Account --output text)"
REGISTRY="${ACCOUNT_ID}.dkr.ecr.${REGION}.amazonaws.com"
IMAGE="${REGISTRY}/${REPO}:${TAG}"

if [ ! -d checkpoints/best ]; then
  echo "warning: checkpoints/best missing — the container will serve an untrained head." >&2
  mkdir -p checkpoints
fi

echo "==> building ${IMAGE}"
docker build --platform linux/amd64 -f docker/Dockerfile -t "${IMAGE}" .

echo "==> pushing"
aws ecr get-login-password --region "${REGION}" \
  | docker login --username AWS --password-stdin "${REGISTRY}"
docker push "${IMAGE}"

SERVICE_ARN="$(aws apprunner list-services --region "${REGION}" \
  --query "ServiceSummaryList[?ServiceName=='${SERVICE}'].ServiceArn | [0]" --output text)"

SOURCE_CONFIG=$(cat <<EOF
{
  "ImageRepository": {
    "ImageIdentifier": "${IMAGE}",
    "ImageRepositoryType": "ECR",
    "ImageConfiguration": {
      "Port": "8080",
      "RuntimeEnvironmentVariables": {
        "DRAINWATCH_BUCKET": "${DRAINWATCH_BUCKET}",
        "DRAINWATCH_TABLE": "${DRAINWATCH_TABLE}",
        "AWS_REGION": "${REGION}"
      }
    }
  },
  "AutoDeploymentsEnabled": false,
  "AuthenticationConfiguration": {
    "AccessRoleArn": "${DRAINWATCH_ACCESS_ROLE_ARN}"
  }
}
EOF
)

if [ "${SERVICE_ARN}" = "None" ] || [ -z "${SERVICE_ARN}" ]; then
  echo "==> creating App Runner service ${SERVICE}"
  # 1 vCPU / 2 GB: SegFormer-b0 on CPU needs the memory headroom more than the cores.
  # Given numerically rather than as "1 vCPU"/"2 GB" — both are valid to the API, but the
  # CLI's shorthand parser splits on commas and a value containing a space is one more
  # thing that can go wrong on the one day this script has to work first time.
  SERVICE_ARN="$(aws apprunner create-service \
    --region "${REGION}" \
    --service-name "${SERVICE}" \
    --source-configuration "${SOURCE_CONFIG}" \
    --instance-configuration "Cpu=1024,Memory=2048,InstanceRoleArn=${DRAINWATCH_INSTANCE_ROLE_ARN}" \
    --health-check-configuration "Protocol=HTTP,Path=/health,Interval=20,Timeout=5,HealthyThreshold=1,UnhealthyThreshold=5" \
    --query 'Service.ServiceArn' --output text)"
else
  echo "==> updating App Runner service ${SERVICE}"
  aws apprunner update-service \
    --region "${REGION}" \
    --service-arn "${SERVICE_ARN}" \
    --source-configuration "${SOURCE_CONFIG}" >/dev/null
fi

echo "==> waiting for the service to go live (first deploy takes ~5 min)"
while :; do
  STATUS="$(aws apprunner describe-service --region "${REGION}" --service-arn "${SERVICE_ARN}" \
    --query 'Service.Status' --output text)"
  echo "    ${STATUS}"
  case "${STATUS}" in
    RUNNING) break ;;
    CREATE_FAILED|DELETE_FAILED|PAUSED)
      echo "deploy failed (${STATUS}). Check the App Runner event log:" >&2
      echo "  aws apprunner list-operations --region ${REGION} --service-arn ${SERVICE_ARN}" >&2
      exit 1 ;;
  esac
  sleep 20
done

URL="$(aws apprunner describe-service --region "${REGION}" --service-arn "${SERVICE_ARN}" \
  --query 'Service.ServiceUrl' --output text)"

cat <<EOF

Live: https://${URL}

  curl https://${URL}/health
  curl -F file=@drain.jpg -F lat=28.7501 -F lon=77.1177 https://${URL}/analyze
EOF
