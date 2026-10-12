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
| SudachiDict notcore_lex.zip（リリース v20260723） | https://sudachi.s3-ap-northeast-1.amazonaws.com/sudachidict-raw/v1/20260723/notcore_lex.zip | 75aa2d8cb855c7a44a326dc9f43f78cb6dc881caccb996e7559e2513b69cb0e1 |
| Mozc OSS dictionary00-09.txt | https://raw.githubusercontent.com/google/mozc/ff395cea46fe1438948e2fa876be19600e61ac00/src/data/dictionary_oss/dictionary0N.txt | `tools/fetch_inputs.py` に記載 |
| Wikidata スナップショット（2026-10-12） | https://github.com/c-ardinal/gboard-extended-dictionary/releases/download/wikidata-20261012/wikidata-p1814-ja-20261012.tsv | 90c9d2eaa447ad45004ba853d67621d1ea4e1e964495a0e4ac48a0c499581950 |

`tools/fetch_inputs.py` を実行すると、これらを `input/` にダウンロードします。
あわせて全ファイルの SHA-256 を照合し、3 つの CSV を取り出します。CI も同じスクリプトを使います。

リポジトリが非公開の間は、リリースの添付ファイルを認証なしではダウンロードできません。
先に `gh release download wikidata-20261012 -p wikidata-p1814-ja-20261012.tsv -D input` で取得してください。
`tools/fetch_inputs.py` は、既にあるファイルについては sha256 の照合だけを行います。

## 実行方法（Python 3.12 以上、標準ライブラリのみ）

```
python -I tools/fetch_inputs.py input
python -I test_build_dictionary.py
python -I build_dictionary.py --base input/small_lex.csv input/core_lex.csv --extra input/notcore_lex.csv --wikidata input/wikidata-p1814-ja-20261012.tsv --mozc-dir input/mozc --out out
```

`--sizes` で出力する件数をカンマ区切りで指定できます。
既定値は `1000,10000,50000,all` で、`all` はフィルタを通った全行を表します。

`--base` に指定した CSV（small・core）から基本辞書を作ります。
`--extra` は省略可能で、指定した CSV（notcore）から追加辞書を作ります。

`out/` には次のファイルができます。

- `sudachi-ja-{1k,10k,50k,all}.txt` と `.zip`（基本辞書）：Sudachi のコストが低い順に上位 N 行を収めたもの（1k ⊂ 10k ⊂ 50k ⊂ all）
- `sudachi-ja-notcore-extra.txt` と `.zip`（追加辞書、`--extra` を指定したときのみ）
  - 基本辞書の all にない（読み, 表層形）の行だけを収めます。
  - 並び順は、人名以外をコスト順に並べた後に人名をコスト順に並べたものです。人名は後ろに回ります。
  - 上限は 100,000 行です。
- `wikidata-ja-proper.txt` と `.zip`（Wikidata 固有名詞辞書、`--wikidata` を指定したときのみ。後述）
- `stats.json`：フィルタの段階ごとの件数と品詞別の内訳。追加辞書については、上限で切る前の件数（品詞別）と、実際に収録した人名の件数も記録します。
- `sha256.txt`

### 取り込み方

まず基本辞書を、次に追加辞書をインポートしてください。

Gboard が Mozc に渡せるのはユーザー辞書の合計 200,000 行までです。この上限には、自分で登録した単語も含まれます。
基本辞書の all（約 87,000 行）と追加辞書（最大 100,000 行）を合わせても 200,000 行に収まるよう、追加辞書に上限を設けています。

### 件数の目安

通常の Gboard では、Mozc が取り込むユーザー辞書は最大 200,000 件です。Extended Dictionary パッチを当てると 1,000,000 件まで増えます。

各ファイルのおおよその件数は次のとおりです。

| ファイル | 件数 |
|---|---|
| 基本辞書の all | 約 87,000 |
| 追加辞書 | 100,000 |
| Wikidata 固有名詞辞書 | 約 214,000 |

通常の Gboard を使う場合は、合計が 200,000 件に収まるようにファイルを選んでください。
Wikidata 固有名詞辞書は知名度の高い順（sitelinks の多い順）に並んでいます。途中で切り詰めても、よく知られた名前から残ります。

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
   - ハングル（字母 U+1100-11FF、互換字母 U+3130-318F、音節 U+AC00-D7AF）を含む
3. **読み**：カタカナをひらがなに変換し（ヴ → ゔ、ー はそのまま）、英字は小文字にします。
   区切り記号の「・」と「=」は取り除きます（まいける・じゃくそん → まいけるじゃくそん）。
   使える文字はひらがな・カタカナ・ー・`[0-9a-z]` だけで、長さは 256 文字までです。
4. **変換しても意味がない語**：次のどちらかに当てはまる行は除外します。
   - 読みが表層形と同じ
   - 読みが、表層形をひらがな化・小文字化したものと同じ
