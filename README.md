# gboard-extended-dictionary

English: [README.en.md](README.en.md)

Gboard の「Extended Dictionary」パッチで使う日本語単語リストを、SudachiDict から生成するリポジトリです。
生成されるファイルは Gboard の単語リスト（ユーザー辞書）形式なので、そのままインポートできます。

## 出力形式

Gboard の単語リスト形式 version 2 です。文字コードは BOM なしの UTF-8、改行は LF です。

```
# Gboard Dictionary version:2
# Gboard Dictionary format:shortcut<TAB>word<TAB>language_tag<TAB>pos_tag
けんきゅう<TAB>研究<TAB>ja-JP<TAB>名詞サ変
```

- 列は「読み（ひらがな）」「単語」「`ja-JP`」「品詞」の順です。どの行もちょうど 4 列です。
- 品詞は Gboard の日本語品詞ラベルを使います。このビルドが出力するのは 名詞・名詞サ変・名詞形動・固有名詞・組織・人名 の 6 種類です。
- `.zip` には `dictionary.txt` が 1 つだけ入っており、中身は同名の `.txt` と同じです。
- zip はどの環境で作ってもバイト単位で同じになります。日時は 1980-01-01、属性は固定で、圧縮せずに格納しています（サイズは `.txt` とほぼ同じです）。

## 入力データ（バージョン固定）

| 入力 | URL | SHA-256 |
|---|---|---|
| SudachiDict small_lex.zip（リリース v20260723） | https://sudachi.s3-ap-northeast-1.amazonaws.com/sudachidict-raw/v1/20260723/small_lex.zip | e49936daef64043657752eb9f4ada912cf0316e26dd158a6200c770e2f93e706 |
| SudachiDict core_lex.zip（リリース v20260723） | https://sudachi.s3-ap-northeast-1.amazonaws.com/sudachidict-raw/v1/20260723/core_lex.zip | d8ed376d8ff368226314a43151ab02591378dd344bff017d8a93ae9839212edf |
| Mozc OSS dictionary00-09.txt | https://raw.githubusercontent.com/google/mozc/ff395cea46fe1438948e2fa876be19600e61ac00/src/data/dictionary_oss/dictionary0N.txt | `tools/fetch_inputs.py` に記載 |

`tools/fetch_inputs.py` を実行すると、これらを `input/` にダウンロードします。
あわせて全ファイルの SHA-256 を照合し、2 つの CSV を取り出します。CI も同じスクリプトを使います。

## 実行方法（Python 3.12 以上、標準ライブラリのみ）

```
python -I tools/fetch_inputs.py input
python -I test_build_dictionary.py
python -I build_dictionary.py --sudachi input/small_lex.csv input/core_lex.csv --mozc-dir input/mozc --out out
```

`--sizes` で出力する件数をカンマ区切りで指定できます。
既定値は `1000,10000,50000,all` で、`all` はフィルタを通った全行を表します。

`out/` には次のファイルができます。

- `sudachi-ja-{1k,10k,50k,all}.txt` と `.zip`：Sudachi のコストが低い順に上位 N 行を収めたもの（1k ⊂ 10k ⊂ 50k ⊂ all）
- `stats.json`：フィルタの段階ごとの件数と、品詞別の内訳
- `sha256.txt`

書き出した後、すべての出力を読み直して検査します。
検査項目は、ヘッダ・列数 4・文字数・読みの文字種・品詞ラベル・CR や BOM がないことです。
1 行でも違反があればビルドは失敗します。

## フィルタ

0. **解析に使われない項目**：LeftId または RightId が -1 の行は除外します。
   これらは Sudachi の参照用・表記ゆれ用の項目で、形態素解析には使われません。コストがすべて 0 のため、残すと先頭に並んでしまいます。
1. **品詞**：次の Sudachi の品詞だけを残し、Gboard のラベルに対応付けます。
   - 名詞-普通名詞：サ変可能 → 名詞サ変、形状詞可能 → 名詞形動、それ以外 → 名詞
   - 名詞-固有名詞：一般 → 固有名詞、組織名 → 組織、人名（姓・名を除く）→ 人名
   - これ以外はすべて除外します（地名、姓・名の一覧、動詞、形容詞、記号、顔文字など）。
2. **表層形（単語）**：次のどれかに当てはまる行は除外します。
   - 制御文字を含む、または前後に空白がある
   - ASCII 文字だけでできている
   - どこかに空白を含む、または表示用の互換文字を含む
     - 互換文字の範囲：文字様記号・数字の形（U+2100-218F）、囲み CJK 文字・CJK 互換用文字（U+3200-33FF）、CJK 互換漢字（U+F900-FAFF）
3. **読み**：カタカナをひらがなに変換し（ヴ → ゔ、ー はそのまま）、英字は小文字にします。
   使える文字はひらがな・カタカナ・ー・`[0-9a-z]` だけで、長さは 256 文字までです。
4. **変換しても意味がない語**：次のどちらかに当てはまる行は除外します。
   - 読みが表層形と同じ
   - 読みが、表層形をひらがな化・小文字化したものと同じ
5. **重複除去と既存語の除外**：
   - （読み, 表層形）が同じ行は、コストが最も低い 1 行だけを残します。
   - Mozc OSS 辞書に既にある表層形は除外します。
   - 残った行を Sudachi のコストが低い順に並べます。

## ライセンス

- **コード**：このリポジトリのスクリプトは Apache License 2.0 です（`LICENSE` を参照）。
- **単語リスト**：生成される単語リストは [SudachiDict](https://github.com/WorksApplications/SudachiDict) の派生物です。
  SudachiDict は Apache License 2.0、Copyright (c) 2017-2023 Works Applications Co., Ltd. です。
  - SudachiDict には UniDic と NEologd の一部が含まれています。それぞれの表示は `NOTICE` に収録しています。
  - 単語リストを配布するときは、`LICENSE` と `NOTICE` も一緒に配布してください。
- **Mozc OSS 辞書**（BSD-3-Clause ほか、google/mozc に記載のライセンス）：
  ビルド時の除外フィルタとして使うだけで、再配布はしていません。
