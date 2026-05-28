#!/usr/bin/env python3
"""Fetch a paginated SPARQL CONSTRUCT query and write the full result as Turtle.

Example:
    python3 fetch_sparql_construct.py \
      --query-file wikidata_people_query.rq \
      --output humans.ttl
"""

from __future__ import annotations

import argparse
import pathlib
import re
import socket
import sys
import textwrap
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass


DEFAULT_ENDPOINT = "https://wikidata.swissartresearch.net/api"
DEFAULT_USER_AGENT = "sparql-construct-pager/1.0 (https://wikidata.swissartresearch.net/)"
RETRYABLE_STATUS_CODES = {408, 429, 500, 502, 503, 504}
PROLOGUE_LINE_RE = re.compile(r"^\s*(?:PREFIX|BASE)\b", re.IGNORECASE)
PREFIX_LINE_RE = re.compile(r"^\s*(?:@prefix|@base|PREFIX|BASE)\b", re.IGNORECASE)
ORDER_BY_RE = re.compile(r"\bORDER\s+BY\b", re.IGNORECASE)
LIMIT_RE = re.compile(r"\bLIMIT\s+\d+\b", re.IGNORECASE)
OFFSET_RE = re.compile(r"\bOFFSET\s+\d+\b", re.IGNORECASE)
VAR_RE = re.compile(r"\?[A-Za-z_][A-Za-z0-9_-]*")


@dataclass
class ParsedConstructQuery:
    prologue: str
    construct_body: str
    where_body: str
    suffix: str


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Fetch all results from a SPARQL CONSTRUCT query by paging with "
            "LIMIT/OFFSET and writing a merged Turtle file."
        )
    )
    query_group = parser.add_mutually_exclusive_group(required=True)
    query_group.add_argument(
        "--query",
        help="SPARQL CONSTRUCT query passed directly on the command line.",
    )
    query_group.add_argument(
        "--query-file",
        type=pathlib.Path,
        help="Path to a file containing the SPARQL CONSTRUCT query.",
    )
    parser.add_argument(
        "--endpoint",
        default=DEFAULT_ENDPOINT,
        help=f"SPARQL endpoint URL. Default: {DEFAULT_ENDPOINT}",
    )
    parser.add_argument(
        "--output",
        type=pathlib.Path,
        required=True,
        help="Path to the Turtle file to write.",
    )
    parser.add_argument(
        "--page-size",
        type=int,
        default=1000,
        help="Number of solution rows to request per page. Default: 1000",
    )
    parser.add_argument(
        "--start-offset",
        type=int,
        default=0,
        help="Offset to start from. Default: 0",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=60,
        help="HTTP timeout per request in seconds. Default: 60",
    )
    parser.add_argument(
        "--max-retries",
        type=int,
        default=5,
        help="Maximum retries per page for timeouts/retryable errors. Default: 5",
    )
    parser.add_argument(
        "--retry-backoff",
        type=float,
        default=2.0,
        help="Base backoff in seconds between retries. Default: 2.0",
    )
    parser.add_argument(
        "--user-agent",
        default=DEFAULT_USER_AGENT,
        help=(
            "User-Agent header to send to the SPARQL endpoint. "
            f"Default: {DEFAULT_USER_AGENT}"
        ),
    )
    parser.add_argument(
        "--http-method",
        choices=("auto", "post", "get"),
        default="auto",
        help=(
            "How to send the SPARQL query. 'auto' tries POST first and then GET "
            "if the endpoint rejects POST. Default: auto"
        ),
    )
    parser.add_argument(
        "--no-auto-order-by",
        action="store_true",
        help=(
            "Do not inject an ORDER BY when the source query does not already "
            "define one."
        ),
    )
    return parser.parse_args()


def read_query(args: argparse.Namespace) -> str:
    if args.query is not None:
        return args.query.strip()

    assert args.query_file is not None
    return args.query_file.read_text(encoding="utf-8").strip()


def extract_prologue(query: str) -> tuple[str, str]:
    lines = query.splitlines(keepends=True)
    prologue_lines: list[str] = []
    index = 0

    while index < len(lines):
        stripped = lines[index].strip()
        if not stripped:
            if prologue_lines:
                prologue_lines.append(lines[index])
            index += 1
            continue
        if PROLOGUE_LINE_RE.match(lines[index]):
            prologue_lines.append(lines[index])
            index += 1
            continue
        break

    prologue = "".join(prologue_lines).strip()
    remainder = "".join(lines[index:]).strip()
    return prologue, remainder


