"""Convert SudachiDict lexicon CSVs into Gboard personal-dictionary word lists (ja-JP nouns)."""
import argparse
import collections
import glob
import hashlib
import json
import os
import re
import sys
import time
import zipfile

HEADER = (
    "# Gboard Dictionary version:2\n"
    "# Gboard Dictionary format:shortcut\tword\tlanguage_tag\tpos_tag\n"
)
LANGUAGE_TAG = "ja-JP"
MAX_CHARS = 256
DEFAULT_SIZES = "1000,10000,50000,all"

# arrays.xml:2510-2556 of Gboard 18.0.3 (item 0 is the placeholder; the rest are real labels).
GBOARD_POS_ITEMS = (
    "品詞なし", "名詞", "短縮よみ", "サジェストのみ", "固有名詞", "人名", "姓", "名", "組織", "地名",
    "名詞サ変", "名詞形動", "数", "アルファベット", "記号", "顔文字", "副詞", "連体詞", "接続詞", "感動詞",
    "接頭語", "助数詞", "接尾一般", "接尾人名", "接尾地名", "動詞ワ行五段", "動詞カ行五段", "動詞サ行五段",
    "動詞タ行五段", "動詞ナ行五段", "動詞マ行五段", "動詞ラ行五段", "動詞ガ行五段", "動詞バ行五段",
    "動詞ハ行四段", "動詞一段", "動詞カ変", "動詞サ変", "動詞ザ変", "動詞ラ変", "形容詞", "終助詞",
    "句読点", "独立語", "抑制単語",
)
GBOARD_POS_LABELS = frozenset(GBOARD_POS_ITEMS[1:])

# Sudachi lexicon CSV columns (header row: IndexForm,LeftId,RightId,Cost,Headword,POS1..POS6,ReadingForm,...).
COL_INDEX, COL_LEFT_ID, COL_RIGHT_ID, COL_COST, COL_HEADWORD, COL_POS1, COL_READING = 0, 1, 2, 3, 4, 5, 11
EXPECTED_HEADER = ("IndexForm", "LeftId", "RightId", "Cost", "Headword",
                   "POS1", "POS2", "POS3", "POS4", "POS5", "POS6", "ReadingForm")
MIN_COLUMNS = 13

_UNICODE_ESCAPE = re.compile(r"\\u([0-9a-fA-F]{4})")
# Presentation forms and spacing, not words: Letterlike Symbols / Number Forms, Enclosed CJK,
# CJK Compatibility, CJK Compatibility Ideographs, and any Unicode whitespace (incl. U+2003, U+3000).
_COMPAT_CHARS = re.compile(r"[\u2100-\u218f\u3200-\u33ff\uf900-\ufaff\s]")
_READING_OK = re.compile(r"[\u3041-\u3096\u30a1-\u30fa\u30fc0-9a-z]+")


def unescape(text):
    return _UNICODE_ESCAPE.sub(lambda m: chr(int(m.group(1), 16)), text)


def katakana_to_hiragana(text):
    # U+30A1..U+30F6 (incl. ヴ U+30F4 -> ゔ U+3094, ヵヶ) shift by 0x60; ー U+30FC and ヷ-ヺ have no hiragana and stay.
    return "".join(chr(ord(c) - 0x60) if "\u30a1" <= c <= "\u30f6" else c for c in text)


def normalize_reading(raw):
    """Return a hiragana reading, or None if it contains anything but kana, ー or ASCII letters/digits."""
    reading = katakana_to_hiragana(unescape(raw)).lower()
    return reading if _READING_OK.fullmatch(reading) else None


def map_pos(pos):
    """Map Sudachi POS1..POS3/4 to a Gboard label; None for classes outside the v1 noun subset."""
    p1, p2, p3, p4 = pos[0], pos[1], pos[2], pos[3]
    if p1 != "名詞":
        return None
    if p2 == "普通名詞":
        return {"サ変可能": "名詞サ変", "形状詞可能": "名詞形動"}.get(p3, "名詞")
    if p2 == "固有名詞":
        if p3 == "一般":
            return "固有名詞"
        if p3 == "組織名":
            return "組織"
        if p3 == "人名" and p4 not in ("姓", "名"):
            return "人名"
    return None


def has_bad_surface_chars(surface):
    return surface != surface.strip() or any(ord(c) < 0x20 or 0x7F <= ord(c) <= 0x9F for c in surface)


