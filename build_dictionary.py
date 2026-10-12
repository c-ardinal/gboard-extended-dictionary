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
# Per-file import limits of the Extended Dictionary patch; larger lists are written as -part1, -part2, ...
MAX_PART_ROWS = 100_000
MAX_PART_BYTES = 8 * 1024 * 1024
ZIP_OVERHEAD_BYTES = 1024  # stored single-entry zip headers, so the .zip also stays within MAX_PART_BYTES
PART_BYTE_BUDGET = MAX_PART_BYTES - ZIP_OVERHEAD_BYTES
DEFAULT_SIZES = "1000,10000,50000,all"
# Base "all" (about 85k) + extra must stay under the 200,000 rows Gboard hands to Mozc, with room for user entries.
MAX_EXTRA_ROWS = 100_000
EXTRA_NAME = "sudachi-ja-notcore-extra"
NAME_POS = "人名"

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
# Hangul (Jamo, Compatibility Jamo, Syllables) is not Japanese input either.
_COMPAT_CHARS = re.compile(r"[\u1100-\u11ff\u2100-\u218f\u3130-\u318f\u3200-\u33ff\uac00-\ud7af\uf900-\ufaff\s]")
_READING_OK = re.compile(r"[\u3041-\u3096\u30a1-\u30fa\u30fc0-9a-z]+")
# Word separators in Sudachi readings (\u307e\u3044\u3051\u308b\u30fb\u3058\u3083\u304f\u305d\u3093); removed rather than rejecting the row.
_READING_SEPARATORS = re.compile("[\u30fb=]")


def unescape(text):
    return _UNICODE_ESCAPE.sub(lambda m: chr(int(m.group(1), 16)), text)


def katakana_to_hiragana(text):
    # U+30A1..U+30F6 (incl. ヴ U+30F4 -> ゔ U+3094, ヵヶ) shift by 0x60; ー U+30FC and ヷ-ヺ have no hiragana and stay.
    return "".join(chr(ord(c) - 0x60) if "\u30a1" <= c <= "\u30f6" else c for c in text)


def normalize_reading(raw):
    """Return a hiragana reading without ・/= separators, or None if anything but kana, ー or [0-9a-z] remains."""
    reading = _READING_SEPARATORS.sub("", katakana_to_hiragana(unescape(raw)).lower())
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
        if _READING_SEPARATORS.search(row[COL_READING]):
            stats["reading_separators_removed"] += 1
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


def render_row(entry):
    reading, surface, pos = entry[:3]
    return f"{reading}\t{surface}\t{LANGUAGE_TAG}\t{pos}\n"


def render(entries):
    return HEADER + "".join(map(render_row, entries))


def validate_text(text):
    """Re-parse with the importer's column rules; return a list of violation messages (empty = valid)."""
    errors = []
    if text.startswith(chr(0xFEFF)):
        errors.append("BOM present")
    if "\r" in text:
        errors.append("CR present")
    if not text.startswith(HEADER):
        errors.append("header mismatch")
    if len(text.encode("utf-8")) > MAX_PART_BYTES:
        errors.append(f"file larger than {MAX_PART_BYTES} bytes")
    if text.count("\n") - 2 > MAX_PART_ROWS:
        errors.append(f"more than {MAX_PART_ROWS} rows")
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
    info.compress_type = zipfile.ZIP_STORED  # no zlib involved, so bytes match on every platform
    info.create_system = 3  # Unix, so the result does not depend on the build OS
    info.external_attr = 0o100644 << 16
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr(info, data)


def size_name(size):
    if size is None:
        return "sudachi-ja-all"
    return f"sudachi-ja-{size // 1000}k" if size % 1000 == 0 else f"sudachi-ja-{size}"


def split_parts(entries, max_rows=None, max_bytes=None):
    """Split entries, in order, into parts that each render to at most max_rows rows and max_bytes bytes."""
    max_rows = max_rows or MAX_PART_ROWS
    max_bytes = max_bytes or PART_BYTE_BUDGET
    parts, current, size = [], [], len(HEADER.encode("utf-8"))
    for entry in entries:
        row_bytes = len(render_row(entry).encode("utf-8"))
        if current and (len(current) == max_rows or size + row_bytes > max_bytes):
            parts.append(current)
            current, size = [], len(HEADER.encode("utf-8"))
        current.append(entry)
        size += row_bytes
    parts.append(current)
    return parts


