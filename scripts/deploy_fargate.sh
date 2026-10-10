#!/usr/bin/env bash
#
# Deploy the pushed ECR image to ECS Fargate with a public IP.
#
# App Runner is blocked by an SCP on this account, so this is the standard replacement:
# a single Fargate task in a public subnet, port 8080 open, reachable at
# http://<public-ip>:8080. No EC2 instance to manage, no load balancer.
#
# Prereqs: aws_bootstrap.sh has run and its vars are exported (bucket, table, region),
# and an image is pushed to the ECR repo. Run from the project root:
#
#     bash scripts/deploy_fargate.sh            # uses the most recently pushed image
#     bash scripts/deploy_fargate.sh 20261007-170835   # or a specific tag

set -euo pipefail

REGION="${AWS_REGION:-ap-southeast-2}"
REPO="${DRAINWATCH_ECR_REPO:-drainwatch}"
CLUSTER="${DRAINWATCH_CLUSTER:-drainwatch}"
BUCKET="${DRAINWATCH_BUCKET:?run scripts/aws_bootstrap.sh and export its output}"
TABLE="${DRAINWATCH_TABLE:?run scripts/aws_bootstrap.sh and export its output}"
EXEC_ROLE="DrainWatchEcsExecRole"
TASK_ROLE="DrainWatchEcsTaskRole"
LOG_GROUP="/drainwatch/fargate"

ACCOUNT_ID="$(aws sts get-caller-identity --query Account --output text)"
REGISTRY="${ACCOUNT_ID}.dkr.ecr.${REGION}.amazonaws.com"

# Image tag: first arg, else the most recently pushed tag in the repo.
TAG="${1:-$(aws ecr describe-images --repository-name "$REPO" --region "$REGION" \
  --query 'sort_by(imageDetails,&imagePushedAt)[-1].imageTags[0]' --output text)}"
IMAGE="${REGISTRY}/${REPO}:${TAG}"
echo "==> image ${IMAGE}"

# ---------- IAM: an execution role (ECR pull + logs) and a task role (S3 + DynamoDB) ----------
# Both must be trusted by ecs-tasks (the App Runner roles are trusted by apprunner, so
# they can't be reused here).
ASSUME='{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Principal":{"Service":"ecs-tasks.amazonaws.com"},"Action":"sts:AssumeRole"}]}'

ensure_role () {
  aws iam get-role --role-name "$1" >/dev/null 2>&1 \
    || aws iam create-role --role-name "$1" --assume-role-policy-document "$ASSUME" >/dev/null
}

echo "==> IAM roles"
ensure_role "$EXEC_ROLE"
aws iam attach-role-policy --role-name "$EXEC_ROLE" \
  --policy-arn arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy

ensure_role "$TASK_ROLE"
aws iam put-role-policy --role-name "$TASK_ROLE" --policy-name DrainWatchData --policy-document "{
  \"Version\":\"2012-10-17\",\"Statement\":[
    {\"Effect\":\"Allow\",\"Action\":[\"s3:PutObject\",\"s3:GetObject\"],\"Resource\":\"arn:aws:s3:::${BUCKET}/*\"},
    {\"Effect\":\"Allow\",\"Action\":[\"dynamodb:PutItem\",\"dynamodb:Query\",\"dynamodb:Scan\"],\"Resource\":\"arn:aws:dynamodb:${REGION}:${ACCOUNT_ID}:table/${TABLE}\"}
  ]}" >/dev/null

EXEC_ROLE_ARN="arn:aws:iam::${ACCOUNT_ID}:role/${EXEC_ROLE}"
TASK_ROLE_ARN="arn:aws:iam::${ACCOUNT_ID}:role/${TASK_ROLE}"

# ---------- networking: default VPC + a public subnet + a security group open on 8080 ----------
echo "==> networking"
VPC_ID="$(aws ec2 describe-vpcs --region "$REGION" --filters Name=isDefault,Values=true \
  --query 'Vpcs[0].VpcId' --output text)"
if [ "$VPC_ID" = "None" ] || [ -z "$VPC_ID" ]; then
  echo "no default VPC in ${REGION} — tell me and I'll add VPC/subnet creation" >&2
  exit 1
fi

SUBNET_ID="$(aws ec2 describe-subnets --region "$REGION" \
  --filters Name=vpc-id,Values="$VPC_ID" Name=map-public-ip-on-launch,Values=true \
  --query 'Subnets[0].SubnetId' --output text)"
if [ "$SUBNET_ID" = "None" ] || [ -z "$SUBNET_ID" ]; then
  SUBNET_ID="$(aws ec2 describe-subnets --region "$REGION" \
    --filters Name=vpc-id,Values="$VPC_ID" --query 'Subnets[0].SubnetId' --output text)"
fi

