# gboard-extended-dictionary

日本語: [README.md](README.md)

Japanese dictionaries for the Gboard "Extended Dictionary" patch, built from SudachiDict and Wikidata.
Each output file is a Gboard personal-dictionary word list that can be imported as is.

## Output format

Gboard personal-dictionary word list, version 2, UTF-8 without BOM, LF line endings:

```
# Gboard Dictionary version:2
# Gboard Dictionary format:shortcut<TAB>word<TAB>language_tag<TAB>pos_tag
けんきゅう<TAB>研究<TAB>ja-JP<TAB>名詞サ変
```

- Columns: reading (hiragana), word, `ja-JP`, part-of-speech label. Every row has exactly 4 columns.
- The POS label is one of Gboard's Japanese labels. This build emits seven: 名詞, 名詞サ変, 名詞形動, 固有名詞, 組織, 人名 and 地名 (地名 only in the Wikidata dictionary).
- Each `.zip` holds one entry, `dictionary.txt`, with the same content as the `.txt`.
- Zips are byte-identical on every platform: fixed timestamp (1980-01-01), fixed attributes, stored without compression (size about equal to the .txt).

## Inputs (pinned)

| Input | URL | SHA-256 |
|---|---|---|
| SudachiDict small_lex.zip (release v20260723) | https://sudachi.s3-ap-northeast-1.amazonaws.com/sudachidict-raw/v1/20260723/small_lex.zip | e49936daef64043657752eb9f4ada912cf0316e26dd158a6200c770e2f93e706 |
| SudachiDict core_lex.zip (release v20260723) | https://sudachi.s3-ap-northeast-1.amazonaws.com/sudachidict-raw/v1/20260723/core_lex.zip | d8ed376d8ff368226314a43151ab02591378dd344bff017d8a93ae9839212edf |
| SudachiDict notcore_lex.zip (release v20260723) | https://sudachi.s3-ap-northeast-1.amazonaws.com/sudachidict-raw/v1/20260723/notcore_lex.zip | 75aa2d8cb855c7a44a326dc9f43f78cb6dc881caccb996e7559e2513b69cb0e1 |
| Mozc OSS dictionary00-09.txt | https://raw.githubusercontent.com/google/mozc/ff395cea46fe1438948e2fa876be19600e61ac00/src/data/dictionary_oss/dictionary0N.txt | listed in `tools/fetch_inputs.py` |
| Wikidata snapshot (2026-10-12) | https://github.com/c-ardinal/gboard-extended-dictionary/releases/download/wikidata-20261012/wikidata-p1814-ja-20261012.tsv | 90c9d2eaa447ad45004ba853d67621d1ea4e1e964495a0e4ac48a0c499581950 |

`tools/fetch_inputs.py` downloads all of them into `input/`, checks every SHA-256 and extracts the three CSVs.
The Wikidata TSV is downloaded directly from this repository's releases.
CI uses the same script.

## Run (Python 3.12+, stdlib only)

```
python -I tools/fetch_inputs.py input
python -I test_build_dictionary.py
python -I test_fetch_wikidata.py
python -I build_dictionary.py --base input/small_lex.csv input/core_lex.csv --extra input/notcore_lex.csv --wikidata input/wikidata-p1814-ja-20261012.tsv --mozc-dir input/mozc --out out
```

`--sizes` takes a comma-separated list. The default is `1000,10000,50000,all`, where `all` means every filtered row.

`--base` takes the CSVs for the base dictionary (small and core).
`--extra` is optional and takes the CSVs for the extra dictionary (notcore).
`--wikidata` is optional and builds the Wikidata dictionary from the pinned TSV.

The build writes these files to `out/`:

- `sudachi-ja-{1k,10k,50k,all}.txt` and `.zip` (base dictionary), the top-N rows by Sudachi cost (so 1k ⊂ 10k ⊂ 50k ⊂ all)
- `sudachi-ja-notcore-extra.txt` and `.zip` (extra dictionary; only with `--extra`)
  - It holds only rows whose (reading, surface) is not already in the base dictionary's "all" file.
  - Order: rows other than 人名 by cost first, then 人名 rows by cost, so names go to the back.
  - It is capped at 100,000 rows.
- `wikidata-ja-proper-part1.txt`, `-part2.txt`, … and `.zip` (Wikidata dictionary, split at 100,000 rows or 8 MiB; only with `--wikidata`, see below)
- `stats.json`, with row counts at every filter stage and a POS breakdown.
  For the extra dictionary it also records the rows available before the cap (by POS) and how many 人名 rows made it in.
