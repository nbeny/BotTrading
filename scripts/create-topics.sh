#!/usr/bin/env bash
# Create all CMI Kafka topics with sensible partition counts (see docs/scaling.md).
#
# Retention policy — the rule is "can this be recomputed?":
#
#   RAW (30 d)    Ingested from outside and gone forever once expired. Nothing
#                 in the system can reconstruct a price tick Kafka dropped, so
#                 these are the floor: a future, larger machine can only replay
#                 the history that still exists here.
#   DERIVED (7 d) Computed from RAW by our own workers. Losing them costs a
#                 replay, not information.
#   AUDIT         Its own table is the system of record; retention matches it.
#
# `kafka-configs.sh --alter` runs on every invocation, not just at creation:
# `--create --if-not-exists` silently ignores the config of a topic that already
# exists, so a policy change here would otherwise never reach a live broker.
set -euo pipefail

BROKER="${KAFKA_BOOTSTRAP_SERVERS:-kafka:9092}"

RAW_MS=2592000000       # 30 days
DERIVED_MS=604800000    # 7 days
AUDIT_MS=15552000000    # 180 days, matches decision_journal

create() {
  kafka-topics.sh --bootstrap-server "$BROKER" --create --if-not-exists \
    --topic "$1" --partitions "$2" --replication-factor 1 \
    --config retention.ms="$3"
  kafka-configs.sh --bootstrap-server "$BROKER" --alter --entity-type topics \
    --entity-name "$1" --add-config retention.ms="$3" >/dev/null
  echo "  topic $1 (partitions=$2, retention=$(( $3 / 86400000 ))d)"
}

echo "Creating CMI Kafka topics on $BROKER ..."
# --- RAW: irreplaceable, 30 days -------------------------------------------
create market.price.events      12  "$RAW_MS"
create market.volume.events     6   "$RAW_MS"
create market.dex.events        12  "$RAW_MS"
create market.news.events       6   "$RAW_MS"
create market.social.events     6   "$RAW_MS"
# Sentiment scores are computed from raw_content in Postgres rather than from a
# Kafka topic, so they are re-derivable in principle -- but only by re-running
# the models, and the scores exist nowhere else on the bus. Treated as raw.
create market.sentiment.events  6   "$RAW_MS"

# --- DERIVED: recomputable by replaying the raw topics, 7 days --------------
create market.analysis.events   6   "$DERIVED_MS"
create decision.events          3   "$DERIVED_MS"
create risk.approved.events     3   "$DERIVED_MS"
create execution.events         3   "$DERIVED_MS"
create control.commands         3   "$DERIVED_MS"
create account.snapshot.events  1   "$DERIVED_MS"

# --- AUDIT: the table is the system of record ------------------------------
create journal.entries          6   "$AUDIT_MS"
echo "Done."
