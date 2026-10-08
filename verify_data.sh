#!/usr/bin/env bash

show_help() {
    cat << EOF
Usage: $(basename "$0") [--remove-errors] [DATA_DIRECTORY]

Description:
  This script scans a specified directory for RDF files (Turtle, RDF/XML, N-Triples, N-Quads)
  and uses the 'rapper' utility to verify their syntax. It provides a summary of successful
  and failed files, and generates an error report if syntax errors are found.

Arguments:
  [DATA_DIRECTORY]  The path to the directory containing RDF files. (Default: ./output)
  --remove-errors   Remove the line after each reported syntax error and retry verification.
  -h, --help        Show this help message.

Requirements:
  Requires the 'rapper' (Raptor RDF syntax parsing utility - raptor2-utils) and 'awk' commands installed.
EOF
}

REMOVE_ERRORS=0
DATA_DIR=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        -h|--help)
            show_help
            exit 0
            ;;
        --remove-errors)
            REMOVE_ERRORS=1
            ;;
        -*)
            echo "Unknown option: $1" >&2
            show_help >&2
            exit 1
            ;;
        *)
            if [[ -n "$DATA_DIR" ]]; then
                echo "Only one data directory may be specified." >&2
                show_help >&2
                exit 1
            fi
            DATA_DIR="$1"
            ;;
    esac
    shift
done

DATA_DIR="${DATA_DIR:-./output}"
FILES_VERIFIED=0
FILES_OK=0
FILES_ERROR=0
ERROR_REPORT=""

remove_line_after() {
    local file="$1"
    local reported_line="$2"
    local line_to_remove=$((reported_line + 1))
    local temporary_file

    temporary_file=$(mktemp "${file}.XXXXXX") || return 1

    if ! awk -v line="$line_to_remove" '
        NR == line { found = 1; next }
        { print }
        END { exit !found }
    ' "$file" > "$temporary_file"; then
        rm -f "$temporary_file"
        return 1
    fi

    mv "$temporary_file" "$file"
}

echo "Starting RDF verification in: $DATA_DIR"
echo "----------------------------------------"

while IFS= read -r -d '' file; do
    echo "Verifying $file,..."

    while true; do
        if out=$(rapper -g -c "$file" 2>&1); then
            triples=$(printf "%s\n" "$out" | awk '/Parsing returned/ {print $4}')
            echo "OK! Returned ${triples:-0} triples"
            FILES_OK=$((FILES_OK + 1))
            break
        fi

        echo "VERIFICATION FAILED"
        err_line=$(printf "%s\n" "$out" | grep -i 'Error' | head -n1 || true)
        [ -z "$err_line" ] && err_line=$(printf "%s\n" "$out" | head -n1)
        echo "Error: $err_line"

        reported_line=$(printf "%s\n" "$err_line" | sed -nE 's/.*:([0-9]+)[[:space:]]*-.*/\1/p')
        if [[ "$REMOVE_ERRORS" -eq 1 && -n "$reported_line" ]] \
            && remove_line_after "$file" "$reported_line"; then
            echo "Removed line $((reported_line + 1)); retrying verification."
            continue
        fi

        FILES_ERROR=$((FILES_ERROR + 1))
        ERROR_REPORT="${ERROR_REPORT}\n${file}: ${err_line}"
        break
    done
done < <(find "$DATA_DIR" -maxdepth 1 -type f \
    \( -name '*.ttl' -o -name '*.rdf' -o -name '*.rdfs' -o -name '*.nt' -o -name '*.nq' \) \
    -print0)

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
