#!/usr/bin/env bash

set -u
set -o pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
QUERY_DIR="${ROOT_DIR}/queries"
OUTPUT_DIR="${ROOT_DIR}/output"
PYTHON_BIN="${PYTHON_BIN:-python3}"
ENDPOINT="${ENDPOINT:-http://0.0.0.0:7001/api}"
PAGE_SIZE="${PAGE_SIZE:-50000}"

usage() {
  cat <<'EOF'
Usage: ./fetch_all_queries.sh [--endpoint URL] [--page-size N] [--python PATH]

Environment overrides:
  ENDPOINT   SPARQL endpoint URL
  PAGE_SIZE  Number of rows per page
  PYTHON_BIN Python executable to use
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --endpoint)
      ENDPOINT="$2"
      shift 2
      ;;
    --page-size)
      PAGE_SIZE="$2"
      shift 2
      ;;
    --python)
      PYTHON_BIN="$2"
      shift 2
      ;;
    --help|-h)
      usage
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      usage >&2
      exit 1
      ;;
  esac
done

mkdir -p "$OUTPUT_DIR"

shopt -s nullglob
query_files=("$QUERY_DIR"/*.rq)
shopt -u nullglob

if [[ ${#query_files[@]} -eq 0 ]]; then
  echo "No query files found in $QUERY_DIR" >&2
  exit 1
fi

failures=0

for query_file in "${query_files[@]}"; do
  query_name="$(basename "$query_file" .rq)"
  output_file="${OUTPUT_DIR}/${query_name}.ttl"

  if [[ -f "$output_file" ]]; then
    echo "Skipping existing output: $output_file"
    continue
  fi

  echo "Fetching $query_file -> $output_file"

  if ! "$PYTHON_BIN" "$ROOT_DIR/fetch_sparql_construct.py" \
    --endpoint "$ENDPOINT" \
    --query-file "$query_file" \
    --output "$output_file" \
    --page-size "$PAGE_SIZE"; then
    echo "Failed: $query_file" >&2
    failures=1
  fi
done

exit "$failures"
