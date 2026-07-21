#!/usr/bin/env bash
# Create all CMI Kafka topics with sensible partition counts (see docs/scaling.md).
set -euo pipefail

BROKER="${KAFKA_BOOTSTRAP_SERVERS:-kafka:9092}"
create() {
  kafka-topics.sh --bootstrap-server "$BROKER" --create --if-not-exists \
    --topic "$1" --partitions "$2" --replication-factor 1 \
    --config retention.ms="${3:-604800000}"
  echo "  topic $1 (partitions=$2)"
}

echo "Creating CMI Kafka topics on $BROKER ..."
create market.price.events      12
create market.volume.events     6
create market.dex.events        12
create market.news.events       6
create market.social.events     6
create market.sentiment.events  6
create market.analysis.events   6
create decision.events          3
create risk.approved.events     3   2592000000   # 30d retention for signals
echo "Done."
