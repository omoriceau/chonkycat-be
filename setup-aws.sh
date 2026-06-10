#!/usr/bin/env bash
set -euo pipefail

# ==============================================================================
# CONFIG
# ==============================================================================
VPC_NAME="chonky-vpc"
VPC_CIDR="10.0.0.0/16"
SUBNET_A_CIDR="10.0.1.0/24"
SUBNET_B_CIDR="10.0.2.0/24"
SUBNET_GROUP_NAME="chonky-subnet-group"
SG_NAME="chonky-db-sg"
CLUSTER_ID="chonky-cluster"
INSTANCE_ID="chonky-instance"
DB_NAME="chonky"
DB_USER="admin"
DB_PASS="Ch0nky_Secure_P4ss_2026!"
ENGINE_VERSION="8.0.mysql_aurora.3.12.0"

# ==============================================================================
# HELPERS
# ==============================================================================
log()  { echo "[INFO]  $*"; }
warn() { echo "[WARN]  $*"; }
die()  { echo "[ERROR] $*" >&2; exit 1; }

# ==============================================================================
# 1. VPC
# ==============================================================================
log "Checking for existing VPC named '$VPC_NAME'..."

VPC_ID=$(aws ec2 describe-vpcs \
    --filters "Name=tag:Name,Values=$VPC_NAME" \
    --query 'Vpcs[0].VpcId' \
    --output text)

if [ "$VPC_ID" = "None" ] || [ -z "$VPC_ID" ]; then
    log "VPC not found — creating..."
    VPC_ID=$(aws ec2 create-vpc \
        --cidr-block "$VPC_CIDR" \
        --tag-specifications "ResourceType=vpc,Tags=[{Key=Name,Value=$VPC_NAME}]" \
        --query 'Vpc.VpcId' \
        --output text)
    log "Created VPC: $VPC_ID"
else
    log "VPC already exists: $VPC_ID — skipping creation."
fi

# Verify the VPC CIDR matches what we expect (catches partial/conflicting state)
ACTUAL_CIDR=$(aws ec2 describe-vpcs \
    --vpc-ids "$VPC_ID" \
    --query 'Vpcs[0].CidrBlock' \
    --output text)
[ "$ACTUAL_CIDR" = "$VPC_CIDR" ] || die "VPC $VPC_ID has CIDR $ACTUAL_CIDR, expected $VPC_CIDR. Fix manually."

# ==============================================================================
# 2. AVAILABILITY ZONES
# ==============================================================================
AZ_A=$(aws ec2 describe-availability-zones \
    --query 'AvailabilityZones[0].ZoneName' --output text)
AZ_B=$(aws ec2 describe-availability-zones \
    --query 'AvailabilityZones[1].ZoneName' --output text)
log "Using availability zones: $AZ_A, $AZ_B"

# ==============================================================================
# 3. SUBNETS
# ==============================================================================

# --- Subnet A ---
log "Checking for existing subnet '$SUBNET_A_CIDR' in $AZ_A..."
SUBNET_A_ID=$(aws ec2 describe-subnets \
    --filters \
        "Name=vpc-id,Values=$VPC_ID" \
        "Name=cidrBlock,Values=$SUBNET_A_CIDR" \
        "Name=availabilityZone,Values=$AZ_A" \
    --query 'Subnets[0].SubnetId' \
    --output text)

if [ "$SUBNET_A_ID" = "None" ] || [ -z "$SUBNET_A_ID" ]; then
    log "Subnet A not found — creating in $AZ_A..."
    SUBNET_A_ID=$(aws ec2 create-subnet \
        --vpc-id "$VPC_ID" \
        --cidr-block "$SUBNET_A_CIDR" \
        --availability-zone "$AZ_A" \
        --tag-specifications "ResourceType=subnet,Tags=[{Key=Name,Value=chonky-subnet-a}]" \
        --query 'Subnet.SubnetId' \
        --output text)
    log "Created Subnet A: $SUBNET_A_ID"
else
    log "Subnet A already exists: $SUBNET_A_ID — skipping creation."
fi

# --- Subnet B ---
log "Checking for existing subnet '$SUBNET_B_CIDR' in $AZ_B..."
SUBNET_B_ID=$(aws ec2 describe-subnets \
    --filters \
        "Name=vpc-id,Values=$VPC_ID" \
        "Name=cidrBlock,Values=$SUBNET_B_CIDR" \
        "Name=availabilityZone,Values=$AZ_B" \
    --query 'Subnets[0].SubnetId' \
    --output text)

if [ "$SUBNET_B_ID" = "None" ] || [ -z "$SUBNET_B_ID" ]; then
    log "Subnet B not found — creating in $AZ_B..."
    SUBNET_B_ID=$(aws ec2 create-subnet \
        --vpc-id "$VPC_ID" \
        --cidr-block "$SUBNET_B_CIDR" \
        --availability-zone "$AZ_B" \
        --tag-specifications "ResourceType=subnet,Tags=[{Key=Name,Value=chonky-subnet-b}]" \
        --query 'Subnet.SubnetId' \
        --output text)
    log "Created Subnet B: $SUBNET_B_ID"
else
    log "Subnet B already exists: $SUBNET_B_ID — skipping creation."
fi

# ==============================================================================
# 4. RDS SUBNET GROUP
# Explicitly validates it belongs to our VPC — no silent wrong-VPC surprises.
# ==============================================================================
log "Checking for existing RDS subnet group '$SUBNET_GROUP_NAME'..."

EXISTING_SUBNET_GROUP_VPC=$(aws rds describe-db-subnet-groups \
    --db-subnet-group-name "$SUBNET_GROUP_NAME" \
    --query 'DBSubnetGroups[0].VpcId' \
    --output text 2>/dev/null || echo "NOT_FOUND")