- `sha256.txt`

### Importing

Import in this order: base dictionary → extra dictionary → Wikidata dictionary (part1 → part2 → part3).

The number of entries Mozc ingests is limited (see Size guidance). The extra dictionary is capped at 100,000 rows so that the base dictionary's "all" file plus the extra dictionary stay under 200,000.

### Split files

The Extended Dictionary patch imports at most 100,000 rows and 8 MiB per file.
A dictionary that exceeds either limit is written as `<name>-part1.txt`, `-part2.txt`, and so on, each with its own `.zip`.

- Every part is a complete Gboard word-list file with both header lines, at most 100,000 rows and at most 8 MiB (the zip stays under 8 MiB too).
- A dictionary that fits in one file keeps its plain name.
- Row order is preserved across parts; for example, `wikidata-ja-proper-part1` holds the items with the most sitelinks (number of Wikimedia sites with an article about the item).
- Import the parts in order, starting with part1.
- `sha256.txt` lists every part, and `stats.json` records the number of parts and the rows in each.
- Each part goes through the self-check after it is written.

### Size guidance

Mozc ingests at most 200,000 personal-dictionary entries in total on stock Gboard, and at most 1,000,000 with the Extended Dictionary patch. Your own entries count towards this limit.

Approximate row counts per file:

| File | Rows |
|---|---|
| Base dictionary ("all") | about 87,000 |
| Extra dictionary | 100,000 |
| Wikidata dictionary | about 214,000 (3 parts) |

On stock Gboard, pick files so that the total stays under 200,000.
The Wikidata dictionary is ordered by sitelinks (number of Wikimedia sites with an article about the item), descending, so a truncated copy keeps the items with the most sitelinks.

Every output is re-parsed after writing (header, 4 columns, lengths, reading charset, POS label, no CR or BOM).
The build fails if any row violates these rules.

## Filters

0. **Unconnected entries.** Drop rows with LeftId or RightId -1. These are Sudachi reference/variant entries that are not used for analysis, and they all have cost 0, so they would otherwise sort to the top.
1. **POS.** Keep only these Sudachi classes, mapped to Gboard labels:
   - 名詞-普通名詞: サ変可能 → 名詞サ変, 形状詞可能 → 名詞形動, everything else → 名詞.
   - 名詞-固有名詞: 一般 → 固有名詞, 組織名 → 組織, 人名 (excluding 姓 and 名) → 人名.
   - Everything else is dropped: 地名, surname and given-name lists, verbs, adjectives, symbols, emoticons.
2. **Surface.** Drop a row if its surface:
   - contains control characters or has leading/trailing whitespace;
   - is ASCII only;
   - contains whitespace anywhere, or presentation forms: Letterlike Symbols / Number Forms (U+2100-218F),
     Enclosed CJK / CJK Compatibility (U+3200-33FF) or CJK Compatibility Ideographs (U+F900-FAFF);
   - contains Hangul (Jamo U+1100-11FF, Compatibility Jamo U+3130-318F, Syllables U+AC00-D7AF).
3. **Reading.** Convert katakana to hiragana (ヴ → ゔ, ー kept) and lowercase ASCII.
   Remove the separators ・ and = (まいける・じゃくそん → まいけるじゃくそん).
   Only hiragana, katakana, ー and `[0-9a-z]` are allowed. The reading and the surface must each be 256 characters or fewer.
4. **No conversion value.** Drop the row if the reading equals the surface, or equals the surface converted to hiragana and lowercased.
5. **Dedupe and novelty.** Dedupe on (reading, surface), keeping the lowest cost.
   Drop surfaces that already appear in the Mozc OSS dictionary.
   Sort by Sudachi cost, ascending.
   The base and extra dictionaries each go through these steps separately. Rows that are already in the base dictionary are then removed from the extra dictionary.

## Wikidata dictionary (optional)

`wikidata-ja-proper` (`-part1`, `-part2`, … as `.txt` and `.zip`) is a separate dictionary of proper nouns from Wikidata (CC0).
It is built only with `--wikidata` and is distributed apart from the base and extra dictionaries.

