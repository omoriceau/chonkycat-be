#!/usr/bin/env bash
set -euo pipefail

# ==============================================================================
# CONFIG — matches chonky VPC setup
# ==============================================================================
VPC_NAME="chonky-vpc"
SUBNET_NAME="chonky-subnet-a"       # bastion goes in subnet-a (any subnet works)
BASTION_NAME="chonky-bastion"
INSTANCE_TYPE="t3.micro"            # free tier eligible
SG_NAME="chonky-bastion-sg"
ROLE_NAME="chonky-bastion-role"
PROFILE_NAME="chonky-bastion-profile"
RDS_SG_NAME="chonky-db-sg"

# ==============================================================================
# HELPERS
# ==============================================================================
log()  { echo "[INFO]  $*"; }
warn() { echo "[WARN]  $*"; }
die()  { echo "[ERROR] $*" >&2; exit 1; }

# ==============================================================================
# 1. LOOK UP EXISTING VPC + SUBNET
# ==============================================================================
log "Looking up VPC '$VPC_NAME'..."
VPC_ID=$(aws ec2 describe-vpcs \
    --filters "Name=tag:Name,Values=$VPC_NAME" \
    --query 'Vpcs[0].VpcId' \
    --output text)
[ "$VPC_ID" = "None" ] || [ -z "$VPC_ID" ] && die "VPC '$VPC_NAME' not found. Run the chonky setup script first."
log "Found VPC: $VPC_ID"

log "Looking up subnet '$SUBNET_NAME'..."
SUBNET_ID=$(aws ec2 describe-subnets \
    --filters \
        "Name=vpc-id,Values=$VPC_ID" \
        "Name=tag:Name,Values=$SUBNET_NAME" \
    --query 'Subnets[0].SubnetId' \
    --output text)
[ "$SUBNET_ID" = "None" ] || [ -z "$SUBNET_ID" ] && die "Subnet '$SUBNET_NAME' not found."
log "Found subnet: $SUBNET_ID"

# ==============================================================================
# 2. IAM ROLE + INSTANCE PROFILE (required for SSM)
# ==============================================================================
log "Checking IAM role '$ROLE_NAME'..."

ROLE_EXISTS=$(aws iam get-role --role-name "$ROLE_NAME" \
    --query 'Role.RoleName' --output text 2>/dev/null || echo "NOT_FOUND")

if [ "$ROLE_EXISTS" = "NOT_FOUND" ]; then
    log "Creating IAM role..."
    aws iam create-role \
        --role-name "$ROLE_NAME" \
        --assume-role-policy-document '{
            "Version": "2012-10-17",
            "Statement": [{
                "Effect": "Allow",
                "Principal": { "Service": "ec2.amazonaws.com" },
                "Action": "sts:AssumeRole"
            }]
        }' > /dev/null

    log "Attaching AmazonSSMManagedInstanceCore policy..."
    aws iam attach-role-policy \
        --role-name "$ROLE_NAME" \
        --policy-arn "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"

    log "Creating instance profile..."
    aws iam create-instance-profile \
        --instance-profile-name "$PROFILE_NAME" > /dev/null

    aws iam add-role-to-instance-profile \
        --instance-profile-name "$PROFILE_NAME" \
        --role-name "$ROLE_NAME"

    log "Waiting for IAM profile to propagate (15s)..."
    sleep 15
else
    log "IAM role already exists — skipping."
fi

# ==============================================================================
# 3. SECURITY GROUP FOR BASTION
#    No inbound rules needed — SSM connects outbound only.
#    We allow all outbound so SSM can reach its endpoints.
# ==============================================================================
log "Checking bastion security group '$SG_NAME'..."
BASTION_SG_ID=$(aws ec2 describe-security-groups \
    --filters \
        "Name=group-name,Values=$SG_NAME" \
        "Name=vpc-id,Values=$VPC_ID" \
    --query 'SecurityGroups[0].GroupId' \
    --output text)

if [ "$BASTION_SG_ID" = "None" ] || [ -z "$BASTION_SG_ID" ]; then
    log "Creating bastion security group..."
    BASTION_SG_ID=$(aws ec2 create-security-group \
        --group-name "$SG_NAME" \
        --description "Bastion for chonky - SSM only, no inbound SSH" \
        --vpc-id "$VPC_ID" \
        --query 'GroupId' \
        --output text)
    log "Created security group: $BASTION_SG_ID"
else
    log "Bastion security group already exists: $BASTION_SG_ID — skipping."
fi

