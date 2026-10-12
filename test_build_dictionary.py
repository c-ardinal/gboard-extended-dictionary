import collections
import io
import json
import os
import sys
import tempfile
import unittest
from unittest import mock
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
        for bad in ("カン字", "アダム スミス", "ア、イ", "・","ア\tイ", ""):
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
                    chr(0xFEFF) + good, good.replace("version:2", "version:1"), good + "漢\tb\tja-JP\t名詞\n",
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
            bd.main(["--base", csv_path, "--mozc-dir", mozc, "--out", out, "--sizes", "2,100,all"])

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
            bd.write_outputs([("bad", [("a b", "x", "名詞", 0)])], tmp, {"outputs": {}})

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
                bd.write_outputs([("sudachi-ja-all", entries)], out, {"outputs": {}})
                with open(os.path.join(out, "sudachi-ja-all.zip"), "rb") as fh:
                    blobs.append(fh.read())
        self.assertEqual(blobs[0], blobs[1])
        with zipfile.ZipFile(io.BytesIO(blobs[0])) as zf:
            info = zf.infolist()[0]
            self.assertEqual((info.filename, info.date_time, info.compress_type),
                             ("dictionary.txt", (1980, 1, 1, 0, 0, 0), zipfile.ZIP_STORED))

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

    def test_reading_separators_removed(self):
        self.assertEqual(bd.normalize_reading("マイケル・ジャクソン"), "まいけるじゃくそん")
        self.assertEqual(bd.normalize_reading("アン=マリー"), "あんまりー")
        noun = ["名詞", "固有名詞", "人名", "一般"]
        rows = [row("マイケル・ジャクソン氏", 1, noun, "マイケル・ジャクソンシ").rstrip("\n").split(","),
                row("研究", 1, ["名詞", "普通名詞", "一般"], "ケンキュウ").rstrip("\n").split(",")]
        stats = collections.Counter()
        kept = [(r, s) for r, s, _, _ in bd.filter_entries(rows, stats, collections.Counter())]
        self.assertEqual(kept, [("まいけるじゃくそんし", "マイケル・ジャクソン氏"), ("けんきゅう", "研究")])
        self.assertEqual(stats["reading_separators_removed"], 1)

    def test_extra_puts_names_last_and_caps(self):
        entries = [("a", "A", "人名", 1), ("b", "B", "名詞", 5), ("c", "C", "固有名詞", 3), ("d", "D", "人名", 2)]
        stats = collections.Counter()
        available, ordered = bd.order_extra(entries, {("b", "B")}, stats, cap=3)
        self.assertEqual(len(available), 3)
        self.assertEqual(stats["drop_in_base"], 1)
        self.assertEqual([e[1] for e in ordered], ["C", "A", "D"])
        _, capped = bd.order_extra(entries, set(), collections.Counter(), cap=2)
        self.assertEqual([e[1] for e in capped], ["C", "B"])

    def test_base_extra_split_end_to_end(self):
        common = row("研究", 100, ["名詞", "普通名詞", "サ変可能"], "ケンキュウ")
        name = ["名詞", "固有名詞", "人名", "一般"]
        with tempfile.TemporaryDirectory() as tmp:
            base, extra, mozc, out = (os.path.join(tmp, n) for n in ("small_lex.csv", "notcore_lex.csv", "mozc", "out"))
            with open(base, "w", encoding="utf-8", newline="\n") as fh:
                fh.write(CSV_HEADER + common)
            with open(extra, "w", encoding="utf-8", newline="\n") as fh:
                fh.write(CSV_HEADER + common.replace(",100,", ",50,")
                         + row("山田花子", -500, name, "ヤマダハナコ")
                         + row("某工業", 900, ["名詞", "固有名詞", "一般"], "ボウコウギョウ"))
            os.mkdir(mozc)
            with open(os.path.join(mozc, "dictionary00.txt"), "w", encoding="utf-8", newline="\n") as fh:
                fh.write("きぞんご\t1\t1\t100\t既存語\n")
            bd.main(["--base", base, "--extra", extra, "--mozc-dir", mozc, "--out", out, "--sizes", "all"])
            with open(os.path.join(out, "sudachi-ja-all.txt"), encoding="utf-8") as fh:
                self.assertEqual([l.split("\t")[1] for l in fh.read().split("\n")[2:] if l], ["研究"])
            with open(os.path.join(out, bd.EXTRA_NAME + ".txt"), encoding="utf-8") as fh:
                text = fh.read()
            self.assertEqual(bd.validate_text(text), [])
            self.assertEqual([l.split("\t")[1] for l in text.split("\n")[2:] if l], ["某工業", "山田花子"])
            self.assertTrue(os.path.exists(os.path.join(out, bd.EXTRA_NAME + ".zip")))
            with open(os.path.join(out, "stats.json"), encoding="utf-8") as fh:
                report = json.load(fh)
            ex = report["extra"]
            self.assertEqual((ex["available"], ex["written"], ex["written_name_rows"], ex["cap"]), (2, 2, 1, bd.MAX_EXTRA_ROWS))
            self.assertEqual(ex["available_pos"], {"固有名詞": 1, "人名": 1})
            self.assertEqual(ex["stages"]["drop_in_base"], 1)
            self.assertEqual(report["outputs"][bd.EXTRA_NAME]["rows"], 2)

    def test_wikidata_pos_table(self):
        cases = {
            ("Q5",): "人名", ("Q845945",): "地名", ("Q55488",): "地名", ("Q18663566",): "地名",
            ("Q5358913",): "組織", ("Q43229",): "組織", ("Q7889",): "固有名詞", (): "固有名詞",
            ("Q101352",): None, ("Q4167410",): None, ("Q5", "Q101352"): None,
            ("Q16521",): None, ("Q23038290",): None, ("Q7432",): None,
            ("Q5358913", "Q845945"): "組織", ("Q5", "Q43229"): "人名",
        }
        for p31s, label in cases.items():
            self.assertEqual(bd.wikidata_pos(p31s), label, p31s)
        groups = (bd.WIKIDATA_DROP, bd.WIKIDATA_PERSON, bd.WIKIDATA_ORGANISATION, bd.WIKIDATA_PLACE)
        self.assertEqual(sum(map(len, groups)), len(set().union(*groups)))

    def test_wikidata_filters(self):
        rows = [r + ["1"] for r in [
            ["Q1", "斎藤隆", "さいとう たかし", "Q5"],
            ["Q2", "マイケル・ジャクソン", "マイケル・ジャクソン", "Q5"],
            ["Q3", "東山(京都)", "ひがしやま", "Q8502"],
            ["Q4", "東山（京都）", "ひがしやま", "Q8502"],
            ["Q5", "長" * 33, "なが", ""],
            ["Q6", "吉田", "よしだ", "Q101352"],
            ["Q7", "飛鳥坐神社", "あすかにいますじんじゃ", "Q845945"],
            ["Q7", "飛鳥坐神社", "あすかにますじんじゃ", "Q845945"],
            ["Q8", "トヨタ", "とよた", ""],
            ["Q9", "姶良市立建昌小学校", "けんしょう", "Q5358913"],
            ["Q10", "りそなホールディングス", "だいわぎんほーるでぃんぐす", "Q4830453"],
            ["Q11", "ㄱ", "きよく", ""],
        ]]
        stats = collections.Counter()
        kept = [(r, s, p) for r, s, p, _ in bd.filter_wikidata(rows, stats)]
        self.assertEqual(kept, [("さいとうたかし", "斎藤隆", "人名"), ("まいけるじゃくそん", "マイケル・ジャクソン", "人名"),
                                ("あすかにいますじんじゃ", "飛鳥坐神社", "地名"), ("あすかにますじんじゃ", "飛鳥坐神社", "地名")])
        self.assertEqual(stats["drop_kana_or_case_variant"], 1)
        self.assertEqual(stats["reading_separators_removed"], 1)
        self.assertEqual(stats["drop_parenthetical"], 2)
        self.assertEqual(stats["drop_surface_over_32"], 1)
        self.assertEqual(stats["drop_name_or_wikimedia_item"], 1)
        self.assertEqual(stats["multi_reading_items"], 1)
        self.assertEqual(stats["drop_reading_mismatch"], 2)
        self.assertEqual(stats["drop_compat_surface"], 1)

    def test_reading_matches_surface(self):
        for reading, surface in (("みなみさつまし", "南さつま市"), ("まいけるじゃくそん", "マイケル・ジャクソン"),
                                 ("ささきのぞみ", "佐々木希"), ("こーひーかん", "コーヒー館"),
                                 ("ふじみがおかえき", "富士見ヶ丘駅"), ("いなむらがさきえき", "稲村ヵ崎駅"),
                                 ("みとさつえ", "三戸サツヱ"), ("ささきたず", "佐々木たづ"), ("ぶぃら", "ヴィラ")):
            self.assertTrue(bd.reading_matches_surface(reading, surface), surface)
        for reading, surface in (("だいわぎんほーるでぃんぐす", "りそなホールディングス"),  # (a) kana missing
                                 ("けんしょう", "姶良市立建昌小学校"),  # (b) shorter than the kanji count
                                 ("らくさし", "さくら市"),  # (a) kana out of order
                                 ("ふじみおかえき", "富士見ヶ丘駅のぞみ")):  # (a) kana missing even with ヶ skipped
            self.assertFalse(bd.reading_matches_surface(reading, surface), surface)

    def test_wikidata_orders_by_sitelinks_then_qid(self):
        rows = [["Q30", "東山", "ひがしやま", "Q8502", "3"], ["Q20", "西山", "にしやま", "Q8502", "3"],
                ["Q40", "北山", "きたやま", "Q8502", "50"]]
        stats = collections.Counter()
        entries = bd.select_entries(bd.dedupe(bd.filter_wikidata(rows, stats), stats), set(), stats)
        self.assertEqual([e[1] for e in entries], ["北山", "西山", "東山"])

    def test_wikidata_dedupe_keeps_higher_sitelinks(self):
        rows = [["Q1", "東山", "ひがしやま", "Q8502", "2"], ["Q2", "東山", "ひがしやま", "Q5", "40"],
                ["Q3", "東山", "ひがしやま", "Q43229", "40"]]
        stats = collections.Counter()
        best = bd.dedupe(bd.filter_wikidata(rows, stats), stats)
        self.assertEqual(best, {("ひがしやま", "東山"): ("人名", (-40, 2))})
        self.assertEqual(stats["drop_duplicate"], 2)

    def test_hangul_surface_dropped(self):
        noun = ["名詞", "固有名詞", "一般"]
        rows = [row(s, 1, noun, r).rstrip("\n").split(",") for s, r in (
            ("ㄱ", "キヨク"), ("가", "カ"), ("한국店", "ハングクテン"), ("韓国店", "カンコクテン"))]
        stats = collections.Counter()
        kept = [s for _, s, _, _ in bd.filter_entries(rows, stats, collections.Counter())]
        self.assertEqual(kept, ["韓国店"])
        self.assertEqual(stats["drop_compat_surface"], 3)

    def test_place_label_passes_self_check(self):
        self.assertEqual(bd.validate_text(bd.render([("ひがしやま", "東山", "地名")])), [])

    def test_wikidata_deduped_against_base_and_extra(self):
        with tempfile.TemporaryDirectory() as tmp:
            base, extra, wd, mozc, out = (os.path.join(tmp, n) for n in (
                "small_lex.csv", "notcore_lex.csv", "wikidata-p1814-ja-20261012.tsv", "mozc", "out"))
            with open(base, "w", encoding="utf-8", newline="\n") as fh:
                fh.write(CSV_HEADER + row("研究所", 100, ["名詞", "普通名詞", "一般"], "ケンキュウジョ"))
            with open(extra, "w", encoding="utf-8", newline="\n") as fh:
                fh.write(CSV_HEADER + row("某工業", 900, ["名詞", "固有名詞", "一般"], "ボウコウギョウ"))
            with open(wd, "w", encoding="utf-8", newline="\n") as fh:
                fh.write("qid\tlabel_ja\tkana\tp31s\tsitelinks\n"
                         "Q10\t研究所\tけんきゅうじょ\tQ31855\t9\n"
                         "Q11\t某工業\tボウコウギョウ\tQ4830453\t9\n"
                         "Q12\t既存語\tきぞんご\t\t9\n"
                         "Q13\t東山\tひがしやま\tQ8502\t2\n"
                         "Q14\t斎藤隆\tさいとう たかし\tQ5\t5\n")
            os.mkdir(mozc)
            with open(os.path.join(mozc, "dictionary00.txt"), "w", encoding="utf-8", newline="\n") as fh:
                fh.write("きぞんご\t1\t1\t100\t既存語\n")
            bd.main(["--base", base, "--extra", extra, "--wikidata", wd, "--mozc-dir", mozc, "--out", out,
                     "--sizes", "all"])
            with open(os.path.join(out, bd.WIKIDATA_NAME + ".txt"), encoding="utf-8") as fh:
                text = fh.read()
            self.assertEqual(bd.validate_text(text), [])
            self.assertEqual([tuple(l.split("\t")) for l in text.split("\n")[2:] if l],
                             [("さいとうたかし", "斎藤隆", "ja-JP", "人名"), ("ひがしやま", "東山", "ja-JP", "地名")])
            with open(os.path.join(out, "stats.json"), encoding="utf-8") as fh:
                wd_report = json.load(fh)["wikidata"]
            self.assertEqual(wd_report["stages"]["drop_in_sudachi_lists"], 2)
            self.assertEqual(wd_report["stages"]["drop_in_mozc_oss"], 1)
            self.assertEqual((wd_report["date"], wd_report["input_rows"]), ("20261012", 5))
            self.assertEqual(len(wd_report["input_sha256"]), 64)

    def test_split_at_row_boundary(self):
        entries = [("あ" + str(i), "亜" + str(i), "名詞", i) for i in range(100_001)]
        self.assertEqual([len(p) for p in bd.split_parts(entries[:100_000])], [100_000])
        parts = bd.split_parts(entries)
        self.assertEqual([len(p) for p in parts], [100_000, 1])
        self.assertEqual([e for p in parts for e in p], entries)  # order preserved
        self.assertEqual(bd.split_parts([]), [[]])

    def test_split_by_byte_budget(self):
        entries = [("あ", "亜" * 10, "名詞", i) for i in range(10)]
        row_bytes = len(bd.render_row(entries[0]).encode("utf-8"))
        budget = len(bd.HEADER.encode("utf-8")) + 3 * row_bytes
        self.assertEqual([len(p) for p in bd.split_parts(entries, max_bytes=budget)], [3, 3, 3, 1])
        self.assertEqual(len(bd.split_parts(entries)), 1)  # well under the real 8 MiB budget

    def test_large_output_written_as_parts(self):
        entries = [("あ" + str(i), "亜" + str(i), "名詞", i) for i in range(5)]
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(bd, "MAX_PART_ROWS", 2):
            stats = {"outputs": {}}
            bd.write_outputs([("big", entries), ("small", entries[:2])], tmp, stats)
            names = sorted(os.listdir(tmp))
            self.assertEqual(names, ["big-part1.txt", "big-part1.zip", "big-part2.txt", "big-part2.zip",
                                     "big-part3.txt", "big-part3.zip", "sha256.txt", "small.txt", "small.zip"])
            surfaces = []
            for i in (1, 2, 3):
                with open(os.path.join(tmp, f"big-part{i}.txt"), encoding="utf-8") as fh:
                    text = fh.read()
                self.assertTrue(text.startswith(bd.HEADER))
                self.assertEqual(bd.validate_text(text), [])
                surfaces += [line.split("\t")[1] for line in text.split("\n")[2:] if line]
            self.assertEqual(surfaces, [e[1] for e in entries])
            self.assertEqual(stats["outputs"]["big"]["parts"],
                             [{"name": "big-part1", "rows": 2}, {"name": "big-part2", "rows": 2},
                              {"name": "big-part3", "rows": 1}])
            with open(os.path.join(tmp, "sha256.txt"), encoding="utf-8") as fh:
                self.assertEqual(len(fh.read().splitlines()), 8)

    def test_self_check_enforces_part_limits(self):
        row_line = "あ\t亜\tja-JP\t名詞\n"
        with mock.patch.object(bd, "MAX_PART_ROWS", 2):
            self.assertEqual(bd.validate_text(bd.HEADER + row_line * 2), [])
            self.assertIn("more than 2 rows", bd.validate_text(bd.HEADER + row_line * 3))
        with mock.patch.object(bd, "MAX_PART_BYTES", len((bd.HEADER + row_line).encode("utf-8"))):
            self.assertEqual(bd.validate_text(bd.HEADER + row_line), [])
            self.assertTrue(any("larger than" in e for e in bd.validate_text(bd.HEADER + row_line * 2)))

    def test_parse_sizes_all(self):
        self.assertEqual(bd.parse_sizes("1000,10000,50000,all"), [1000, 10000, 50000, None])

    def test_dedupe_keeps_lowest_cost(self):
        stats = collections.Counter()
        best = bd.dedupe([("a", "A", "名詞", 9), ("a", "A", "固有名詞", 3), ("a", "A", "名詞", 5)], stats)
        self.assertEqual(best, {("a", "A"): ("固有名詞", 3)})
        self.assertEqual(stats["drop_duplicate"], 2)


if __name__ == "__main__":
    unittest.main()