if [ "$EXISTING_SUBNET_GROUP_VPC" = "NOT_FOUND" ]; then
    log "Subnet group not found — creating..."
    aws rds create-db-subnet-group \
        --db-subnet-group-name "$SUBNET_GROUP_NAME" \
        --db-subnet-group-description "Subnet group for chonky serverless database" \
        --subnet-ids "$SUBNET_A_ID" "$SUBNET_B_ID"
    log "Created subnet group: $SUBNET_GROUP_NAME"
elif [ "$EXISTING_SUBNET_GROUP_VPC" = "$VPC_ID" ]; then
    log "Subnet group already exists in correct VPC ($VPC_ID) — skipping creation."
else
    die "Subnet group '$SUBNET_GROUP_NAME' exists but belongs to VPC $EXISTING_SUBNET_GROUP_VPC, not $VPC_ID. Delete it manually: aws rds delete-db-subnet-group --db-subnet-group-name $SUBNET_GROUP_NAME"
fi

# ==============================================================================
# 5. SECURITY GROUP
# ==============================================================================
log "Checking for existing security group '$SG_NAME' in VPC $VPC_ID..."

SECURITY_GROUP_ID=$(aws ec2 describe-security-groups \
    --filters \
        "Name=group-name,Values=$SG_NAME" \
        "Name=vpc-id,Values=$VPC_ID" \
    --query 'SecurityGroups[0].GroupId' \
    --output text)

if [ "$SECURITY_GROUP_ID" = "None" ] || [ -z "$SECURITY_GROUP_ID" ]; then
    log "Security group not found — creating..."
    SECURITY_GROUP_ID=$(aws ec2 create-security-group \
        --group-name "$SG_NAME" \
        --description "Firewall for chonky database" \
        --vpc-id "$VPC_ID" \
        --query 'GroupId' \
        --output text)
    log "Created security group: $SECURITY_GROUP_ID"
else
    log "Security group already exists: $SECURITY_GROUP_ID — skipping creation."
fi

# ==============================================================================
# 6. AURORA CLUSTER
# ==============================================================================
log "Checking for existing DB cluster '$CLUSTER_ID'..."

CLUSTER_STATUS=$(aws rds describe-db-clusters \
    --db-cluster-identifier "$CLUSTER_ID" \
    --query 'DBClusters[0].Status' \
    --output text 2>/dev/null || echo "NOT_FOUND")

if [ "$CLUSTER_STATUS" = "NOT_FOUND" ]; then
    log "Cluster not found — creating..."
    aws rds create-db-cluster \
        --db-cluster-identifier "$CLUSTER_ID" \
        --engine aurora-mysql \
        --engine-version "$ENGINE_VERSION" \
        --database-name "$DB_NAME" \
        --master-username "$DB_USER" \
        --master-user-password "$DB_PASS" \
        --network-type IPV4 \
        --db-subnet-group-name "$SUBNET_GROUP_NAME" \
        --vpc-security-group-ids "$SECURITY_GROUP_ID" \
        --backup-retention-period 1 \
        --no-enable-performance-insights \
        --serverless-v2-scaling-configuration MinCapacity=0.0,MaxCapacity=1.0 \
        --storage-type aurora-iopt1
    log "Cluster creation initiated. Waiting for it to become available..."
    aws rds wait db-cluster-available --db-cluster-identifier "$CLUSTER_ID"
    log "Cluster is available."
else
    log "Cluster '$CLUSTER_ID' already exists (status: $CLUSTER_STATUS) — skipping creation."
fi

# ==============================================================================
# 7. AURORA INSTANCE
# ==============================================================================
log "Checking for existing DB instance '$INSTANCE_ID'..."

INSTANCE_STATUS=$(aws rds describe-db-instances \
    --db-instance-identifier "$INSTANCE_ID" \
    --query 'DBInstances[0].DBInstanceStatus' \
    --output text 2>/dev/null || echo "NOT_FOUND")

if [ "$INSTANCE_STATUS" = "NOT_FOUND" ]; then
    log "Instance not found — creating..."
    aws rds create-db-instance \
        --db-instance-identifier "$INSTANCE_ID" \
        --db-cluster-identifier "$CLUSTER_ID" \
        --engine aurora-mysql \
        --db-instance-class db.serverless \
        --no-publicly-accessible \
        --no-auto-minor-version-upgrade
    log "Instance creation initiated. Waiting for it to become available..."
    aws rds wait db-instance-available --db-instance-identifier "$INSTANCE_ID"
    log "Instance is available."
else
    log "Instance '$INSTANCE_ID' already exists (status: $INSTANCE_STATUS) — skipping creation."
fi

# ==============================================================================
# 8. OUTPUT USEFUL ARNS
# ==============================================================================
log "====== DONE ======"

CLUSTER_ARN=$(aws rds describe-db-clusters \
    --db-cluster-identifier "$CLUSTER_ID" \
    --query 'DBClusters[0].DBClusterArn' \
    --output text)
log "Cluster ARN:  $CLUSTER_ARN"

CLUSTER_ENDPOINT=$(aws rds describe-db-clusters \
    --db-cluster-identifier "$CLUSTER_ID" \
    --query 'DBClusters[0].Endpoint' \
    --output text)
log "Endpoint:     $CLUSTER_ENDPOINT"

SECRET_ARN=$(aws secretsmanager list-secrets \
    --query 'SecretList[?contains(Name, `rds`)].ARN' \
    --output text)
[ -n "$SECRET_ARN" ] && log "Secret ARN:   $SECRET_ARN" || warn "No RDS secret found in Secrets Manager."