def write_outputs(files, out_dir, stats):
    """files: [(name, entries)]. Each is split into importable parts, self-checked, written as .txt and .zip,
    and recorded in stats/sha256.txt. A single part keeps the plain name; otherwise <name>-part1, -part2, ..."""
    os.makedirs(out_dir, exist_ok=True)
    checksums = []
    for name, subset in files:
        parts = split_parts(subset)
        part_names = [name] if len(parts) == 1 else [f"{name}-part{i}" for i in range(1, len(parts) + 1)]
        for part_name, part in zip(part_names, parts):
            text = render(part)
            errors = validate_text(text)
            if errors:
                raise SystemExit(f"self-check failed for {part_name}: {len(errors)} violations, first: {errors[0]}")
            data = text.encode("utf-8")
            txt_path = os.path.join(out_dir, part_name + ".txt")
            with open(txt_path, "wb") as fh:
                fh.write(data)
            zip_path = os.path.join(out_dir, part_name + ".zip")
            write_zip(zip_path, data)
            for path in (txt_path, zip_path):
                with open(path, "rb") as fh:
                    checksums.append(f"{hashlib.sha256(fh.read()).hexdigest()}  {os.path.basename(path)}\n")
        stats["outputs"][name] = {
            "rows": len(subset),
            "pos": dict(collections.Counter(e[2] for e in subset).most_common()),
            "cost_cutoff": subset[-1][3] if subset else None,
            "parts": [{"name": n, "rows": len(p)} for n, p in zip(part_names, parts)],
        }
    with open(os.path.join(out_dir, "sha256.txt"), "w", encoding="utf-8", newline="\n") as fh:
        fh.writelines(checksums)


def build_entries(paths, mozc_surfaces, stats, dropped_pos):
    best = dedupe(filter_entries(iter_sudachi_rows(paths), stats, dropped_pos), stats)
    stats["after_dedupe"] = len(best)
    return select_entries(best, mozc_surfaces, stats)


def order_extra(entries, base_keys, stats, cap=MAX_EXTRA_ROWS):
    """Rows not already in base; non-人名 first, then 人名, each by cost; cut to cap."""
    extra = [e for e in entries if e[:2] not in base_keys]
    stats["drop_in_base"] = len(entries) - len(extra)
    ordered = sorted(extra, key=lambda e: (e[2] == NAME_POS, e[3], e[0], e[1]))
    return extra, ordered[:cap]


