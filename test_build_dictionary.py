import collections
import io
import json
import os
import sys
import tempfile
import unittest
import zipfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import build_dictionary as bd  # noqa: E402

CSV_HEADER = ("IndexForm,LeftId,RightId,Cost,Headword,POS1,POS2,POS3,POS4,POS5,POS6,"
              "ReadingForm,NormalizedForm,DictionaryForm,SplitA,SplitB,WordStructure,SynonymGroups,ReferenceId\n")


def row(surface, cost, pos, reading, headword="", left_id="1", right_id="1"):
    pos = (pos + ["*"] * 6)[:6]
    return ",".join([surface, left_id, right_id, str(cost), headword, *pos, reading, "", "", "", "", "", "", ""]) + "\n"


FIXTURE = [
    row("構築分子", 500, ["名詞", "普通名詞", "一般"], "コウチクブンシ"),
    row("研究", 100, ["名詞", "普通名詞", "サ変可能"], "ケンキュウ"),
    row("静穏", 300, ["名詞", "普通名詞", "形状詞可能"], "セイオン"),
    row("ヴァイオリン工房", 400, ["名詞", "固有名詞", "一般"], "ヴァイオリンコウボウ"),
    row("某社", 200, ["名詞", "固有名詞", "組織名"], "ボウシャ"),
    row("山田太郎", 250, ["名詞", "固有名詞", "人名", "一般"], "ヤマダタロウ"),
    row("山田", 50, ["名詞", "固有名詞", "人名", "姓"], "ヤマダ"),          # 姓 excluded
    row("東京都", 50, ["名詞", "固有名詞", "地名", "一般"], "トウキョウト"),  # 地名 excluded
    row("走る", 50, ["動詞", "一般"], "ハシル"),                             # inflecting
    row("(^^)", 50, ["補助記号", "ＡＡ", "顔文字"], "カオモジ"),
    row("adam smith", 600, ["名詞", "固有名詞", "人名", "一般"], "アダム スミス", "Adam Smith"),  # space
    row("漢字読", 600, ["名詞", "普通名詞", "一般"], "カン字"),               # kanji in reading
    row("ねこ", 600, ["名詞", "普通名詞", "一般"], "ネコ"),                   # reading == surface
    row("研究", 900, ["名詞", "普通名詞", "一般"], "ケンキュウ"),             # duplicate, higher cost
    row("既存語", 10, ["名詞", "普通名詞", "一般"], "キゾンゴ"),             # in Mozc OSS
    row("あ\\u002cb", 700, ["名詞", "普通名詞", "一般"], "エービー"),        # escaped comma
    row("改\\u0009行", 700, ["名詞", "普通名詞", "一般"], "カイギョウ"),     # TAB in surface
    row("長" * 10, 700, ["名詞", "普通名詞", "一般"], "ア" * 257),           # reading too long
]


class UnitTest(unittest.TestCase):
    def test_katakana_to_hiragana_keeps_choon_and_maps_vu(self):
        self.assertEqual(bd.katakana_to_hiragana("コーヒー"), "こーひー")
        self.assertEqual(bd.katakana_to_hiragana("ヴァヵヶ"), "ゔぁゕゖ")
        self.assertEqual(bd.normalize_reading("ABC123"), "abc123")

    def test_rejects_kanji_space_and_symbol_readings(self):
        for bad in ("カン字", "アダム スミス", "ア・イ", "ア\tイ", ""):
            self.assertIsNone(bd.normalize_reading(bad), bad)

    def test_pos_mapping(self):
        cases = {
            ("名詞", "普通名詞", "一般", "*"): "名詞",
            ("名詞", "普通名詞", "サ変可能", "*"): "名詞サ変",
            ("名詞", "普通名詞", "形状詞可能", "*"): "名詞形動",
            ("名詞", "固有名詞", "一般", "*"): "固有名詞",
            ("名詞", "固有名詞", "組織名", "*"): "組織",
            ("名詞", "固有名詞", "人名", "一般"): "人名",
            ("名詞", "固有名詞", "人名", "姓"): None,
            ("名詞", "固有名詞", "人名", "名"): None,
            ("名詞", "固有名詞", "地名", "一般"): None,
            ("名詞", "数詞", "*", "*"): None,
            ("動詞", "一般", "*", "*"): None,
            ("補助記号", "ＡＡ", "顔文字", "*"): None,
        }
        for pos, label in cases.items():
            self.assertEqual(bd.map_pos(list(pos) + ["*", "*"]), label, pos)
            if label:
                self.assertIn(label, bd.GBOARD_POS_LABELS)
        self.assertEqual(len(bd.GBOARD_POS_ITEMS), 45)

    def test_self_check_rejects_bad_rows(self):
        good = bd.render([("けんきゅう", "研究", "名詞")])
        self.assertEqual(bd.validate_text(good), [])
        for bad in (good + "a\tb\tja-JP\n", good + "a\tb\tja-JP\t名詞\textra\n", good.replace("\n", "\r\n"),
                    "﻿" + good, good.replace("version:2", "version:1"), good + "漢\tb\tja-JP\t名詞\n",
                    good + "a\tb\tja-JP\t謎\n", good + "a\t" + "b" * 257 + "\tja-JP\t名詞\n"):
            self.assertNotEqual(bd.validate_text(bad), [], repr(bad[-40:]))


