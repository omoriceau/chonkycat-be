#!/usr/bin/env bash
set -euo pipefail

VPC_NAME="chonky-vpc"

echo "Finding VPC..."

VPC_ID=$(aws ec2 describe-vpcs \
  --filters "Name=tag:Name,Values=${VPC_NAME}" \
  --query 'Vpcs[0].VpcId' \
  --output text)

if [[ "$VPC_ID" == "None" ]]; then
  echo "VPC not found"
  exit 0
fi

echo "VPC: $VPC_ID"

# ------------------------------------------------------------------
# RDS
# ------------------------------------------------------------------

echo "Cleaning up RDS..."

# 1. Delete DB instances (including cluster members)
DB_INSTANCES=$(aws rds describe-db-instances \
  --query 'DBInstances[].DBInstanceIdentifier' \
  --output text)

for DB in $DB_INSTANCES; do
  echo "Deleting DB instance: $DB"

  aws rds delete-db-instance \
    --db-instance-identifier "$DB" \
    --skip-final-snapshot \
    || true
done

# 2. Wait for instances to go away (important for clusters)
echo "Waiting for DB instances to delete... checking every 1 minute"

while true; do
  DB_INSTANCES=$(aws rds describe-db-instances \
    --query 'DBInstances[].DBInstanceIdentifier' \
    --output text 2>/dev/null || true)

  if [[ -z "$DB_INSTANCES" ]]; then
    echo "No DB instances remaining"
    break
  fi

  echo "Still deleting: $DB_INSTANCES"
  sleep 60
done

echo "DB instances fully deleted"

# 3. Delete DB clusters
DB_CLUSTERS=$(aws rds describe-db-clusters \
  --query 'DBClusters[].DBClusterIdentifier' \
  --output text)

for CLUSTER in $DB_CLUSTERS; do
  echo "Deleting DB cluster: $CLUSTER"

  aws rds delete-db-cluster \
    --db-cluster-identifier "$CLUSTER" \
    --skip-final-snapshot \
    || true
done

# 4. Delete subnet groups (manual cleanup)
SUBNET_GROUPS=$(aws rds describe-db-subnet-groups \
  --query 'DBSubnetGroups[].DBSubnetGroupName' \
  --output text)

for SG in $SUBNET_GROUPS; do
  echo "Deleting DB subnet group: $SG"

  aws rds delete-db-subnet-group \
    --db-subnet-group-name "$SG" \
    || true
done

# ------------------------------------------------------------------
# NAT Gateways
# ------------------------------------------------------------------

echo "Deleting NAT gateways..."

for NAT in $(aws ec2 describe-nat-gateways \
  --filter "Name=vpc-id,Values=$VPC_ID" \
  --query 'NatGateways[].NatGatewayId' \
  --output text); do

  aws ec2 delete-nat-gateway \
    --nat-gateway-id "$NAT"
done

# ------------------------------------------------------------------
# Internet Gateways
# ------------------------------------------------------------------

echo "Deleting Internet Gateways..."

for IGW in $(aws ec2 describe-internet-gateways \
  --filters "Name=attachment.vpc-id,Values=$VPC_ID" \
  --query 'InternetGateways[].InternetGatewayId' \
  --output text); do

  aws ec2 detach-internet-gateway \
    --internet-gateway-id "$IGW" \
    --vpc-id "$VPC_ID" || true

  aws ec2 delete-internet-gateway \
    --internet-gateway-id "$IGW"
done

# ------------------------------------------------------------------
# Route Tables
# ------------------------------------------------------------------

echo "Deleting custom route tables..."

for RTB in $(aws ec2 describe-route-tables \
  --filters "Name=vpc-id,Values=$VPC_ID" \
  --query 'RouteTables[?Associations[0].Main!=`true`].RouteTableId' \
  --output text); do

  aws ec2 delete-route-table \
    --route-table-id "$RTB" || true
done

# ------------------------------------------------------------------
# Security Groups
# ------------------------------------------------------------------

echo "Deleting security groups..."

for SG in $(aws ec2 describe-security-groups \
  --filters "Name=vpc-id,Values=$VPC_ID" \
  --query 'SecurityGroups[?GroupName!=`default`].GroupId' \
  --output text); do

  aws ec2 delete-security-group \
    --group-id "$SG" || true
done

# ------------------------------------------------------------------
# Subnets
# ------------------------------------------------------------------

echo "Deleting subnets..."

for SUBNET in $(aws ec2 describe-subnets \
  --filters "Name=vpc-id,Values=$VPC_ID" \
  --query 'Subnets[].SubnetId' \
  --output text); do

  aws ec2 delete-subnet \
    --subnet-id "$SUBNET" || true
done

# ------------------------------------------------------------------
# VPC
# ------------------------------------------------------------------

echo "Deleting VPC..."

aws ec2 delete-vpc \
  --vpc-id "$VPC_ID"

echo "Done"