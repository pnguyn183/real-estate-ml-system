#!/usr/bin/env bash
set -euo pipefail

BOOTSTRAP_SERVERS="${KAFKA_BOOTSTRAP_SERVERS:-kafka:29092}"
PARTITIONS=3

wait_for_kafka() {
  for _ in $(seq 1 30); do
    if kafka-topics --bootstrap-server "${BOOTSTRAP_SERVERS}" --list >/dev/null 2>&1; then
      return 0
    fi
    sleep 2
  done
  echo "Kafka did not become ready at ${BOOTSTRAP_SERVERS}" >&2
  exit 1
}

ensure_topic() {
  local topic="$1"
  kafka-topics --bootstrap-server "${BOOTSTRAP_SERVERS}" \
    --create --if-not-exists --topic "${topic}" \
    --partitions "${PARTITIONS}" --replication-factor 3 \
    --config min.insync.replicas=2 >/dev/null

  # Existing topics from the single-broker stack may have one partition.
  # Increasing partitions is safe for this migration; decreasing is not.
  kafka-topics --bootstrap-server "${BOOTSTRAP_SERVERS}" \
    --alter --topic "${topic}" --partitions "${PARTITIONS}" >/dev/null 2>&1 || true
}

write_reassignment() {
  local path="$1"
  shift
  local topics=("$@")
  {
    printf '{"version":1,"partitions":['
    local first=true
    for topic in "${topics[@]}"; do
      for partition in 0 1 2; do
        if [[ "${first}" != true ]]; then printf ','; fi
        first=false
        case "${partition}" in
          0) replicas='[1,2,3]' ;;
          1) replicas='[2,3,1]' ;;
          2) replicas='[3,1,2]' ;;
        esac
        printf '{"topic":"%s","partition":%s,"replicas":%s}' \
          "${topic}" "${partition}" "${replicas}"
      done
    done
    printf ']}'
  } > "${path}"
}

write_offsets_reassignment() {
  local path="$1"
  local count
  count="$(kafka-topics --bootstrap-server "${BOOTSTRAP_SERVERS}" \
    --describe --topic __consumer_offsets | awk '/Partition:/ {n++} END {print n+0}')"
  if [[ "${count}" -lt 1 ]]; then
    : > "${path}"
    return 1
  fi

  {
    printf '{"version":1,"partitions":['
    local first=true
    for partition in $(seq 0 $((count - 1))); do
      if [[ "${first}" != true ]]; then printf ','; fi
      first=false
      case $((partition % 3)) in
        0) replicas='[1,2,3]' ;;
        1) replicas='[2,3,1]' ;;
        2) replicas='[3,1,2]' ;;
      esac
      printf '{"topic":"__consumer_offsets","partition":%s,"replicas":%s}' \
        "${partition}" "${replicas}"
    done
    printf ']}'
  } > "${path}"
}

wait_for_kafka
ensure_topic real_estate_raw
ensure_topic real_estate_features
# Agent queues use the same local three-broker durability policy. Existing
# raw/features topic names, partitions and consumer group are unchanged.
ensure_topic real_estate_stress_raw
ensure_topic real_estate_stress_features
ensure_topic real_estate_ai_input
ensure_topic real_estate_ai_results
ensure_topic real_estate_ai_dlq

APP_REASSIGNMENT=/tmp/app-reassignment.json
write_reassignment "${APP_REASSIGNMENT}" real_estate_raw real_estate_features \
  real_estate_stress_raw real_estate_stress_features real_estate_ai_input real_estate_ai_results real_estate_ai_dlq
kafka-reassign-partitions --bootstrap-server "${BOOTSTRAP_SERVERS}" \
  --reassignment-json-file "${APP_REASSIGNMENT}" --execute >/dev/null

OFFSETS_REASSIGNMENT=/tmp/offsets-reassignment.json
if write_offsets_reassignment "${OFFSETS_REASSIGNMENT}"; then
  kafka-reassign-partitions --bootstrap-server "${BOOTSTRAP_SERVERS}" \
    --reassignment-json-file "${OFFSETS_REASSIGNMENT}" --execute >/dev/null
fi

sleep 2
kafka-topics --bootstrap-server "${BOOTSTRAP_SERVERS}" \
  --describe --topic real_estate_raw
kafka-topics --bootstrap-server "${BOOTSTRAP_SERVERS}" \
  --describe --topic real_estate_features
for topic in real_estate_stress_raw real_estate_stress_features real_estate_ai_input real_estate_ai_results real_estate_ai_dlq; do
  kafka-topics --bootstrap-server "${BOOTSTRAP_SERVERS}" --describe --topic "${topic}"
done
