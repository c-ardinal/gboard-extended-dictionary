"""Fetch Wikidata items with a Japanese label and a kana reading (P1814) into a pinned TSV.

Output: <dest>/wikidata-p1814-ja-<YYYYMMDD>.tsv (+ .sha256), columns qid, label_ja, kana, p31s (comma-joined),
sitelinks, one row per (item, kana), sorted by QID number then kana. Raw SPARQL JSON responses are kept beside it.
Re-running with the same date reuses the cached TSV and does not touch the network.
Data licence: Wikidata is CC0.

To pin a new snapshot: run this script, upload the TSV as an asset of a release tagged wikidata-<YYYYMMDD>,
then put its URL and sha256 into tools/fetch_inputs.py and the --wikidata path into the workflow.
"""
import argparse
import datetime
import functools
import hashlib
import json
import os
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

ENDPOINT = "https://query.wikidata.org/sparql"
USER_AGENT = "gboard-extended-dictionary/0.3 (https://github.com/c-ardinal/gboard-extended-dictionary)"
ENTITY = "http://www.wikidata.org/entity/"
# A single label query (or one filtered by QID modulo N) scans every P1814 statement and hits the 60 s
# WDQS timeout; WDQS then streams a truncated body ending in a Java TimeoutException. Instead, labels are
# fetched for explicit batches of items (VALUES), which are index lookups.
BATCH = 10_000
TIMEOUT_MARKER = b"java.util.concurrent.TimeoutException"
BACKOFF_SECONDS = 30  # first retry wait; doubles on each further attempt
# Labels and readings for one batch of items (%s = "wd:Q1 wd:Q2 ...").
LABEL_QUERY = """SELECT ?item ?label ?kana WHERE {
  VALUES ?item { %s }
  ?item wdt:P1814 ?kana ; rdfs:label ?label .
  FILTER(LANG(?label) = "ja")
}"""
# Instance-of classes of every item that has a kana reading.
CLASS_QUERY = """SELECT DISTINCT ?item ?class WHERE {
  ?item wdt:P1814 ?kana ; wdt:P31 ?class .
}"""
# Number of sitelinks (Wikipedia and other Wikimedia pages) of every item that has a kana reading.
SITELINK_QUERY = """SELECT DISTINCT ?item ?sitelinks WHERE {
  ?item wdt:P1814 ?kana ; wikibase:sitelinks ?sitelinks .
}"""


def run_query(query, raw_path, endpoint=ENDPOINT, backoff=BACKOFF_SECONDS, attempts=5):
    if os.path.exists(raw_path):
        with open(raw_path, encoding="utf-8") as fh:
            return json.load(fh, strict=False)
    # POST: long VALUES lists do not fit in a URL, and WDQS does not cache POST results (a cached GET can
    # keep serving a truncated timeout body for minutes).
    payload = urllib.parse.urlencode({"query": query, "format": "json"}).encode("ascii")
    request = urllib.request.Request(endpoint, data=payload, headers={
        "User-Agent": USER_AGENT, "Accept": "application/sparql-results+json",
        "Content-Type": "application/x-www-form-urlencoded"})
    for attempt in range(attempts):
        try:
            started = time.monotonic()
            with urllib.request.urlopen(request, timeout=120) as resp:
                body = resp.read()
            if TIMEOUT_MARKER in body:
                raise ValueError("server-side query timeout (truncated result)")
            data = json.loads(body, strict=False)  # WDQS leaves some control characters in literals unescaped
            print(f"{os.path.basename(raw_path)}: {len(body)} bytes, {len(data['results']['bindings'])} rows, "
                  f"{time.monotonic() - started:.1f} s")
            with open(raw_path + ".part", "wb") as fh:
                fh.write(body)
            os.replace(raw_path + ".part", raw_path)  # an interrupted write never becomes a cached response
            return data
        except ssl.SSLCertVerificationError:
            raise  # a local trust-store problem; retrying cannot help
        except urllib.error.HTTPError as exc:
            if exc.code != 429 and exc.code < 500:
                raise  # a bad query or request; retrying cannot help
            wait = backoff * 2 ** attempt
            print(f"attempt {attempt + 1} failed (HTTP {exc.code}); retrying in {wait} s", file=sys.stderr)
            time.sleep(wait)
        except (OSError, ValueError) as exc:  # network errors, timeouts, truncated JSON
            wait = backoff * 2 ** attempt
            print(f"attempt {attempt + 1} failed ({exc}); retrying in {wait} s", file=sys.stderr)
            time.sleep(wait)
    raise SystemExit(f"query failed after {attempts} attempts: {raw_path}")


def qid(binding):
    return binding["item"]["value"].removeprefix(ENTITY)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("dest", nargs="?", default="input")
    ap.add_argument("--date", default=datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d"))
    ap.add_argument("--endpoint", default=ENDPOINT, help="SPARQL endpoint (tests point this at a local stub)")
    ap.add_argument("--backoff", type=float, default=BACKOFF_SECONDS, help="first retry wait in seconds")
    args = ap.parse_args(argv)
    tsv = os.path.join(args.dest, f"wikidata-p1814-ja-{args.date}.tsv")
    if not os.path.exists(tsv):
        raw_dir = os.path.join(args.dest, f"wikidata-raw-{args.date}")
        os.makedirs(raw_dir, exist_ok=True)
        classes = {}
        query = functools.partial(run_query, endpoint=args.endpoint, backoff=args.backoff)
        for b in query(CLASS_QUERY, os.path.join(raw_dir, "classes.json"))["results"]["bindings"]:
            classes.setdefault(qid(b), set()).add(b["class"]["value"].removeprefix(ENTITY))
        sitelinks = {qid(b): int(b["sitelinks"]["value"])
                     for b in query(SITELINK_QUERY, os.path.join(raw_dir, "sitelinks.json"))["results"]["bindings"]}
        items = sorted(sitelinks, key=lambda q: int(q[1:]))  # every item with a kana reading
        rows, skipped = set(), 0
        for start in range(0, len(items), BATCH):
            labels = LABEL_QUERY % " ".join("wd:" + q for q in items[start:start + BATCH])
            raw = os.path.join(raw_dir, f"labels-{start // BATCH:03d}.json")
            for b in query(labels, raw)["results"]["bindings"]:
                fields = (qid(b), b["label"]["value"], b["kana"]["value"])
                if any(c in f for f in fields for c in "\t\r\n"):
                    skipped += 1
                    continue
                rows.add(fields)
        ordered = sorted(rows, key=lambda r: (int(r[0][1:]), r[2]))
        partial = tsv + ".part"  # renamed only when complete, so an interrupted run is never reused as a snapshot
        with open(partial, "w", encoding="utf-8", newline="\n") as fh:
            fh.write("qid\tlabel_ja\tkana\tp31s\tsitelinks\n")
            for q, label, kana in ordered:
                fh.write(f"{q}\t{label}\t{kana}\t{','.join(sorted(classes.get(q, ())))}\t{sitelinks.get(q, 0)}\n")
        os.replace(partial, tsv)
        print(f"rows {len(ordered)}, skipped (tab/newline in a field) {skipped}")
    with open(tsv, "rb") as fh:
        digest = hashlib.sha256(fh.read()).hexdigest()
    with open(tsv + ".sha256", "w", encoding="utf-8", newline="\n") as fh:
        fh.write(f"{digest}  {os.path.basename(tsv)}\n")
    print(f"{tsv} sha256 {digest}")


if __name__ == "__main__":
    main()
