#!/usr/bin/env bash
set -euo pipefail

# ─── Configuration ───────────────────────────────────────────────
REGION="ap-southeast-2"
REPO_NAME="drainwatch"
CLUSTER_NAME="drainwatch-cluster"
SERVICE_NAME="drainwatch-service"
TASK_FAMILY="drainwatch"
ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
ECR_URI="${ACCOUNT_ID}.dkr.ecr.${REGION}.amazonaws.com/${REPO_NAME}"

echo "═══════════════════════════════════════════"
echo "  DrainWatch — AWS ECS Fargate Deployment"
echo "  Account: ${ACCOUNT_ID}"
echo "  Region:  ${REGION}"
echo "═══════════════════════════════════════════"

# ─── Step 1: Create ECR Repository ──────────────────────────────
echo ""
echo "▶ Step 1: ECR Repository"
aws ecr describe-repositories --repository-names ${REPO_NAME} --region ${REGION} 2>/dev/null \
  && echo "  ✓ Repository already exists" \
  || {
    aws ecr create-repository \
      --repository-name ${REPO_NAME} \
      --region ${REGION} \
      --image-scanning-configuration scanOnPush=true \
      --output text
    echo "  ✓ Repository created"
  }

# ─── Step 2: Docker Login to ECR ────────────────────────────────
echo ""
echo "▶ Step 2: Docker Login"
aws ecr get-login-password --region ${REGION} | \
  docker login --username AWS --password-stdin ${ACCOUNT_ID}.dkr.ecr.${REGION}.amazonaws.com
echo "  ✓ Logged in"

# ─── Step 3: Build & Push ───────────────────────────────────────
echo ""
echo "▶ Step 3: Build & Push (this takes 5-10 min on first run)"
docker build \
  --platform linux/amd64 \
  -f docker/Dockerfile \
  -t ${ECR_URI}:latest \
  .
docker push ${ECR_URI}:latest
echo "  ✓ Image pushed to ECR"

# ─── Step 4: Create CloudWatch Log Group ────────────────────────
echo ""
echo "▶ Step 4: CloudWatch Logs"
aws logs create-log-group --log-group-name /ecs/drainwatch --region ${REGION} 2>/dev/null \
  && echo "  ✓ Log group created" \
  || echo "  ✓ Log group already exists"

# ─── Step 5: Create IAM Roles ──────────────────────────────────
echo ""
echo "▶ Step 5: IAM Roles"

# Task Execution Role (ECR pull + CloudWatch)
aws iam get-role --role-name ecsTaskExecutionRole 2>/dev/null \
  && echo "  ✓ ecsTaskExecutionRole exists" \
  || {
    aws iam create-role \
      --role-name ecsTaskExecutionRole \
      --assume-role-policy-document '{
        "Version":"2012-10-17",
        "Statement":[{"Effect":"Allow","Principal":{"Service":"ecs-tasks.amazonaws.com"},"Action":"sts:AssumeRole"}]
      }' --output text
    aws iam attach-role-policy \
      --role-name ecsTaskExecutionRole \
      --policy-arn arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy
    echo "  ✓ ecsTaskExecutionRole created"
  }

# Task Role (S3 + DynamoDB access for the app)
aws iam get-role --role-name drainwatchTaskRole 2>/dev/null \
  && echo "  ✓ drainwatchTaskRole exists" \
  || {
    aws iam create-role \
      --role-name drainwatchTaskRole \
      --assume-role-policy-document '{
        "Version":"2012-10-17",
        "Statement":[{"Effect":"Allow","Principal":{"Service":"ecs-tasks.amazonaws.com"},"Action":"sts:AssumeRole"}]
      }' --output text
    aws iam put-role-policy \
      --role-name drainwatchTaskRole \
      --policy-name drainwatch-s3-dynamo \
      --policy-document '{
        "Version":"2012-10-17",
        "Statement":[
          {"Effect":"Allow","Action":["s3:PutObject","s3:GetObject","s3:ListBucket"],"Resource":["arn:aws:s3:::drainwatch-*","arn:aws:s3:::drainwatch-*/*"]},
          {"Effect":"Allow","Action":["dynamodb:PutItem","dynamodb:GetItem","dynamodb:Query","dynamodb:Scan","dynamodb:UpdateItem"],"Resource":"arn:aws:dynamodb:*:*:table/drainwatch*"}
        ]
      }'
    echo "  ✓ drainwatchTaskRole created"
  }

# ─── Step 6: Create ECS Cluster ────────────────────────────────
echo ""
echo "▶ Step 6: ECS Cluster"
aws ecs describe-clusters --clusters ${CLUSTER_NAME} --region ${REGION} \
    --query 'clusters[?status==`ACTIVE`].clusterName' --output text 2>/dev/null | grep -q ${CLUSTER_NAME} \
  && echo "  ✓ Cluster already exists" \
  || {
    aws ecs create-cluster --cluster-name ${CLUSTER_NAME} --region ${REGION} --output text
    echo "  ✓ Cluster created"
  }

# ─── Step 7: Register Task Definition ──────────────────────────
echo ""
echo "▶ Step 7: Task Definition"