class PipelineTest(unittest.TestCase):
    def test_end_to_end(self):
        with tempfile.TemporaryDirectory() as tmp:
            csv_path = os.path.join(tmp, "small_lex.csv")
            with open(csv_path, "w", encoding="utf-8", newline="\n") as fh:
                fh.write(CSV_HEADER + "".join(FIXTURE))
            mozc = os.path.join(tmp, "mozc")
            os.mkdir(mozc)
            with open(os.path.join(mozc, "dictionary00.txt"), "w", encoding="utf-8", newline="\n") as fh:
                fh.write("きぞんご\t1\t1\t100\t既存語\n")
            out = os.path.join(tmp, "out")
            bd.main(["--sudachi", csv_path, "--mozc-dir", mozc, "--out", out, "--sizes", "2,100,all"])

            with open(os.path.join(out, "sudachi-ja-100.txt"), "rb") as fh:
                data = fh.read()
            text = data.decode("utf-8")
            self.assertTrue(text.startswith(bd.HEADER))
            rows = [tuple(l.split("\t")) for l in text.split("\n")[2:] if l]
            self.assertEqual(rows, [
                ("けんきゅう", "研究", "ja-JP", "名詞サ変"),
                ("ぼうしゃ", "某社", "ja-JP", "組織"),
                ("やまだたろう", "山田太郎", "ja-JP", "人名"),
                ("せいおん", "静穏", "ja-JP", "名詞形動"),
                ("ゔぁいおりんこうぼう", "ヴァイオリン工房", "ja-JP", "固有名詞"),
                ("こうちくぶんし", "構築分子", "ja-JP", "名詞"),
                ("えーびー", "あ,b", "ja-JP", "名詞"),
            ])
            with open(os.path.join(out, "sudachi-ja-2.txt"), encoding="utf-8") as fh:
                self.assertEqual(len(fh.read().split("\n")) - 3, 2)
            with zipfile.ZipFile(os.path.join(out, "sudachi-ja-100.zip")) as zf:
                self.assertEqual(zf.namelist(), ["dictionary.txt"])
                self.assertEqual(zf.read("dictionary.txt"), data)
            with open(os.path.join(out, "stats.json"), encoding="utf-8") as fh:
                st = json.load(fh)["stages"]
            self.assertEqual(st["rows_in"], len(FIXTURE))
            self.assertEqual(st["drop_pos_excluded"], 4)
            self.assertEqual(st["drop_reading_chars"], 1)
            self.assertEqual(st["drop_ascii_surface"], 1)
            self.assertEqual(st["drop_reading_eq_surface"], 1)
            self.assertEqual(st["drop_surface_chars"], 1)
            self.assertEqual(st["drop_over_256"], 1)
            self.assertEqual(st["drop_duplicate"], 1)
            self.assertEqual(st["drop_in_mozc_oss"], 1)
            self.assertEqual(st["after_mozc_filter"], 7)
            with open(os.path.join(out, "sudachi-ja-all.txt"), "rb") as fh:
                self.assertEqual(fh.read(), data)
            with open(os.path.join(out, "stats.json"), encoding="utf-8") as fh:
                outputs = json.load(fh)["outputs"]
            self.assertEqual(outputs["sudachi-ja-all"]["rows"], 7)
            self.assertEqual(outputs["sudachi-ja-all"]["cost_cutoff"], 700)
            with open(os.path.join(out, "sha256.txt"), encoding="utf-8") as fh:
                self.assertEqual(len(fh.read().splitlines()), 6)

    def test_write_outputs_fails_on_bad_row(self):
        with tempfile.TemporaryDirectory() as tmp, self.assertRaises(SystemExit):
            bd.write_outputs([("a b", "x", "名詞", 0)], [1], tmp, {"outputs": {}})

    def test_drops_kana_and_case_variants_keeps_kanji(self):
        noun = ["名詞", "普通名詞", "一般"]
        rows = [row(s, 1, noun, r).rstrip("\n").split(",") for s, r in (
            ("クロルカルキ", "クロルカルキ"), ("ヴィラ", "ヴィラ"), ("AHC", "AHC"), ("MCity", "MCITY"), ("九芒星", "キュウボウセイ"))]
        stats = collections.Counter()
        kept = [(r, s) for r, s, _, _ in bd.filter_entries(rows, stats, collections.Counter())]
        self.assertEqual(kept, [("きゅうぼうせい", "九芒星")])
        self.assertEqual(stats["drop_kana_or_case_variant"], 2)
        self.assertEqual(stats["drop_ascii_surface"], 2)

    def test_drops_ascii_only_surface_keeps_mixed(self):
        noun = ["名詞", "固有名詞", "一般"]
        rows = [row(s, 1, noun, r).rstrip("\n").split(",") for s, r in (
            ("IE", "アイイー"), ("AZ-COM", "アズコム"), ("at", "アット"), ("富士古河E&C", "フジフルカワイーアンドシー"))]
        stats = collections.Counter()
        kept = [s for _, s, _, _ in bd.filter_entries(rows, stats, collections.Counter())]
        self.assertEqual(kept, ["富士古河E&C"])
        self.assertEqual(stats["drop_ascii_surface"], 3)

    def test_drops_cjk_compatibility_surface(self):
        noun = ["名詞", "普通名詞", "一般"]
        rows = [row(s, 1, noun, r).rstrip("\n").split(",") for s, r in (
            ("㌀", "アパート"), ("㈱東京", "カブシキガイシャトウキョウ"), ("\uf900印", "キイン"), ("株式会社", "カブシキガイシャ"))]
        stats = collections.Counter()
        kept = [s for _, s, _, _ in bd.filter_entries(rows, stats, collections.Counter())]
        self.assertEqual(kept, ["株式会社"])
        self.assertEqual(stats["drop_compat_surface"], 3)

    def test_zip_is_reproducible(self):
        entries = [("けんきゅう", "研究", "名詞", 1)]
        blobs = []
        with tempfile.TemporaryDirectory() as tmp:
            for run in ("a", "b"):
                out = os.path.join(tmp, run)
                bd.write_outputs(entries, [None], out, {"outputs": {}})
                with open(os.path.join(out, "sudachi-ja-all.zip"), "rb") as fh:
                    blobs.append(fh.read())
        self.assertEqual(blobs[0], blobs[1])
        with zipfile.ZipFile(io.BytesIO(blobs[0])) as zf:
            info = zf.infolist()[0]
            self.assertEqual((info.filename, info.date_time, info.compress_type),
                             ("dictionary.txt", (1980, 1, 1, 0, 0, 0), zipfile.ZIP_DEFLATED))

    def test_drops_unconnected_rows(self):
        noun = ["名詞", "普通名詞", "一般"]
        rows = [row("一位", 0, noun, "イチイ", left_id="-1").rstrip("\n").split(","),
                row("三位", 0, noun, "サンイ", right_id="-1").rstrip("\n").split(","),
                row("二位", 0, noun, "ニイ").rstrip("\n").split(",")]
        stats = collections.Counter()
        kept = [s for _, s, _, _ in bd.filter_entries(rows, stats, collections.Counter())]
        self.assertEqual(kept, ["二位"])
        self.assertEqual(stats["drop_unconnected"], 2)

    def test_drops_whitespace_letterlike_and_number_forms(self):
        noun = ["名詞", "普通名詞", "一般"]
        rows = [row(s, 1, noun, r).rstrip("\n").split(",") for s, r in (
            ("東京\u2003駅", "トウキョウエキ"), ("東京 駅", "トウキョウエキ"), ("五\u2113", "ゴリットル"),
            ("第\u2161章", "ダイニショウ"), ("東京駅", "トウキョウエキ"))]
        stats = collections.Counter()
        kept = [s for _, s, _, _ in bd.filter_entries(rows, stats, collections.Counter())]
        self.assertEqual(kept, ["東京駅"])
        self.assertEqual(stats["drop_compat_surface"], 4)

    def test_csv_header_checked_or_absent(self):
        data_line = row("研究", 1, ["名詞", "普通名詞", "一般"], "ケンキュウ")
        with tempfile.TemporaryDirectory() as tmp:
            bad = os.path.join(tmp, "bad.csv")
            with open(bad, "w", encoding="utf-8", newline="\n") as fh:
                fh.write(CSV_HEADER.replace("ReadingForm", "Reading") + data_line)
            with self.assertRaises(SystemExit):
                list(bd.iter_sudachi_rows([bad]))
            bare = os.path.join(tmp, "bare.csv")
            with open(bare, "w", encoding="utf-8", newline="\n") as fh:
                fh.write(data_line)
            self.assertEqual([r[0] for r in bd.iter_sudachi_rows([bare])], ["研究"])

    def test_parse_sizes_all(self):
        self.assertEqual(bd.parse_sizes("1000,10000,50000,all"), [1000, 10000, 50000, None])

    def test_dedupe_keeps_lowest_cost(self):
        stats = collections.Counter()
        best = bd.dedupe([("a", "A", "名詞", 9), ("a", "A", "固有名詞", 3), ("a", "A", "名詞", 5)], stats)
        self.assertEqual(best, {("a", "A"): ("固有名詞", 3)})
        self.assertEqual(stats["drop_duplicate"], 2)


if __name__ == "__main__":
    unittest.main()
