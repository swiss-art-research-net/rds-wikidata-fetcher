DATA_DIR="{{.DATA_DIRECTORY}}"
FILES_VERIFIED=0
FILES_OK=0
FILES_ERROR=0
ERROR_REPORT=""

for file in $(find "$DATA_DIR" -type f -regex ".*\.\(ttl\|rdf\|rdfs\|nt\|nq\)"); do
    echo "Verifying $file,..."

    if out=$(rapper -g -c "$file" 2>&1); then
    triples=$(printf "%s\n" "$out" | awk '/Parsing returned/ {print $4}')
    echo "OK! Returned ${triples} triples"
    FILES_OK=$((FILES_OK + 1))
    else
    echo "VERIFICATION FAILED"
    err_line=$(printf "%s\n" "$out" | grep -i 'Error' | head -n1 || true)
    [ -z "$err_line" ] && err_line=$(printf "%s\n" "$out" | head -n1)
    echo "Error: $err_line"
    FILES_ERROR=$((FILES_ERROR + 1))
    ERROR_REPORT="${ERROR_REPORT}\n${file}: ${err_line}"
    fi
done

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
fi

if [ "$FILES_ERROR" -gt 0 ]; then
    exit 1
fi