# ==============================================================================
# 4. ALLOW BASTION → RDS (port 5432) IN THE DB SECURITY GROUP
# ==============================================================================
log "Looking up RDS security group '$RDS_SG_NAME'..."
RDS_SG_ID=$(aws ec2 describe-security-groups \
    --filters \
        "Name=group-name,Values=$RDS_SG_NAME" \
        "Name=vpc-id,Values=$VPC_ID" \
    --query 'SecurityGroups[0].GroupId' \
    --output text)
[ "$RDS_SG_ID" = "None" ] || [ -z "$RDS_SG_ID" ] && die "RDS security group '$RDS_SG_NAME' not found."

log "Adding inbound rule: $BASTION_SG_ID -> $RDS_SG_ID:5432 (skipping if already exists)..."
AUTHORIZE_OUTPUT=$(aws ec2 authorize-security-group-ingress \
    --group-id "$RDS_SG_ID" \
    --protocol tcp \
    --port 5432 \
    --source-group "$BASTION_SG_ID" 2>&1) && log "Rule added." \
    || { echo "$AUTHORIZE_OUTPUT" | grep -q "InvalidPermission.Duplicate" \
        && log "Rule already exists - skipping." \
        || die "Failed to authorize ingress: $AUTHORIZE_OUTPUT"; }

# ==============================================================================
# 5. FIND LATEST AMAZON LINUX 2023 AMI (ARM)
# ==============================================================================
log "Finding latest Amazon Linux 2023 x86_64 AMI..."
AMI_ID=$(aws ec2 describe-images \
    --owners amazon \
    --filters \
        "Name=name,Values=al2023-ami-2023*-x86_64" \
        "Name=state,Values=available" \
    --query 'sort_by(Images, &CreationDate)[-1].ImageId' \
    --output text)
[ -z "$AMI_ID" ] && die "Could not find Amazon Linux 2023 ARM AMI."
log "Using AMI: $AMI_ID"

# ==============================================================================
# 6. LAUNCH BASTION INSTANCE
# ==============================================================================
log "Checking for existing bastion instance '$BASTION_NAME'..."
INSTANCE_ID=$(aws ec2 describe-instances \
    --filters \
        "Name=tag:Name,Values=$BASTION_NAME" \
        "Name=instance-state-name,Values=running,stopped,pending" \
    --query 'Reservations[0].Instances[0].InstanceId' \
    --output text)

if [ "$INSTANCE_ID" = "None" ] || [ -z "$INSTANCE_ID" ]; then
    log "Launching bastion instance..."
    INSTANCE_ID=$(aws ec2 run-instances \
        --image-id "$AMI_ID" \
        --instance-type "$INSTANCE_TYPE" \
        --subnet-id "$SUBNET_ID" \
        --security-group-ids "$BASTION_SG_ID" \
        --iam-instance-profile Name="$PROFILE_NAME" \
        --no-associate-public-ip-address \
        --metadata-options "HttpTokens=required,HttpPutResponseHopLimit=1" \
        --tag-specifications "ResourceType=instance,Tags=[{Key=Name,Value=$BASTION_NAME}]" \
        --query 'Instances[0].InstanceId' \
        --output text)
    log "Launched: $INSTANCE_ID — waiting for it to be running..."
    aws ec2 wait instance-running --instance-ids "$INSTANCE_ID"
    log "Instance is running."

    log "Waiting for SSM agent to register (30s)..."
    sleep 30
else
    log "Bastion instance already exists: $INSTANCE_ID — skipping launch."
fi

# ==============================================================================
# 7. OUTPUT
# ==============================================================================
RDS_ENDPOINT=$(aws rds describe-db-instances \
    --db-instance-identifier "chonky-instance" \
    --query 'DBInstances[0].Endpoint.Address' \
    --output text 2>/dev/null || echo "<rds-endpoint>")

log "====== DONE ======"
log ""
log "Bastion instance ID : $INSTANCE_ID"
log "RDS endpoint        : $RDS_ENDPOINT"
log ""
log "--- To start the SSM port-forward tunnel ---"
log ""
log "  aws ssm start-session \\"
log "    --target $INSTANCE_ID \\"
log "    --document-name AWS-StartPortForwardingSessionToRemoteHost \\"
log "    --parameters '{\"host\":[\"$RDS_ENDPOINT\"],\"portNumber\":[\"5432\"],\"localPortNumber\":[\"5432\"]}'"
log ""
log "--- Then in a second terminal, load your SQL file ---"
log ""
log "  psql -h localhost -p 5432 -U chonky_admin -d chonky -f your_file.sql"
log ""
log "--- To teardown the bastion when done ---"
log ""
log "  aws ec2 terminate-instances --instance-ids $INSTANCE_ID"