def extract_braced_block(text: str, opening_brace_index: int) -> tuple[str, int]:
    if opening_brace_index < 0 or opening_brace_index >= len(text) or text[opening_brace_index] != "{":
        raise ValueError("Expected an opening brace while parsing the query.")

    depth = 0
    index = opening_brace_index
    in_comment = False
    string_delimiter: str | None = None
    triple_quoted = False

    while index < len(text):
        char = text[index]
        next_three = text[index : index + 3]

        if in_comment:
            if char == "\n":
                in_comment = False
            index += 1
            continue

        if string_delimiter is not None:
            if triple_quoted:
                if next_three == string_delimiter * 3:
                    string_delimiter = None
                    triple_quoted = False
                    index += 3
                    continue
                index += 1
                continue

            if char == "\\":
                index += 2
                continue
            if char == string_delimiter:
                string_delimiter = None
            index += 1
            continue

        if char == "#":
            in_comment = True
            index += 1
            continue

        if next_three in ('"""', "'''"):
            string_delimiter = next_three[0]
            triple_quoted = True
            index += 3
            continue

        if char in ('"', "'"):
            string_delimiter = char
            triple_quoted = False
            index += 1
            continue

        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[opening_brace_index + 1 : index], index + 1

        index += 1

    raise ValueError("Unbalanced braces while parsing the query.")


def parse_construct_query(query: str) -> ParsedConstructQuery:
    prologue, remainder = extract_prologue(query)

    construct_match = re.search(r"\bCONSTRUCT\b", remainder, re.IGNORECASE)
    if not construct_match:
        raise ValueError("Only SPARQL CONSTRUCT queries are supported.")

    construct_open = remainder.find("{", construct_match.end())
    construct_body, after_construct = extract_braced_block(remainder, construct_open)

    where_match = re.search(r"\bWHERE\b", remainder[after_construct:], re.IGNORECASE)
    if not where_match:
        raise ValueError("Could not find a WHERE clause in the CONSTRUCT query.")

    where_keyword_index = after_construct + where_match.start()
    where_open = remainder.find("{", where_keyword_index)
    where_body, after_where = extract_braced_block(remainder, where_open)
    suffix = remainder[after_where:].strip()

    return ParsedConstructQuery(
        prologue=prologue,
        construct_body=construct_body.strip(),
        where_body=where_body.strip(),
        suffix=suffix,
    )


def strip_limit_offset(suffix: str) -> str:
    without_limit = LIMIT_RE.sub("", suffix)
    without_offset = OFFSET_RE.sub("", without_limit)
    cleaned_lines = [line.rstrip() for line in without_offset.splitlines()]
    return "\n".join(cleaned_lines).strip()


def infer_order_by(parsed: ParsedConstructQuery) -> str:
    seen: set[str] = set()
    ordered_vars: list[str] = []

    for variable in VAR_RE.findall(f"{parsed.construct_body}\n{parsed.where_body}"):
        if variable not in seen:
            seen.add(variable)
            ordered_vars.append(variable)

    if not ordered_vars:
        return ""

    return "ORDER BY " + " ".join(ordered_vars)


def build_paginated_query(
    parsed: ParsedConstructQuery,
    page_size: int,
    offset: int,
    auto_order_by: bool,
) -> str:
    suffix = strip_limit_offset(parsed.suffix)
    has_explicit_order_by = bool(ORDER_BY_RE.search(suffix))
    order_clause = ""

    if auto_order_by and not has_explicit_order_by:
        order_clause = infer_order_by(parsed)

    query_parts = []
    if parsed.prologue:
        query_parts.append(parsed.prologue)

    query_parts.append("CONSTRUCT {")
    query_parts.append(textwrap.indent(parsed.construct_body, "  "))
    query_parts.append("}")
    query_parts.append("WHERE {")
    query_parts.append("  {")
    query_parts.append("    SELECT * WHERE {")
    query_parts.append(textwrap.indent(parsed.where_body, "      "))
    query_parts.append("    }")

    if suffix:
        query_parts.append(textwrap.indent(suffix, "    "))
    if order_clause:
        query_parts.append(f"    {order_clause}")

    query_parts.append(f"    LIMIT {page_size}")
    query_parts.append(f"    OFFSET {offset}")
    query_parts.append("  }")
    query_parts.append("}")

    return "\n".join(query_parts)


def compute_retry_delay(
    attempt_number: int,
    retry_backoff: float,
    retry_after_value: str | None,
) -> float:
    if retry_after_value:
        try:
            return max(float(retry_after_value), 0.0)
        except ValueError:
            pass
    return retry_backoff * attempt_number


def build_request(
    endpoint: str,
    query: str,
    user_agent: str,
    method: str,
) -> urllib.request.Request:
    headers = {
        "Accept": "text/turtle",
        "User-Agent": user_agent,
    }

    if method == "POST":
        encoded_query = urllib.parse.urlencode({"query": query}).encode("utf-8")
        headers["Content-Type"] = "application/x-www-form-urlencoded; charset=utf-8"
        return urllib.request.Request(
            endpoint,
            data=encoded_query,
            method="POST",
            headers=headers,
        )

    if method == "GET":
        separator = "&" if "?" in endpoint else "?"
        url = f"{endpoint}{separator}{urllib.parse.urlencode({'query': query})}"
        return urllib.request.Request(url, method="GET", headers=headers)

    raise ValueError(f"Unsupported HTTP method: {method}")


