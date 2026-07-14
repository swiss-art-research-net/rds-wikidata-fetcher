#!/usr/bin/env bash

show_help() {
    cat << EOF
Usage: $(basename "$0") [DATA_DIRECTORY]

Description:
  This script scans a specified directory for RDF files (Turtle, RDF/XML, N-Triples, N-Quads)
  and uses the 'rapper' utility to verify their syntax. It provides a summary of successful
  and failed files, and generates an error report if syntax errors are found.

Arguments:
  [DATA_DIRECTORY]  The path to the directory containing RDF files. (Default: ./output)
  -h, --help        Show this help message.

Requirements:
  Requires the 'rapper' (Raptor RDF syntax parsing utility - raptor2-utils) and 'awk' commands installed.
EOF
}

if [[ "$1" == "-h" || "$1" == "--help" ]]; then
    show_help
    exit 0
fi

DATA_DIR="${1:-./output}"
FILES_VERIFIED=0
FILES_OK=0
FILES_ERROR=0
ERROR_REPORT=""

echo "Starting RDF verification in: $DATA_DIR"
echo "----------------------------------------"

while IFS= read -r -d '' file; do
    echo "Verifying $file,..."

    if out=$(rapper -g -c "$file" 2>&1); then
    triples=$(printf "%s\n" "$out" | awk '/Parsing returned/ {print $4}')
    echo "OK! Returned ${triples:-0} triples"
    FILES_OK=$((FILES_OK + 1))
    else
    echo "VERIFICATION FAILED"
    err_line=$(printf "%s\n" "$out" | grep -i 'Error' | head -n1 || true)
    [ -z "$err_line" ] && err_line=$(printf "%s\n" "$out" | head -n1)
    echo "Error: $err_line"
    FILES_ERROR=$((FILES_ERROR + 1))
    ERROR_REPORT="${ERROR_REPORT}\n${file}: ${err_line}"
    fi
done < <(find "$DATA_DIR" -type f -regex ".*\.\(ttl\|rdf\|rdfs\|nt\|nq\)" -print0)

FILES_VERIFIED=$((FILES_OK + FILES_ERROR))

echo
echo "Verification completed."
echo "Files verified: $FILES_VERIFIED"
echo "Files OK: $FILES_OK"
echo "Files with errors: $FILES_ERROR"

if [ "$FILES_ERROR" -gt 0 ]; then
    echo
    echo "Error Report:"
    printf "%b\n" "${ERROR_REPORT#\\n}"
    exit 1
fi