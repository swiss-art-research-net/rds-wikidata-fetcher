# RDS Wikidata Fetcher

Python script and queries to download large SPARQL `CONSTRUCT` result sets from the Swiss Art Research Wikidata API and similar endpoints, with pagination and retry logic.

Used to create partial Wikidata dumps for use in the Reference Data Service (RDS) project.

## Requirements

- Python 3.9+
- No third-party Python packages

## Usage

Run the script with the included example query:

```bash
python3 fetch_sparql_construct.py \
  --query-file queries/people.rq \
  --output output/people.ttl \
  --page-size 50000
```

You can also pass the query directly on the command line:

```bash
python3 fetch_sparql_construct.py \
  --query 'PREFIX wd: <http://www.wikidata.org/entity/>
CONSTRUCT { wd:Q42 ?p ?o . }
WHERE { wd:Q42 ?p ?o . }' \
  --output output/q42.ttl
```

## How It Works

The script expects a `CONSTRUCT` query and rewrites it into this shape for each page:

```sparql
CONSTRUCT {
  ...
}
WHERE {
  {
    SELECT * WHERE {
      ...
    }
    ORDER BY ...
    LIMIT 5000
    OFFSET 0
  }
}
```

This allows the script to fetch all rows in chunks while still receiving Turtle output for each chunk.

If the source query already contains `LIMIT` or `OFFSET`, those are stripped from the original suffix and replaced with the page-specific values.

If the source query does not include `ORDER BY`, the script injects one automatically based on variables found in the query. This makes pagination more stable across pages.

## Recommended Usage

For large exports:

- Start with `--page-size 1000` or `--page-size 5000`
- Increase `--timeout` if the endpoint is slow
- Leave `--http-method auto` unless you know the endpoint preference
- Keep automatic ordering enabled unless your query already has a deliberate `ORDER BY`

Example with more conservative retry settings:

```bash
python3 fetch_sparql_construct.py \
  --query-file queries/people.rq \
  --output output/people.ttl \
  --page-size 2000 \
  --timeout 120 \
  --max-retries 8 \
  --retry-backoff 3
```

## Command-Line Options

- `--query`: pass a SPARQL `CONSTRUCT` query inline
- `--query-file`: read the query from a file
- `--endpoint`: override the SPARQL endpoint
- `--output`: destination Turtle file
- `--page-size`: number of rows per page
- `--start-offset`: start paging from a specific offset
- `--timeout`: per-request timeout in seconds
- `--max-retries`: number of retries for transient failures
- `--retry-backoff`: base delay between retries
- `--user-agent`: custom HTTP `User-Agent`
- `--http-method {auto,post,get}`: request method selection
- `--no-auto-order-by`: disable injected `ORDER BY`

## Retry Behavior

The script retries when it encounters:

- timeouts
- connection errors
- HTTP `408`
- HTTP `429`
- HTTP `500`
- HTTP `502`
- HTTP `503`
- HTTP `504`

When the endpoint sends `Retry-After`, the script respects it. Otherwise it uses a linear backoff based on `--retry-backoff`.

## Notes

- The script stops when a page returns no Turtle triples.
- Prefix declarations are written once, from the first page.
- The final output file is a merged Turtle document containing all fetched pages.
- The script supports only SPARQL `CONSTRUCT` queries.