def fetch_turtle_page(
    endpoint: str,
    query: str,
    timeout: int,
    max_retries: int,
    retry_backoff: float,
    user_agent: str,
    http_method: str,
) -> str:
    methods = ["POST", "GET"] if http_method == "auto" else [http_method.upper()]

    for attempt in range(1, max_retries + 1):
        method_errors: list[str] = []

        for index, method in enumerate(methods):
            request = build_request(endpoint, query, user_agent, method)

            try:
                with urllib.request.urlopen(request, timeout=timeout) as response:
                    return response.read().decode("utf-8")
            except urllib.error.HTTPError as exc:
                body = exc.read().decode("utf-8", errors="replace")

                if (
                    http_method == "auto"
                    and method == "POST"
                    and exc.code in {400, 405, 415, 501}
                    and index < len(methods) - 1
                ):
                    print(
                        f"POST was rejected with HTTP {exc.code}; trying GET instead...",
                        file=sys.stderr,
                    )
                    method_errors.append(f"{method}: HTTP {exc.code}")
                    continue

                if exc.code not in RETRYABLE_STATUS_CODES or attempt == max_retries:
                    raise RuntimeError(
                        f"HTTP {exc.code} from endpoint after {attempt} attempt(s):\n{body}"
                    ) from exc

                delay = compute_retry_delay(
                    attempt,
                    retry_backoff,
                    exc.headers.get("Retry-After"),
                )
                print(
                    f"Retryable HTTP {exc.code}; retrying in {delay:.1f}s "
                    f"(attempt {attempt}/{max_retries})...",
                    file=sys.stderr,
                )
                time.sleep(delay)
                break
            except (urllib.error.URLError, TimeoutError, socket.timeout) as exc:
                if attempt == max_retries:
                    raise RuntimeError(
                        f"Request failed after {attempt} attempt(s): {exc}"
                    ) from exc

                delay = compute_retry_delay(attempt, retry_backoff, None)
                print(
                    f"Request error: {exc}; retrying in {delay:.1f}s "
                    f"(attempt {attempt}/{max_retries})...",
                    file=sys.stderr,
                )
                time.sleep(delay)
                break
        else:
            if method_errors:
                raise RuntimeError(
                    "Endpoint rejected all available HTTP methods: "
                    + ", ".join(method_errors)
                )

    raise RuntimeError("Unexpected retry loop exit.")


def split_turtle_document(document: str) -> tuple[str, str]:
    lines = document.splitlines()
    prefix_lines: list[str] = []
    body_lines: list[str] = []
    in_prefix_block = True

    for line in lines:
        stripped = line.strip()
        if in_prefix_block and (not stripped or stripped.startswith("#") or PREFIX_LINE_RE.match(line)):
            prefix_lines.append(line)
            continue

        in_prefix_block = False
        body_lines.append(line)

    prefix_text = "\n".join(prefix_lines).strip()
    body_text = "\n".join(body_lines).strip()
    return prefix_text, body_text


def ensure_parent_directory(path: pathlib.Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def main() -> int:
    args = parse_args()

    if args.page_size <= 0:
        print("--page-size must be greater than zero.", file=sys.stderr)
        return 2
    if args.start_offset < 0:
        print("--start-offset must not be negative.", file=sys.stderr)
        return 2
    if args.max_retries <= 0:
        print("--max-retries must be greater than zero.", file=sys.stderr)
        return 2

    source_query = read_query(args)
    parsed_query = parse_construct_query(source_query)
    ensure_parent_directory(args.output)

    if args.no_auto_order_by and not ORDER_BY_RE.search(parsed_query.suffix):
        print(
            "Warning: the query has no ORDER BY clause. Pagination may be less stable "
            "if the endpoint changes while the script is running.",
            file=sys.stderr,
        )

    offset = args.start_offset
    pages_written = 0

    with args.output.open("w", encoding="utf-8") as output_handle:
        while True:
            page_query = build_paginated_query(
                parsed_query,
                page_size=args.page_size,
                offset=offset,
                auto_order_by=not args.no_auto_order_by,
            )
            page_number = pages_written + 1

            print(
                f"Fetching page {page_number} with LIMIT {args.page_size} OFFSET {offset}...",
                file=sys.stderr,
            )
            turtle_page = fetch_turtle_page(
                endpoint=args.endpoint,
                query=page_query,
                timeout=args.timeout,
                max_retries=args.max_retries,
                retry_backoff=args.retry_backoff,
                user_agent=args.user_agent,
                http_method=args.http_method,
            )

            prefixes, body = split_turtle_document(turtle_page)

            if pages_written == 0 and prefixes:
                output_handle.write(prefixes)
                output_handle.write("\n\n")

            if not body:
                print("No more triples returned; stopping.", file=sys.stderr)
                break

            output_handle.write(body)
            output_handle.write("\n\n")
            output_handle.flush()

            pages_written += 1
            offset += args.page_size
            print(
                f"Wrote page {pages_written} to {args.output}.",
                file=sys.stderr,
            )

    print(
        f"Finished. Wrote {pages_written} page(s) to {args.output}.",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except ValueError as exc:
        print(f"Query parsing error: {exc}", file=sys.stderr)
        raise SystemExit(2)
    except RuntimeError as exc:
        print(f"Fetch error: {exc}", file=sys.stderr)
        raise SystemExit(1)