5. **重複除去と既存語の除外**：
   - （読み, 表層形）が同じ行は、コストが最も低い 1 行だけを残します。
   - Mozc OSS 辞書に既にある表層形は除外します。
   - 残った行を Sudachi のコストが低い順に並べます。
   - 基本辞書と追加辞書は別々にこの処理をします。そのうえで、基本辞書にある行は追加辞書から除きます。

## Wikidata 固有名詞辞書（任意・試作）

`wikidata-ja-proper.txt` と `.zip` は、Wikidata（CC0）の固有名詞から作る独立した辞書です。
`--wikidata` を指定したときだけ生成され、基本辞書・追加辞書とは別ファイルとして配布します。

- **入力:** バージョン固定の TSV `wikidata-p1814-ja-<YYYYMMDD>.tsv` です。
  - 列は qid、label_ja、kana、p31s、sitelinks です。
  - このリポジトリのリリース（タグ `wikidata-<YYYYMMDD>`）に添付ファイルとして置き、`tools/fetch_inputs.py` が他の入力と同じく sha256 を照合して取得します。
  - 取得日・行数・sha256 は `stats.json` の `wikidata` に記録されます。
  - Wikidata の内容と WDQS の結果は日々変わります。再現できる入力は、日付付きで固定したこの TSV だけです。
- **新しいスナップショットの作り方:**
  1. `python -I tools/fetch_wikidata.py input` を実行します。WDQS から取得し、TSV と `.sha256` を書き出します。同じ日付で再実行すると保存済みのファイルを使います。
  2. TSV を、タグ `wikidata-<YYYYMMDD>` のリリースに添付ファイルとしてアップロードします。
  3. `tools/fetch_inputs.py` の URL と sha256、ワークフローの `--wikidata` のパスを更新します。
- **読み:** P1814（かな表記）を使います。空白と「・」「=」を取り除いてから、基本辞書と同じ規則で正規化します。
- **品詞:** P31（分類）から決めます。対応表は `build_dictionary.py` にあります。
  - 姓・名の項目、曖昧さ回避ページ、生物の分類群（偶蹄目など）→ 除外
  - Q5（人間）→ 人名
  - 学校・企業・団体 → 組織
  - 神社・寺院・駅・市区町村（廃止を含む）・山・川・島・公園・道路など → 地名
  - それ以外 → 固有名詞
- **追加のフィルタ:**
  - 括弧付きの曖昧さ回避（「(」「（」を含む表記）は除外します。
  - 33 文字以上の表記は除外します。
  - **読みと表記の不一致（drop_reading_mismatch）:** P1814 は「名前」のかな表記で、ラベル全体の読みとは限りません。次のどちらかに当てはまる行は除外します。
    - 表記に含まれるかな（カタカナはひらがなにして比べ、ー も含む）が、同じ順番で読みの中に現れない（部分列になっていない）
      - ヶ・ヵ（富士見ヶ丘 など）は「が」「か」とも読むので比較から外します。
      - ゐ→い、ゑ→え、ぢ→じ、づ→ず、ゔ→ぶ は、表記と読みの両方で同じ文字とみなします。
      - 例：りそなホールディングス ← だいわぎんほーるでぃんぐす
    - 読みの文字数が、表記の漢字の数（々 を含む）より少ない
      - 例：姶良市立建昌小学校 ← けんしょう
  - 基本辞書の all・追加辞書・Mozc OSS 辞書に既にある語は除外します。
- **並び順:** sitelinks（その項目がある Wikipedia などの記事数）の多い順、同数なら QID の小さい順です。
  - 上限は設けず、条件を満たした行をすべて出力します。
  - `stats.json` には、全体と先頭 5 万行の品詞別件数を参考として記録します。

取得に使う SPARQL は次の 3 種類です。ラベルの取得は、sitelinks の結果から得た項目を 10,000 件ずつ VALUES に並べて POST します。

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

## ライセンス

- **コード**：このリポジトリのスクリプトは Apache License 2.0 です（`LICENSE` を参照）。
- **単語リスト**：生成される単語リストは [SudachiDict](https://github.com/WorksApplications/SudachiDict) の派生物です。
  SudachiDict は Apache License 2.0、Copyright (c) 2017-2023 Works Applications Co., Ltd. です。
  - SudachiDict には UniDic と NEologd の一部が含まれています。それぞれの表示は `NOTICE` に収録しています。
  - `NOTICE` に収録した SudachiDict の LEGAL（v20260723）は small_lex・core_lex・notcore_lex の 3 ファイルすべてを対象としており、notcore にも同じ Apache-2.0 が適用されます。
  - 単語リストを配布するときは、`LICENSE` と `NOTICE` も一緒に配布してください。
- **Wikidata 固有名詞辞書**：Wikidata のデータは CC0 1.0 なので、帰属表示は不要です。`NOTICE` には謝辞として 1 行だけ記載しています。
- **Mozc OSS 辞書**（BSD-3-Clause ほか、google/mozc に記載のライセンス）：
  ビルド時の除外フィルタとして使うだけで、再配布はしていません。