def iter_sudachi_rows(paths):
    for path in paths:
        with open(path, encoding="utf-8", newline="") as fh:
            first = fh.readline().rstrip("\r\n").split(",")
            if len(first) > COL_COST and not first[COL_COST].lstrip("-").isdigit():
                if tuple(first[:len(EXPECTED_HEADER)]) != EXPECTED_HEADER:
                    raise SystemExit(f"{path}: unexpected CSV header {first[:len(EXPECTED_HEADER)]}")
            else:
                print(f"{path}: no header row, first line read as data", file=sys.stderr)
                yield first
            for line in fh:
                # Sudachi escapes commas inside fields as \u002c, so a plain split is exact (csv quoting would misparse '"').
                yield line.rstrip("\r\n").split(",")


def filter_entries(rows, stats, dropped_pos):
    """Yield (reading, surface, pos, cost) for rows passing POS / reading / surface rules."""
    for row in rows:
        stats["rows_in"] += 1
        if len(row) < MIN_COLUMNS:
            stats["drop_malformed_row"] += 1
            continue
        if row[COL_LEFT_ID] == "-1" or row[COL_RIGHT_ID] == "-1":
            # Reference/variant entries not used for analysis; all have cost 0 and would sort to the top.
            stats["drop_unconnected"] += 1
            continue
        pos = map_pos(row[COL_POS1:COL_POS1 + 6])
        if pos is None:
            stats["drop_pos_excluded"] += 1
            dropped_pos["-".join(p for p in row[COL_POS1:COL_POS1 + 4] if p != "*")] += 1
            continue
        surface = unescape(row[COL_HEADWORD] or row[COL_INDEX])
        if not surface or has_bad_surface_chars(surface):
            stats["drop_surface_chars"] += 1
            continue
        if surface.isascii():
            stats["drop_ascii_surface"] += 1
            continue
        if _COMPAT_CHARS.search(surface):
            stats["drop_compat_surface"] += 1
            continue
        reading = normalize_reading(row[COL_READING])
        if reading is None:
            stats["drop_reading_chars"] += 1
            continue
        if len(reading) > MAX_CHARS or len(surface) > MAX_CHARS:
            stats["drop_over_256"] += 1
            continue
        if reading == surface:
            stats["drop_reading_eq_surface"] += 1
            continue
        if reading == katakana_to_hiragana(surface).lower():
            stats["drop_kana_or_case_variant"] += 1
            continue
        stats["after_row_filters"] += 1
        yield reading, surface, pos, int(row[COL_COST])


def dedupe(entries, stats):
    """Keep one entry per (reading, surface): the lowest cost (first seen wins ties)."""
    best = {}
    for reading, surface, pos, cost in entries:
        key = (reading, surface)
        if key in best:
            stats["drop_duplicate"] += 1
            if cost >= best[key][1]:
                continue
        best[key] = (pos, cost)
    return best


def load_mozc_surfaces(paths):
    surfaces = set()
    for path in paths:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                cols = line.rstrip("\n").split("\t")
                if len(cols) >= 5:
                    surfaces.add(cols[4])
    return surfaces


def select_entries(best, mozc_surfaces, stats):
    kept = [(cost, reading, surface, pos) for (reading, surface), (pos, cost) in best.items()
            if surface not in mozc_surfaces]
    stats["drop_in_mozc_oss"] = len(best) - len(kept)
    kept.sort()
    stats["after_mozc_filter"] = len(kept)
    return [(reading, surface, pos, cost) for cost, reading, surface, pos in kept]


def render(entries):
    return HEADER + "".join(f"{r}\t{s}\t{LANGUAGE_TAG}\t{p}\n" for r, s, p, *_ in entries)


def validate_text(text):
    """Re-parse with the importer's column rules; return a list of violation messages (empty = valid)."""
    errors = []
    if text.startswith("﻿"):
        errors.append("BOM present")
    if "\r" in text:
        errors.append("CR present")
    if not text.startswith(HEADER):
        errors.append("header mismatch")
    for no, line in enumerate(text.split("\n")[2:], start=3):
        if line == "":
            continue
        cols = line.split("\t")
        if len(cols) != 4:
            errors.append(f"line {no}: {len(cols)} columns")
            continue
        reading, surface, tag, pos = cols
        if not surface or len(surface) > MAX_CHARS or len(reading) > MAX_CHARS:
            errors.append(f"line {no}: bad word/reading length")
        if not _READING_OK.fullmatch(reading):
            errors.append(f"line {no}: bad reading charset")
        if tag != LANGUAGE_TAG or pos not in GBOARD_POS_LABELS:
            errors.append(f"line {no}: bad language_tag/pos")
    return errors