# --- Wikidata (CC0) proper nouns -------------------------------------------------------------
# P31 (instance of) -> Gboard POS. Precedence: drop > 人名 > 組織 > 地名; anything else -> 固有名詞.
# Output order: sitelinks (number of Wikipedia/Wikimedia pages) descending, then QID ascending.
# Built from the most frequent classes among items with a kana reading (P1814), 2026-10-12.
WIKIDATA_NAME = "wikidata-ja-proper"
WIKIDATA_HEADER = ("qid", "label_ja", "kana", "p31s", "sitelinks")
WIKIDATA_MAX_SURFACE = 32
WIKIDATA_DROP = {  # name lists and Wikimedia pages are not words
    "Q101352", "Q19914123",  # family name, Japanese family name
    "Q202444", "Q22812564", "Q12308941", "Q11879590", "Q3409032",  # given names (generic, ja, male, female, unisex)
    "Q4167410", "Q13406463",  # disambiguation page, list article
    # biological taxa (common nouns such as 偶蹄目, not proper nouns): taxon, fossil taxon, organisms known by
    # a common name, monotypic taxon, clade, and rank classes
    "Q16521", "Q23038290", "Q55983715", "Q310890", "Q713623",
    "Q7432", "Q34740", "Q35409", "Q36602", "Q37517", "Q38348", "Q36732", "Q767728",
}
WIKIDATA_PERSON = {"Q5"}  # human
WIKIDATA_ORGANISATION = {
    "Q43229", "Q4830453", "Q6881511", "Q891723", "Q1480166", "Q163740", "Q327333",  # organisation, business, company...
    "Q85907346", "Q10438042", "Q11086540", "Q5354754", "Q1616075", "Q18127",  # rail/bus companies, agencies, TV, label
    "Q3914", "Q5358913", "Q55521176", "Q56351315", "Q43271061", "Q11396122",  # schools in Japan
    "Q3918", "Q38723", "Q875538", "Q902104", "Q2385804", "Q31855", "Q11507944",  # universities, institutes
}
WIKIDATA_PLACE = {
    # Shinto shrines and their classes
    "Q845945", "Q134917286", "Q135160342", "Q135160338", "Q135022904", "Q135038714", "Q135100459", "Q135419779",
    "Q11390939", "Q514480", "Q3200355", "Q11588442", "Q2370082", "Q11590310", "Q125316862", "Q11667187",
    "Q798838", "Q125339990", "Q125316983", "Q125313404", "Q125314093", "Q125314171", "Q32512", "Q134987077",
    "Q11451876", "Q11562963", "Q11355275", "Q118304363", "Q11646488", "Q137640533", "Q135009132", "Q11635468",
    "Q134986959", "Q11488964", "Q11391618", "Q135009906", "Q135009973", "Q135009904", "Q1442984",
    # Buddhist temples
    "Q5393308", "Q11486287", "Q10901138",
    # stations and stops
    "Q55488", "Q4312270", "Q85882206", "Q123094976", "Q122397434", "Q4346602", "Q332496", "Q121844689",
    "Q11670533", "Q2142091", "Q121844395", "Q1147171", "Q11606300", "Q22808404", "Q2175765", "Q928830",
    "Q2451577", "Q105038730", "Q22808403", "Q123025722", "Q55485", "Q4663385", "Q55491", "Q6641445",
    "Q124416148", "Q7886778", "Q11429081", "Q63125083", "Q123114662", "Q953806",
    # municipalities (current and dissolved), districts, neighbourhoods, toponyms
    "Q18663566", "Q494721", "Q1059478", "Q4174776", "Q137773", "Q1549591", "Q1137833", "Q15284", "Q515", "Q3957",
    "Q5327369", "Q424857", "Q5327509", "Q65948724", "Q486972", "Q123705", "Q11484330", "Q7884789",
    "Q1122846", "Q860290", "Q12357832", "Q56061", "Q1147395", "Q48336",
    # nature, parks, hot springs
    "Q8502", "Q4022", "Q2908753", "Q23442", "Q22698", "Q11643861", "Q11832860",
    "Q655311", "Q11408869", "Q11428612", "Q11567142", "Q11587679", "Q11587674",
    # roads, bridges, road facilities, castles, sites
    "Q34442", "Q79007", "Q10276558", "Q46622", "Q16939396", "Q5409910", "Q285783", "Q2376564", "Q1788454",
    "Q11313284", "Q785979", "Q60985097", "Q1364150", "Q12280", "Q537127", "Q1055465", "Q39715",
    "Q92026", "Q839954", "Q1081138",
}
PLACE_POS, ORGANISATION_POS, PROPER_POS = "地名", "組織", "固有名詞"
_DISAMBIGUATOR = re.compile("[(（]")
_WHITESPACE = re.compile(r"\s+")
_SURFACE_KANA = re.compile(r"[\u3041-\u3096\u30a1-\u30fa\u30fc]")
_KANJI = re.compile(r"[\u3005\u3400-\u4dbf\u4e00-\u9fff]")
# For the subsequence check only: \u30f6/\u30f5 (\u3096/\u3095 after conversion) are counters read \u304c/\u304b/\u3053, so they are skipped;
# historical and voiced spellings are folded to the form a modern reading uses.
_KANA_EQUIVALENCE = str.maketrans({"\u3090": "\u3044", "\u3091": "\u3048", "\u3062": "\u3058", "\u3065": "\u305a", "\u3094": "\u3076", "\u3095": None, "\u3096": None})