- **Input:** the pinned TSV `wikidata-p1814-ja-<YYYYMMDD>.tsv`.
  - Columns: qid, label_ja, kana, p31s, sitelinks.
  - It is published as an asset of a release of this repository (tag `wikidata-<YYYYMMDD>`).
    `tools/fetch_inputs.py` downloads it and checks its sha256 like every other input.
  - Its date, row count and sha256 are recorded in the `wikidata` section of `stats.json`.
  - Wikidata and WDQS results change over time; the pinned, dated TSV is the only reproducible input.
- **Making a new snapshot:**
  1. Run `python -I tools/fetch_wikidata.py input`. It queries WDQS and writes the TSV plus a `.sha256`. Re-running with the same date reuses the saved file.
  2. Upload the TSV as an asset of a release tagged `wikidata-<YYYYMMDD>`.
  3. Update the URL and sha256 in `tools/fetch_inputs.py` and the `--wikidata` path in the workflow.
- **Reading:** P1814 (name in kana). Spaces, ・ and = are removed, then the reading is normalised with the same rules as the base dictionary.
- **POS:** derived from P31 (instance of). The class table lives in `build_dictionary.py`.
  - Family-name and given-name items, disambiguation pages and biological taxa (such as 偶蹄目) → dropped
  - Q5 (human) → 人名
  - Schools, companies and organisations → 組織
  - Shrines, temples, stations, municipalities (including dissolved ones), mountains, rivers, islands, parks, roads and similar → 地名
  - Everything else → 固有名詞
- **Extra filters:**
  - Labels with a parenthetical disambiguator (containing "(" or "（") are dropped.
  - Surfaces longer than 32 characters are dropped.
  - **Reading/surface mismatch (drop_reading_mismatch).** P1814 is the reading of the name, not necessarily of the whole label. A row is dropped if either holds:
    - the kana in the surface (katakana compared as hiragana, ー included) do not appear in the reading in the same order as a subsequence
      (ヶ/ヵ, as in 富士見ヶ丘, are counters read が or か and are skipped; ゐ→い, ゑ→え, ぢ→じ, づ→ず and ゔ→ぶ are treated as equal on both sides),
      for example りそなホールディングス ← だいわぎんほーるでぃんぐす;
    - the reading is shorter than the number of kanji (including 々) in the surface, for example 姶良市立建昌小学校 ← けんしょう.
  - Rows already in the base dictionary's "all" file, in the extra dictionary, or in the Mozc OSS dictionary are dropped.
- **Order:** sitelinks (number of Wikimedia sites with an article about the item) descending, then QID ascending.
  - There is no cap; every surviving row is written.
  - `stats.json` records the POS breakdown for the whole file and, for reference, for the first 50,000 rows.

The fetch uses the three queries below. Labels are fetched by POSTing the items from the sitelinks result in batches of 10,000 in a VALUES block.

```
SELECT DISTINCT ?item ?class WHERE {
  ?item wdt:P1814 ?kana ; wdt:P31 ?class .
}

SELECT DISTINCT ?item ?sitelinks WHERE {
  ?item wdt:P1814 ?kana ; wikibase:sitelinks ?sitelinks .
}

SELECT ?item ?label ?kana WHERE {
  VALUES ?item { wd:Q... }
  ?item wdt:P1814 ?kana ; rdfs:label ?label .
  FILTER(LANG(?label) = "ja")
}
```

## Licence

- **Code:** the scripts in this repository are under the Apache License 2.0 (see `LICENSE`).
- **Base and extra dictionaries:** only the `sudachi-ja-*` files are derived from [SudachiDict](https://github.com/WorksApplications/SudachiDict),
  Apache License 2.0, Copyright (c) 2017-2023 Works Applications Co., Ltd.
  - SudachiDict includes UniDic and a part of NEologd. Their notices are reproduced in `NOTICE`.
  - The SudachiDict LEGAL file reproduced in `NOTICE` (v20260723) states that all files in that distribution are covered under the Apache License 2.0, and that core_lex.csv and notcore_lex.csv contain a part of NEologd.
  - In accordance with section 4 of the Apache License 2.0, `LICENSE` and `NOTICE` are included with the distributed files.
- **Wikidata dictionary:** uses data from Wikidata (https://www.wikidata.org/), released under CC0 1.0. The source is recorded in `NOTICE`.
- **Mozc OSS dictionary** (BSD-3-Clause and the licences listed in google/mozc):
  it is used only as an exclusion filter at build time and is not redistributed.