# Resolve complete Secrets Manager ARNs (including the random suffix).
# ECS requires the full ARN, not the friendly name.
SUPABASE_KEY_ARN=$(aws secretsmanager describe-secret \
  --secret-id drainwatch/supabase-key \
  --region ${REGION} --query 'ARN' --output text 2>/dev/null) || {
  echo "  ✗ Secret 'drainwatch/supabase-key' not found in Secrets Manager."
  echo "    Create it: aws secretsmanager create-secret --name drainwatch/supabase-key --secret-string YOUR_KEY --region ${REGION}"
  exit 1
}
SUPABASE_URL_ARN=$(aws secretsmanager describe-secret \
  --secret-id drainwatch/supabase-url \
  --region ${REGION} --query 'ARN' --output text 2>/dev/null) || {
  echo "  ✗ Secret 'drainwatch/supabase-url' not found in Secrets Manager."
  echo "    Create it: aws secretsmanager create-secret --name drainwatch/supabase-url --secret-string YOUR_URL --region ${REGION}"
  exit 1
}
echo "  ✓ Resolved secret ARNs"

# Grant the execution role permission to retrieve these secrets.
SECRETS_POLICY='{
  "Version":"2012-10-17",
  "Statement":[{
    "Effect":"Allow",
    "Action":["secretsmanager:GetSecretValue"],
    "Resource":["'"${SUPABASE_KEY_ARN}"'","'"${SUPABASE_URL_ARN}"'"]
  }]
}'
aws iam put-role-policy \
  --role-name ecsTaskExecutionRole \
  --policy-name drainwatch-secrets-access \
  --policy-document "${SECRETS_POLICY}" 2>/dev/null
echo "  ✓ Execution role granted secretsmanager:GetSecretValue"

# Replace placeholders in the task definition template.
TASK_DEF=$(cat docker/ecs-task-definition.json | \
  sed "s|ACCOUNT_ID|${ACCOUNT_ID}|g" | \
  sed "s|arn:aws:secretsmanager:${REGION}:${ACCOUNT_ID}:secret:drainwatch/supabase-key|${SUPABASE_KEY_ARN}|g" | \
  sed "s|arn:aws:secretsmanager:${REGION}:${ACCOUNT_ID}:secret:drainwatch/supabase-url|${SUPABASE_URL_ARN}|g")
echo "${TASK_DEF}" | aws ecs register-task-definition \
  --cli-input-json file:///dev/stdin \
  --region ${REGION} --output text --query 'taskDefinition.taskDefinitionArn'
echo "  ✓ Task definition registered"

# ─── Step 8: Create/Update Service ──────────────────────────────
echo ""
echo "▶ Step 8: ECS Service"

# Get default VPC and subnets
DEFAULT_VPC=$(aws ec2 describe-vpcs --filters "Name=isDefault,Values=true" \
  --region ${REGION} --query 'Vpcs[0].VpcId' --output text)
SUBNETS=$(aws ec2 describe-subnets --filters "Name=vpc-id,Values=${DEFAULT_VPC}" \
  --region ${REGION} --query 'Subnets[*].SubnetId' --output text | tr '\t' ',')

# Create security group allowing port 8080
SG_ID=$(aws ec2 describe-security-groups \
  --filters "Name=group-name,Values=drainwatch-sg" "Name=vpc-id,Values=${DEFAULT_VPC}" \
  --region ${REGION} --query 'SecurityGroups[0].GroupId' --output text 2>/dev/null)

if [ "$SG_ID" = "None" ] || [ -z "$SG_ID" ]; then
  SG_ID=$(aws ec2 create-security-group \
    --group-name drainwatch-sg \
    --description "DrainWatch ECS Fargate" \
    --vpc-id ${DEFAULT_VPC} \
    --region ${REGION} --output text --query 'GroupId')
  aws ec2 authorize-security-group-ingress \
    --group-id ${SG_ID} \
    --protocol tcp --port 8080 --cidr 0.0.0.0/0 \
    --region ${REGION}
  echo "  ✓ Security group created: ${SG_ID}"
else
  echo "  ✓ Security group exists: ${SG_ID}"
fi

# Create or update service
aws ecs describe-services --cluster ${CLUSTER_NAME} --services ${SERVICE_NAME} \
  --region ${REGION} --query 'services[?status==`ACTIVE`].serviceName' --output text 2>/dev/null | grep -q ${SERVICE_NAME} \
  && {
    aws ecs update-service \
      --cluster ${CLUSTER_NAME} \
      --service ${SERVICE_NAME} \
      --task-definition ${TASK_FAMILY} \
      --force-new-deployment \
      --region ${REGION} --output text --query 'service.serviceArn'
    echo "  ✓ Service updated (force deploy)"
  } \
  || {
    aws ecs create-service \
      --cluster ${CLUSTER_NAME} \
      --service-name ${SERVICE_NAME} \
      --task-definition ${TASK_FAMILY} \
      --desired-count 1 \
      --launch-type FARGATE \
      --network-configuration "awsvpcConfiguration={subnets=[${SUBNETS}],securityGroups=[${SG_ID}],assignPublicIp=ENABLED}" \
      --region ${REGION} --output text --query 'service.serviceArn'
    echo "  ✓ Service created"
  }

echo ""
echo "═══════════════════════════════════════════"
echo "  ✅ Deployment complete!"
echo ""
echo "  Wait 2-3 min for the task to start, then find the public IP:"
echo "  aws ecs list-tasks --cluster ${CLUSTER_NAME} --region ${REGION} --output text"
echo "  aws ecs describe-tasks --cluster ${CLUSTER_NAME} --tasks <TASK_ARN> --region ${REGION} \\"
echo "    --query 'tasks[0].attachments[0].details[?name==\`networkInterfaceId\`].value' --output text"
echo "  aws ec2 describe-network-interfaces --network-interface-ids <ENI_ID> --region ${REGION} \\"
echo "    --query 'NetworkInterfaces[0].Association.PublicIp' --output text"
echo ""
echo "  Then open: http://<PUBLIC_IP>:8080"
echo "═══════════════════════════════════════════"