def reading_matches_surface(reading, surface):
    """False when the reading cannot belong to the whole surface (P1814 is the reading of the name, not the label).

    (a) kana written in the surface must appear in the reading in the same order (as a subsequence),
        after _KANA_EQUIVALENCE on both sides;
    (b) the reading needs at least one character per kanji.
    """
    folded = reading.translate(_KANA_EQUIVALENCE)
    surface_kana = "".join(_SURFACE_KANA.findall(katakana_to_hiragana(surface))).translate(_KANA_EQUIVALENCE)
    position = 0
    for char in surface_kana:
        position = folded.find(char, position) + 1
        if position == 0:
            return False
    return len(reading) >= len(_KANJI.findall(surface))


def wikidata_pos(p31s):
    """Map an item's P31 classes to a Gboard label, or None to drop the item."""
    classes = set(p31s)
    if classes & WIKIDATA_DROP:
        return None
    if classes & WIKIDATA_PERSON:
        return NAME_POS
    if classes & WIKIDATA_ORGANISATION:
        return ORGANISATION_POS
    if classes & WIKIDATA_PLACE:
        return PLACE_POS
    return PROPER_POS


def iter_wikidata(path):
    with open(path, encoding="utf-8", newline="") as fh:
        header = tuple(fh.readline().rstrip("\r\n").split("\t"))
        if header != WIKIDATA_HEADER:
            raise SystemExit(f"{path}: unexpected TSV header {header}")
        for line in fh:
            yield line.rstrip("\r\n").split("\t")


def filter_wikidata(rows, stats):
    """Yield (reading, surface, pos, rank) for Wikidata rows passing the filters; rank = (-sitelinks, QID number)."""
    readings_per_item = collections.Counter()
    for row in rows:
        stats["rows_in"] += 1
        if len(row) != len(WIKIDATA_HEADER):
            stats["drop_malformed_row"] += 1
            continue
        qid, surface, kana, p31s, sitelinks = row
        readings_per_item[qid] += 1
        pos = wikidata_pos(p31s.split(",") if p31s else [])
        if pos is None:
            stats["drop_name_or_wikimedia_item"] += 1
            continue
        if _DISAMBIGUATOR.search(surface):
            stats["drop_parenthetical"] += 1
            continue
        if len(surface) > WIKIDATA_MAX_SURFACE:
            stats["drop_surface_over_32"] += 1
            continue
        if not surface or has_bad_surface_chars(surface):
            stats["drop_surface_chars"] += 1
            continue
        if surface.isascii():
            stats["drop_ascii_surface"] += 1
            continue
        if _COMPAT_CHARS.search(surface):
            stats["drop_compat_surface"] += 1
            continue
        if _READING_SEPARATORS.search(kana):
            stats["reading_separators_removed"] += 1
        reading = normalize_reading(_WHITESPACE.sub("", kana))
        if reading is None:
            stats["drop_reading_chars"] += 1
            continue
        if len(reading) > MAX_CHARS:
            stats["drop_over_256"] += 1
            continue
        if reading == surface:
            stats["drop_reading_eq_surface"] += 1
            continue
        if reading == katakana_to_hiragana(surface).lower():
            stats["drop_kana_or_case_variant"] += 1
            continue
        if not reading_matches_surface(reading, surface):
            stats["drop_reading_mismatch"] += 1
            continue
        stats["after_row_filters"] += 1
        yield reading, surface, pos, (-int(sitelinks), int(qid[1:]))
    stats["multi_reading_items"] = sum(1 for n in readings_per_item.values() if n > 1)


def build_wikidata(path, mozc_surfaces, known_keys, stats):
    """Wikidata rows not already in the Sudachi lists or Mozc OSS, most sitelinks first, then lowest QID."""
    best = dedupe(filter_wikidata(iter_wikidata(path), stats), stats)
    stats["after_dedupe"] = len(best)
    fresh = {k: v for k, v in best.items() if k not in known_keys}
    stats["drop_in_sudachi_lists"] = len(best) - len(fresh)
    return select_entries(fresh, mozc_surfaces, stats)  # sorted by the rank in the "cost" slot