def parse_sizes(text):
    """"1000,all" -> [1000, None]; None selects every filtered row."""
    return [None if s.strip() == "all" else int(s) for s in text.split(",")]


def write_zip(path, data):
    """Write a single-entry zip with fixed metadata so identical input gives identical bytes."""
    info = zipfile.ZipInfo("dictionary.txt", date_time=(1980, 1, 1, 0, 0, 0))
    info.compress_type = zipfile.ZIP_DEFLATED
    info.create_system = 3  # Unix, so the result does not depend on the build OS
    info.external_attr = 0o100644 << 16
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr(info, data, compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)


def write_outputs(entries, sizes, out_dir, stats):
    os.makedirs(out_dir, exist_ok=True)
    checksums = []
    for size in sizes:
        subset = entries[:size]  # size None = every filtered row
        text = render(subset)
        errors = validate_text(text)
        if errors:
            raise SystemExit(f"self-check failed for top-{size}: {len(errors)} violations, first: {errors[0]}")
        if size is None:
            name = "sudachi-ja-all"
        else:
            name = f"sudachi-ja-{size // 1000}k" if size % 1000 == 0 else f"sudachi-ja-{size}"
        data = text.encode("utf-8")
        txt_path = os.path.join(out_dir, name + ".txt")
        with open(txt_path, "wb") as fh:
            fh.write(data)
        zip_path = os.path.join(out_dir, name + ".zip")
        write_zip(zip_path, data)
        for path in (txt_path, zip_path):
            with open(path, "rb") as fh:
                checksums.append(f"{hashlib.sha256(fh.read()).hexdigest()}  {os.path.basename(path)}\n")
        stats["outputs"][name] = {
            "rows": len(subset),
            "pos": dict(collections.Counter(e[2] for e in subset).most_common()),
            "cost_cutoff": subset[-1][3] if subset else None,
        }
    with open(os.path.join(out_dir, "sha256.txt"), "w", encoding="utf-8", newline="\n") as fh:
        fh.writelines(checksums)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--sudachi", nargs="+", required=True, help="SudachiDict *_lex.csv files (small, core)")
    ap.add_argument("--mozc-dir", required=True, help="directory with Mozc OSS dictionary0*.txt")
    ap.add_argument("--out", default="out")
    ap.add_argument("--sizes", type=parse_sizes, default=parse_sizes(DEFAULT_SIZES), help=f"comma list, 'all' = every row (default {DEFAULT_SIZES})")
    args = ap.parse_args(argv)

    started = time.monotonic()
    mozc_files = sorted(glob.glob(os.path.join(args.mozc_dir, "dictionary*.txt")))
    if not mozc_files:
        raise SystemExit(f"no dictionary*.txt under {args.mozc_dir}")
    stats = collections.Counter()
    dropped_pos = collections.Counter()
    best = dedupe(filter_entries(iter_sudachi_rows(args.sudachi), stats, dropped_pos), stats)
    stats["after_dedupe"] = len(best)
    mozc_surfaces = load_mozc_surfaces(mozc_files)
    entries = select_entries(best, mozc_surfaces, stats)
    if len(entries) < max((s for s in args.sizes if s is not None), default=0):
        print(f"warning: only {len(entries)} entries available", file=sys.stderr)

    report = {
        "inputs": [os.path.basename(p) for p in args.sudachi],
        "mozc_files": len(mozc_files),
        "mozc_surfaces": len(mozc_surfaces),
        "stages": dict(stats),
        "available_pos": dict(collections.Counter(e[2] for e in entries).most_common()),
        "dropped_pos_top": dict(dropped_pos.most_common(30)),
        "outputs": {},
    }
    write_outputs(entries, args.sizes, args.out, report)
    report["elapsed_sec"] = round(time.monotonic() - started, 1)
    with open(os.path.join(args.out, "stats.json"), "w", encoding="utf-8", newline="\n") as fh:
        json.dump(report, fh, ensure_ascii=False, indent=1)
        fh.write("\n")
    print(json.dumps({"stages": report["stages"], "elapsed_sec": report["elapsed_sec"],
                      "outputs": {k: v["rows"] for k, v in report["outputs"].items()}}, ensure_ascii=False))


if __name__ == "__main__":
    main()
