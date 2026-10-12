# gboard-extended-dictionary

日本語: [README.md](README.md)

Japanese word lists for the Gboard "Extended Dictionary" patch, built from SudachiDict.
Each list is a Gboard personal-dictionary file that can be imported as is.

## Output format

Gboard personal-dictionary word list, version 2, UTF-8 without BOM, LF line endings:

```
# Gboard Dictionary version:2
# Gboard Dictionary format:shortcut<TAB>word<TAB>language_tag<TAB>pos_tag
けんきゅう<TAB>研究<TAB>ja-JP<TAB>名詞サ変
```

- Columns: reading (hiragana), word, `ja-JP`, part-of-speech label. Every row has exactly 4 columns.
- The POS label is one of Gboard's Japanese labels. This build emits 名詞, 名詞サ変, 名詞形動, 固有名詞, 組織 and 人名.
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
CI uses the same script.

While the repository is private, release assets cannot be downloaded anonymously.
Fetch the Wikidata TSV first with `gh release download wikidata-20261012 -p wikidata-p1814-ja-20261012.tsv -D input`.
`tools/fetch_inputs.py` then only verifies the sha256 of a file that is already there.

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

The build writes these files to `out/`:

- `sudachi-ja-{1k,10k,50k,all}.txt` and `.zip` (base), the top-N rows by Sudachi cost (so 1k ⊂ 10k ⊂ 50k ⊂ all)
- `sudachi-ja-notcore-extra.txt` and `.zip` (extra; only with `--extra`)
  - It holds only rows whose (reading, surface) is not already in the base "all" list.
  - Order: rows other than 人名 by cost first, then 人名 rows by cost, so names go to the back.
  - It is capped at 100,000 rows.
- `wikidata-ja-proper-part1.txt`, `-part2.txt`, … and `.zip` (Wikidata proper nouns, split every 100,000 rows; only with `--wikidata`, see below)
- `stats.json`, with row counts at every filter stage and a POS breakdown.
  For the extra list it also records the rows available before the cap (by POS) and how many 人名 rows made it in.
- `sha256.txt`

### Importing

Import the base dictionary first, then the extra one.

Gboard hands at most 200,000 user-dictionary rows to Mozc in total, and your own entries count towards that limit.
The extra cap keeps base "all" (about 87,000 rows) plus extra (at most 100,000 rows) under 200,000.

### Split files

The Extended Dictionary patch imports at most 100,000 rows and 8 MiB per file.
A dictionary that exceeds either limit is written as `<name>-part1.txt`, `-part2.txt`, and so on, each with its own `.zip`.

- Every part is a complete Gboard word list with both header lines, at most 100,000 rows and at most 8 MiB (the zip stays under 8 MiB too).
- A dictionary that fits in one file keeps its plain name.
- Row order is preserved across parts; for example, `wikidata-ja-proper-part1` holds the best-known names.
- Import the parts in order, starting with part1.
- `sha256.txt` lists every part, and `stats.json` records the number of parts and the rows in each.
- Each part goes through the self-check after it is written.

### Size guidance

On stock Gboard, Mozc ingests at most 200,000 personal-dictionary entries. The Extended Dictionary patch raises the limit to 1,000,000.

Approximate row counts per file:

| File | Rows |
|---|---|
| Base "all" | about 87,000 |
| Extra | 100,000 |
| Wikidata proper nouns | about 214,000 (3 parts) |

On stock Gboard, pick files so that the total stays under 200,000.
The Wikidata file is ordered by notability (sitelinks descending), so a truncated copy still keeps the best-known names.

Every output is re-parsed after writing (header, 4 columns, lengths, reading charset, POS label, no CR or BOM).
The build fails if any row violates these rules.

## Filters

0. **Unconnected entries.** Drop rows with LeftId or RightId -1. These are Sudachi reference/variant entries that are not used for analysis, and they all have cost 0.
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
   Only hiragana, katakana, ー and `[0-9a-z]` are allowed, and the reading must be 256 characters or fewer.
4. **No conversion value.** Drop the row if the reading equals the surface, or equals the surface converted to hiragana and lowercased.
5. **Dedupe and novelty.** Dedupe on (reading, surface), keeping the lowest cost.
   Drop surfaces that already appear in the Mozc OSS dictionary.
   Sort by Sudachi cost, ascending.
   Base and extra each go through these steps separately. Rows that are already in base are then removed from extra.

## Wikidata proper-noun dictionary (optional, prototype)

`wikidata-ja-proper` (`-part1`, `-part2`, … as `.txt` and `.zip`) forms a separate dictionary of proper nouns from Wikidata (CC0).
They are built only with `--wikidata` and are distributed as a file of their own, apart from the base and extra lists.

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
- **Reading:** P1814 (name in kana). Spaces, ・ and = are removed, then the reading is normalised with the same rules as the base lists.
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
  - Rows already in base "all", in the extra list, or in the Mozc OSS dictionary are dropped.
- **Order:** sitelinks (number of Wikipedia and other Wikimedia pages about the item) descending, then QID ascending.
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
- **Word lists:** the generated word lists are derived from [SudachiDict](https://github.com/WorksApplications/SudachiDict),
  Apache License 2.0, Copyright (c) 2017-2023 Works Applications Co., Ltd.
  - SudachiDict includes UniDic and a part of NEologd. Their notices are reproduced in `NOTICE`.
  - The SudachiDict LEGAL file in `NOTICE` (v20260723) covers all three files (small_lex, core_lex and notcore_lex), so notcore is under the same Apache-2.0 terms.
  - Distribute `LICENSE` and `NOTICE` together with any word list.
- **Wikidata proper-noun dictionary:** Wikidata data is released under CC0 1.0, which does not legally require attribution; we nevertheless credit the Wikidata community in `NOTICE` with gratitude for their work.
- **Mozc OSS dictionary** (BSD-3-Clause and the licences listed in google/mozc):
  it is used only as an exclusion filter at build time and is not redistributed.