SG_ID="$(aws ec2 describe-security-groups --region "$REGION" \
  --filters Name=vpc-id,Values="$VPC_ID" Name=group-name,Values=drainwatch-fargate \
  --query 'SecurityGroups[0].GroupId' --output text 2>/dev/null || echo None)"
if [ "$SG_ID" = "None" ] || [ -z "$SG_ID" ]; then
  SG_ID="$(aws ec2 create-security-group --region "$REGION" --group-name drainwatch-fargate \
    --description "DrainWatch Fargate 8080" --vpc-id "$VPC_ID" --query GroupId --output text)"
  aws ec2 authorize-security-group-ingress --region "$REGION" --group-id "$SG_ID" \
    --protocol tcp --port 8080 --cidr 0.0.0.0/0 >/dev/null
fi
echo "    vpc=${VPC_ID} subnet=${SUBNET_ID} sg=${SG_ID}"

# ---------- logs ----------
aws logs create-log-group --region "$REGION" --log-group-name "$LOG_GROUP" 2>/dev/null || true

# ---------- task definition ----------
echo "==> task definition"
cat > /tmp/drainwatch-taskdef.json <<JSON
{
  "family": "drainwatch",
  "networkMode": "awsvpc",
  "requiresCompatibilities": ["FARGATE"],
  "cpu": "1024",
  "memory": "2048",
  "executionRoleArn": "${EXEC_ROLE_ARN}",
  "taskRoleArn": "${TASK_ROLE_ARN}",
  "containerDefinitions": [{
    "name": "drainwatch",
    "image": "${IMAGE}",
    "essential": true,
    "portMappings": [{"containerPort": 8080, "protocol": "tcp"}],
    "environment": [
      {"name": "DRAINWATCH_BUCKET", "value": "${BUCKET}"},
      {"name": "DRAINWATCH_TABLE", "value": "${TABLE}"},
      {"name": "AWS_REGION", "value": "${REGION}"},
      {"name": "PORT", "value": "8080"}
    ],
    "logConfiguration": {
      "logDriver": "awslogs",
      "options": {
        "awslogs-group": "${LOG_GROUP}",
        "awslogs-region": "${REGION}",
        "awslogs-stream-prefix": "drainwatch"
      }
    }
  }]
}
JSON

TASKDEF_ARN="$(aws ecs register-task-definition --region "$REGION" \
  --cli-input-json file:///tmp/drainwatch-taskdef.json \
  --query 'taskDefinition.taskDefinitionArn' --output text)"

# ---------- cluster + run ----------
echo "==> running on Fargate"
aws ecs create-cluster --region "$REGION" --cluster-name "$CLUSTER" >/dev/null 2>&1 || true

TASK_ARN="$(aws ecs run-task --region "$REGION" --cluster "$CLUSTER" \
  --launch-type FARGATE --task-definition "$TASKDEF_ARN" --count 1 \
  --network-configuration "awsvpcConfiguration={subnets=[${SUBNET_ID}],securityGroups=[${SG_ID}],assignPublicIp=ENABLED}" \
  --query 'tasks[0].taskArn' --output text)"
echo "    task ${TASK_ARN}"

echo "==> waiting for the task to reach RUNNING (first pull can take a couple of minutes)"
aws ecs wait tasks-running --region "$REGION" --cluster "$CLUSTER" --tasks "$TASK_ARN" || true

ENI_ID="$(aws ecs describe-tasks --region "$REGION" --cluster "$CLUSTER" --tasks "$TASK_ARN" \
  --query 'tasks[0].attachments[0].details[?name==`networkInterfaceId`].value | [0]' --output text)"
PUBLIC_IP="$(aws ec2 describe-network-interfaces --region "$REGION" --network-interface-ids "$ENI_ID" \
  --query 'NetworkInterfaces[0].Association.PublicIp' --output text 2>/dev/null || echo "")"

if [ -z "$PUBLIC_IP" ] || [ "$PUBLIC_IP" = "None" ]; then
  echo "" >&2
  echo "task started but no public IP yet. Check status with:" >&2
  echo "  aws ecs describe-tasks --region ${REGION} --cluster ${CLUSTER} --tasks ${TASK_ARN} --query 'tasks[0].{last:lastStatus,health:healthStatus,reason:stoppedReason}'" >&2
  exit 1
fi

cat <<EOF

======================================================================
Live:   http://${PUBLIC_IP}:8080
health: http://${PUBLIC_IP}:8080/health
======================================================================

Notes:
- HTTP on a public IP (no load balancer). The IP changes if the task restarts.
- Give it ~30s after RUNNING for the model to load, then hit /health.
- Seed demo data:  python3 scripts/seed_demo.py
- For a stable URL / HTTPS, say so and I'll put it behind a load balancer.
EOF
