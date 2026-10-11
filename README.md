# gboard-extended-dictionary

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
| Mozc OSS dictionary00-09.txt | https://raw.githubusercontent.com/google/mozc/ff395cea46fe1438948e2fa876be19600e61ac00/src/data/dictionary_oss/dictionary0N.txt | listed in `tools/fetch_inputs.py` |

`tools/fetch_inputs.py` downloads all of them into `input/`, checks every SHA-256 and extracts the two CSVs.
CI uses the same script.

## Run (Python 3.12+, stdlib only)

```
python -I tools/fetch_inputs.py input
python -I test_build_dictionary.py
python -I build_dictionary.py --sudachi input/small_lex.csv input/core_lex.csv --mozc-dir input/mozc --out out
```

`--sizes` takes a comma-separated list. The default is `1000,10000,50000,all`, where `all` means every filtered row.

The build writes these files to `out/`:

- `sudachi-ja-{1k,10k,50k,all}.txt` and `.zip`, the top-N rows by Sudachi cost (so 1k ⊂ 10k ⊂ 50k ⊂ all)
- `stats.json`, with row counts at every filter stage and a POS breakdown
- `sha256.txt`

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
     Enclosed CJK / CJK Compatibility (U+3200-33FF) or CJK Compatibility Ideographs (U+F900-FAFF).
3. **Reading.** Convert katakana to hiragana (ヴ → ゔ, ー kept) and lowercase ASCII.
   Only hiragana, katakana, ー and `[0-9a-z]` are allowed, and the reading must be 256 characters or fewer.
4. **No conversion value.** Drop the row if the reading equals the surface, or equals the surface converted to hiragana and lowercased.
5. **Dedupe and novelty.** Dedupe on (reading, surface), keeping the lowest cost.
   Drop surfaces that already appear in the Mozc OSS dictionary.
   Sort by Sudachi cost, ascending.

## Licence

- **Code:** the scripts in this repository are under the Apache License 2.0 (see `LICENSE`).
- **Word lists:** the generated word lists are derived from [SudachiDict](https://github.com/WorksApplications/SudachiDict),
  Apache License 2.0, Copyright (c) 2017-2023 Works Applications Co., Ltd.
  - SudachiDict includes UniDic and a part of NEologd. Their notices are reproduced in `NOTICE`.
  - Distribute `LICENSE` and `NOTICE` together with any word list.
- **Mozc OSS dictionary** (BSD-3-Clause and the licences listed in google/mozc):
  it is used only as an exclusion filter at build time and is not redistributed.