def file_sha256(path):
    with open(path, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--base", nargs="+", required=True, help="SudachiDict *_lex.csv for the base lists (small, core)")
    ap.add_argument("--extra", nargs="*", default=[], help="SudachiDict *_lex.csv for the extra list (notcore)")
    ap.add_argument("--wikidata", help="TSV from tools/fetch_wikidata.py for the optional Wikidata list")
    ap.add_argument("--mozc-dir", required=True, help="directory with Mozc OSS dictionary0*.txt")
    ap.add_argument("--out", default="out")
    ap.add_argument("--sizes", type=parse_sizes, default=parse_sizes(DEFAULT_SIZES), help=f"comma list, 'all' = every row (default {DEFAULT_SIZES})")
    args = ap.parse_args(argv)

    started = time.monotonic()
    mozc_files = sorted(glob.glob(os.path.join(args.mozc_dir, "dictionary*.txt")))
    if not mozc_files:
        raise SystemExit(f"no dictionary*.txt under {args.mozc_dir}")
    mozc_surfaces = load_mozc_surfaces(mozc_files)
    stats = collections.Counter()
    dropped_pos = collections.Counter()
    entries = build_entries(args.base, mozc_surfaces, stats, dropped_pos)
    if len(entries) < max((s for s in args.sizes if s is not None), default=0):
        print(f"warning: only {len(entries)} entries available", file=sys.stderr)

    report = {
        "inputs": [os.path.basename(p) for p in args.base],
        "mozc_files": len(mozc_files),
        "mozc_surfaces": len(mozc_surfaces),
        "stages": dict(stats),
        "available_pos": dict(collections.Counter(e[2] for e in entries).most_common()),
        "dropped_pos_top": dict(dropped_pos.most_common(30)),
        "outputs": {},
    }
    files = [(size_name(size), entries[:size]) for size in args.sizes]
    if args.extra:
        extra_stats = collections.Counter()
        extra_all = build_entries(args.extra, mozc_surfaces, extra_stats, collections.Counter())
        available, extra = order_extra(extra_all, {e[:2] for e in entries}, extra_stats)
        report["extra"] = {
            "inputs": [os.path.basename(p) for p in args.extra],
            "stages": dict(extra_stats),
            "available": len(available),
            "available_pos": dict(collections.Counter(e[2] for e in available).most_common()),
            "cap": MAX_EXTRA_ROWS,
            "written": len(extra),
            "written_name_rows": sum(1 for e in extra if e[2] == NAME_POS),
        }
        files.append((EXTRA_NAME, extra))
    if args.wikidata:
        wd_stats = collections.Counter()
        known = {e[:2] for e in entries}
        if args.extra:
            known |= {e[:2] for e in extra}  # the capped extra file as published
        wikidata = build_wikidata(args.wikidata, mozc_surfaces, known, wd_stats)
        name = os.path.basename(args.wikidata)
        report["wikidata"] = {
            "input": name,
            "date": name.rsplit("-", 1)[-1].removesuffix(".tsv"),
            "input_rows": wd_stats["rows_in"],
            "input_sha256": file_sha256(args.wikidata),
            "stages": dict(wd_stats),
            "available_pos": dict(collections.Counter(e[2] for e in wikidata).most_common()),
            "first_50k_pos": dict(collections.Counter(e[2] for e in wikidata[:50_000]).most_common()),
        }
        files.append((WIKIDATA_NAME, wikidata))
    write_outputs(files, args.out, report)
    report["elapsed_sec"] = round(time.monotonic() - started, 1)
    with open(os.path.join(args.out, "stats.json"), "w", encoding="utf-8", newline="\n") as fh:
        json.dump(report, fh, ensure_ascii=False, indent=1)
        fh.write("\n")
    print(json.dumps({"stages": report["stages"], "elapsed_sec": report["elapsed_sec"],
                      "outputs": {k: v["rows"] for k, v in report["outputs"].items()}}, ensure_ascii=False))


if __name__ == "__main__":
    main()
