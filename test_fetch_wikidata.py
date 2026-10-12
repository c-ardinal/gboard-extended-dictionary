"""Tests for tools/fetch_wikidata.py against a local SPARQL stub (no network)."""
import http.server
import json
import os
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.parse
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "tools"))
import fetch_wikidata as fw  # noqa: E402

ENTITY = fw.ENTITY
DATE = "20261012"
GOOD = {
    "classes": [("Q1", "Q5"), ("Q2", "Q8502")],
    "sitelinks": [("Q1", "10"), ("Q2", "3")],
    "labels": [("Q1", "斎藤隆", "さいとう たかし"), ("Q2", "東山", "ひがしやま"), ("Q2", "東山", "とうざん")],
}
EXPECTED_TSV = ("qid\tlabel_ja\tkana\tp31s\tsitelinks\n"
                "Q1\t斎藤隆\tさいとう たかし\tQ5\t10\n"
                "Q2\t東山\tとうざん\tQ8502\t3\n"
                "Q2\t東山\tひがしやま\tQ8502\t3\n")


def results(kind, reverse=False):
    rows = GOOD[kind][::-1] if reverse else GOOD[kind]
    if kind == "classes":
        bindings = [{"item": {"value": ENTITY + q}, "class": {"value": ENTITY + c}} for q, c in rows]
    elif kind == "sitelinks":
        bindings = [{"item": {"value": ENTITY + q}, "sitelinks": {"value": n}} for q, n in rows]
    else:
        bindings = [{"item": {"value": ENTITY + q}, "label": {"value": label, "xml:lang": "ja"},
                     "kana": {"value": kana}} for q, label, kana in rows]
    return 200, json.dumps({"results": {"bindings": bindings}}, ensure_ascii=False).encode("utf-8")


class Stub:
    """Threaded HTTP server; per query kind, serves scripted (status, body) responses, then good ones."""

    def __init__(self, reverse=False):
        self.script = {"classes": [], "sitelinks": [], "labels": []}
        self.requests = {"classes": 0, "sitelinks": 0, "labels": 0}
        self.reverse = reverse
        stub = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_POST(self):
                body = self.rfile.read(int(self.headers["Content-Length"])).decode("ascii")
                query = urllib.parse.parse_qs(body)["query"][0]
                kind = "classes" if "wdt:P31" in query else "sitelinks" if "wikibase:sitelinks" in query else "labels"
                stub.requests[kind] += 1
                status, payload = stub.script[kind].pop(0) if stub.script[kind] else results(kind, stub.reverse)
                self.send_response(status)
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, *args):
                pass

        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_port}/sparql"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


