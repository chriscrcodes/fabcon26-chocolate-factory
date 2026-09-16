#!/usr/bin/env bash
# Prepares fresh demo telemetry the day before a session: resumes the
# Fabric capacity if it's paused, clears any previously streamed
# telemetry (so a re-run never double-counts a prior backfill -- see
# fabric/eventhouse/clear_telemetry.py's docstring for why that
# matters), backfills DAYS days of history ending now, and verifies
# the result.
#
# Usage:
#   ./prepare_demo_data.sh [DAYS]   # default 30
#
# Run from a shell already `az login`'d as an identity with Fabric +
# Event Hub access (the same one used for `terraform apply`).
# Connection details are read from `terraform output` in ../infra, so
# `terraform apply` must have already completed successfully.

set -euo pipefail

DAYS="${1:-30}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INFRA_DIR="$SCRIPT_DIR/../infra"
EVENTHOUSE_DIR="$SCRIPT_DIR/../fabric/eventhouse"

echo "==> reading Terraform outputs from $INFRA_DIR"
FABRIC_CAPACITY_ID=$(terraform -chdir="$INFRA_DIR" output -raw FABRIC_CAPACITY_ID)
export AZURE_SUBSCRIPTION_ID="${FABRIC_CAPACITY_ID#/subscriptions/}"
AZURE_SUBSCRIPTION_ID="${AZURE_SUBSCRIPTION_ID%%/*}"
export AZURE_SUBSCRIPTION_ID
export AZURE_RESOURCE_GROUP="${FABRIC_CAPACITY_ID#*/resourceGroups/}"
AZURE_RESOURCE_GROUP="${AZURE_RESOURCE_GROUP%%/*}"
export AZURE_RESOURCE_GROUP
export FABRIC_CAPACITY_NAME="${FABRIC_CAPACITY_ID##*/}"
export KQL_QUERY_URI
KQL_QUERY_URI=$(terraform -chdir="$INFRA_DIR" output -raw FABRIC_KQL_DATABASE_QUERY_URI)
export KQL_DATABASE
KQL_DATABASE=$(terraform -chdir="$INFRA_DIR" output -raw FABRIC_KQL_DATABASE_NAME)
export AZURE_EVENT_HUB_NAMESPACE_HOSTNAME
AZURE_EVENT_HUB_NAMESPACE_HOSTNAME=$(terraform -chdir="$INFRA_DIR" output -raw AZURE_EVENT_HUB_NAMESPACE_HOSTNAME)
export AZURE_EVENT_HUB_NAME
AZURE_EVENT_HUB_NAME=$(terraform -chdir="$INFRA_DIR" output -raw AZURE_EVENT_HUB_NAME)

echo "==> resuming Fabric capacity ($FABRIC_CAPACITY_NAME) if paused"
uv run --with azure-identity --with requests "$SCRIPT_DIR/../fabric/manage_capacity.py" resume

echo "==> clearing existing telemetry (Bronze/Silver/Gold) to avoid duplicates"
uv run --with azure-kusto-data --with azure-identity "$EVENTHOUSE_DIR/clear_telemetry.py"

echo "==> backfilling ${DAYS} day(s) of history ending now"
HOURS=$((DAYS * 24))
(cd "$SCRIPT_DIR" && uv run run_simulator.py --backfill-hours "$HOURS" --interval 300 --backfill-batch-ticks 100)

echo "==> verifying"
uv run --with azure-kusto-data --with azure-identity "$EVENTHOUSE_DIR/verify_telemetry.py"

echo "==> done. Start real-time streaming closer to the session itself:"
echo "    cd simulator && uv run run_simulator.py --interval 5 --anomaly-rate 0.03 --downtime-rate 0.015"