class FetchWikidataTest(unittest.TestCase):
    def setUp(self):
        self.stub = Stub()
        self.tmp = tempfile.TemporaryDirectory()
        self.dest = self.tmp.name
        self.tsv = os.path.join(self.dest, f"wikidata-p1814-ja-{DATE}.tsv")

    def tearDown(self):
        self.stub.close()
        self.tmp.cleanup()

    def run_fetch(self, stub=None, dest=None):
        stub = stub or self.stub
        fw.main([dest or self.dest, "--date", DATE, "--endpoint", stub.url, "--backoff", "0"])

    def read_tsv(self, path=None):
        with open(path or self.tsv, "rb") as fh:
            return fh.read().decode("utf-8")

    def test_happy_path_writes_sorted_tsv_and_sha256(self):
        self.run_fetch()
        self.assertEqual(self.read_tsv(), EXPECTED_TSV)
        with open(self.tsv + ".sha256", encoding="utf-8") as fh:
            self.assertTrue(fh.read().endswith(f"  {os.path.basename(self.tsv)}\n"))
        self.assertEqual(self.stub.requests, {"classes": 1, "sitelinks": 1, "labels": 1})

    def test_timeout_marker_and_truncated_json_are_retried(self):
        self.stub.script["labels"] = [
            (200, b'{"results": {"bindings": [ {"item" : {"value" : "x"}, "label" : {"type" : "literal'
                  b'SPARQL-QUERY: ...\njava.util.concurrent.TimeoutException\n\tat ...'),
            (200, b'{"results": {"bindings": [ {"item"'),  # truncated JSON without the marker
        ]
        self.run_fetch()
        self.assertEqual(self.stub.requests["labels"], 3)
        self.assertEqual(self.read_tsv(), EXPECTED_TSV)

    def test_http_503_and_429_are_retried(self):
        self.stub.script["classes"] = [(503, b"busy"), (429, b"slow down")]
        self.run_fetch()
        self.assertEqual(self.stub.requests["classes"], 3)
        self.assertEqual(self.read_tsv(), EXPECTED_TSV)

    def test_http_400_aborts_immediately(self):
        self.stub.script["classes"] = [(400, b"bad query")]
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self.run_fetch()
        self.assertEqual(ctx.exception.code, 400)
        self.assertEqual(self.stub.requests, {"classes": 1, "sitelinks": 0, "labels": 0})
        self.assertFalse(os.path.exists(self.tsv))
        self.assertFalse(os.path.exists(self.tsv + ".part"))

    def test_gives_up_after_all_attempts(self):
        self.stub.script["sitelinks"] = [(500, b"")] * 5
        with self.assertRaises(SystemExit):
            self.run_fetch()
        self.assertEqual(self.stub.requests["sitelinks"], 5)
        self.assertFalse(os.path.exists(self.tsv))

    def test_interrupted_write_is_not_reused(self):
        with mock.patch.object(fw.os, "replace", side_effect=KeyboardInterrupt), self.assertRaises(KeyboardInterrupt):
            self.run_fetch()  # dies after the first raw response, before anything is renamed into place
        self.assertFalse(os.path.exists(self.tsv))
        with open(self.tsv + ".part", "w", encoding="utf-8", newline="\n") as fh:
            fh.write("qid\tlabel_ja\tkana\tp31s\tsitelinks\nQ9\tpartial\tぱーしゃる\t\t0\n")
        self.run_fetch()
        self.assertEqual(self.read_tsv(), EXPECTED_TSV)
        self.assertFalse(os.path.exists(self.tsv + ".part"))

    def test_interrupted_tsv_rename_leaves_no_snapshot(self):
        real_replace = os.replace

        def fail_on_tsv(src, dst):
            if dst == self.tsv:
                raise KeyboardInterrupt
            real_replace(src, dst)

        with mock.patch.object(fw.os, "replace", side_effect=fail_on_tsv), self.assertRaises(KeyboardInterrupt):
            self.run_fetch()
        self.assertFalse(os.path.exists(self.tsv))
        self.assertTrue(os.path.exists(self.tsv + ".part"))
        before = dict(self.stub.requests)
        self.run_fetch()  # raw responses are cached, so this rebuilds the TSV without the network
        self.assertEqual(self.stub.requests, before)
        self.assertEqual(self.read_tsv(), EXPECTED_TSV)

    def test_cached_raw_json_is_reused_without_network(self):
        self.run_fetch()
        first = self.read_tsv()
        os.remove(self.tsv)
        offline = Stub()
        try:
            self.run_fetch(stub=offline)
            self.assertEqual(offline.requests, {"classes": 0, "sitelinks": 0, "labels": 0})
        finally:
            offline.close()
        self.assertEqual(self.read_tsv(), first)

    def test_cached_tsv_is_reused_without_network(self):
        self.run_fetch()
        before = dict(self.stub.requests)
        self.run_fetch()
        self.assertEqual(self.stub.requests, before)

    def test_tsv_is_deterministic_regardless_of_response_order(self):
        self.run_fetch()
        reversed_stub = Stub(reverse=True)
        with tempfile.TemporaryDirectory() as other:
            try:
                self.run_fetch(stub=reversed_stub, dest=other)
            finally:
                reversed_stub.close()
            self.assertEqual(self.read_tsv(os.path.join(other, os.path.basename(self.tsv))), self.read_tsv())


if __name__ == "__main__":
    unittest.main()
