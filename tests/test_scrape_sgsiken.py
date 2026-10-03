from __future__ import annotations

import os
import json
import tempfile
from pathlib import Path
from unittest.mock import patch
import unittest

import requests

from scrape_sgsiken import (
    collect_question_page_urls,
    parse_pm_question_page,
    parse_q_question_page,
    split_classification_hierarchy,
    save_validated_source,
    load_existing_identities,
    download_and_save_images,
)


RUN_LIVE_TESTS = os.environ.get("RUN_LIVE_TESTS") == "1"


class ScrapeSgsikenTests(unittest.TestCase):
    def test_partial_scrape_cannot_overwrite_existing_source(self):
        from scrape_sgsiken import main
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'question_202601_1.json'
            path.write_text('existing source')
            with patch.dict(os.environ, {'SCRAPER_OUTPUT_LIST_GROUP_ID': '202601'}), \
                 patch('scrape_sgsiken.load_local_secure_env'), \
                 patch('scrape_sgsiken.apply_runtime_overrides_from_env'), \
                 patch('scrape_sgsiken.MAX_QUESTIONS', 1), \
                 patch('scrape_sgsiken.prepare_output_dirs', return_value=(directory, directory)), \
                 patch('scrape_sgsiken.fetch_html_text') as fetch:
                with self.assertRaisesRegex(ValueError, '部分上書き'):
                    main()
            self.assertEqual(path.read_text(), 'existing source')
            fetch.assert_not_called()

    def test_pm_wrapped_answer_keeps_own_explanation_math_lists_and_images(self):
        html = '''<h2>情報セキュリティマネジメント平成29年秋期 午後問2</h2>
        <h3 class="qno">問2</h3><div class="mondai">共通本文。</div><h3 id="s1">設問1</h3>
        <div class="mondai">(1) aとbを答えよ。</div><div class="inputAnswerBox">
        <select name="sel_a"><option>-</option><option>ア A</option><option>イ B</option></select>
        <select name="sel_b"><option>-</option><option>ア C</option><option>イ D</option></select></div>
        <h3>解答 :</h3><div><button>正解</button><div class="answerChars"><span id="ans_a">ア</span><span id="ans_b">イ</span></div></div>
        <h3>解説 :</h3><div class="kaisetsu">10<sup>4</sup>通り。<ol type="i"><li>理由A。</li><li>理由B。</li></ol><img src="exp.png"></div>
        <div class="mondai">(2) 次を答えよ。</div><div class="inputAnswerBox"><select name="sel_2">
        <option>-</option><option>ア E</option><option>イ F</option></select></div>
        <div><div class="answerChars"><span id="ans_2">イ</span></div></div><div class="kaisetsu">次の設問の理由。</div>'''
        with patch('scrape_sgsiken.download_and_save_images', return_value=['pm201702_q2_s1_1_exp_img01.png']) as download:
            records = parse_pm_question_page(html, 'https://www.sg-siken.com/kakomon/29_aki/pm02.html',
                http_session=None, download_images=True, output_list_group_id='201702')
        self.assertEqual(len(records), 3)
        self.assertEqual(download.call_count, 1)
        for record in records[:2]:
            self.assertEqual(record['explanation_common_prefix'], ['10⁴通り。i. 理由A。ii. 理由B。'])
            self.assertEqual(record['explanationImageSourceUrls'], ['https://www.sg-siken.com/kakomon/29_aki/exp.png'])
            self.assertTrue(record['explanationImageStorageUrls'][0].endswith('pm201702_q2_s1_1_exp_img01.png?alt=media'))
        self.assertEqual(records[2]['explanation_common_prefix'], ['次の設問の理由。'])
        self.assertEqual(records[2]['explanationImageStorageUrls'], [])

    def test_pm_missing_answer_cannot_borrow_next_questions_answer(self):
        html = '''<h2>平成29年秋期 午後問1</h2><h3 class="qno">問1</h3><div class="mondai">共通。</div>
        <h3 id="s1">設問1</h3><div class="mondai">(1) 一つ選べ。</div><div class="inputAnswerBox">
        <select name="sel_1"><option>ア A</option><option>イ B</option></select></div>
        <div class="mondai">(2) 一つ選べ。</div><div class="inputAnswerBox">
        <select name="sel_2"><option>ア C</option><option>イ D</option></select></div>
        <div class="answerChars"><span id="ans_2">ア</span></div><div class="kaisetsu">次の問の説明。</div>'''
        with self.assertRaisesRegex(ValueError, '現在の解答欄の正答'):
            parse_pm_question_page(html, 'https://www.sg-siken.com/kakomon/29_aki/pm01.html',
                http_session=None, download_images=False, output_list_group_id='201702')

    def test_source_refresh_adds_new_ids_without_moving_existing_records(self):
        def record(number):
            return {'source_question_id': str(number), 'public_question_id': 'public-' + str(number),
                    'original_question_id': 'original-' + str(number), 'questionBodyText': '本文',
                    'choiceTextList': ['A', 'B'], 'answer_result_inferred_correct_choice_numbers': [1]}
        original = [record(n) for n in range(26)]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            save_validated_source(root, '202601', original, expected_count=26)
            first = (root / 'question_202601_1.json').read_bytes()
            result = save_validated_source(root, '202601', [record(99)] + list(reversed(original)), expected_count=27)
            self.assertEqual(result['newSourceQuestionIds'], ['99'])
            self.assertEqual((root / 'question_202601_1.json').read_bytes(), first)
            second = json.loads((root / 'question_202601_2.json').read_text())['question_bodies']
            self.assertEqual([r['source_question_id'] for r in second], ['25', '99'])
            snapshot = {p.name: p.read_bytes() for p in root.glob('*.json')}
            with self.assertRaisesRegex(ValueError, '消失'):
                save_validated_source(root, '202601', original, expected_count=26)
            self.assertEqual({p.name: p.read_bytes() for p in root.glob('*.json')}, snapshot)

    def test_intent_uses_selection_request_and_not_background_error_words(self):
        from scrape_sgsiken import determine_question_intent
        positive = [
            '誤り検出方式であるCRCに関する記述として，適切なものはどれか。',
            'この回線のビット誤り率は幾らか。',
            '誤りビットを訂正したハミング符号はどれか。',
            '偽陽性率の説明として，最も適切なものはどれか。ここで，正しいものと間違っているものが含まれる。',
            'ACID特性の四つの性質に含まれないものはどれか。',
            'この規定に該当しないものはどれか。',
            'クロスサイトリクエストフォージェリ攻撃の対策として，効果がないものはどれか。',
        ]
        for text in positive:
            with self.subTest(text=text):
                self.assertEqual(determine_question_intent(text), 'select_correct')
        for text in ['次の記述のうち，誤っているものはどれか。',
                     '監査証拠の記述のうち，適切でないものはどれか。',
                     'プログラムの著作権管理上，不適切な行為はどれか。']:
            with self.subTest(text=text):
                self.assertEqual(determine_question_intent(text), 'select_incorrect')

    def test_unknown_script_characters_keep_their_exact_symbol_and_case(self):
        from bs4 import BeautifulSoup
        from scrape_sgsiken import extract_q_text
        node = BeautifulSoup('<div>事務<sup>※</sup>、X<sub>N</sub>、T<sub>b</sub></div>', 'html.parser').div
        self.assertEqual(extract_q_text(node), '事務^(※)、X_(N)、T_(b)')

    def test_nested_explanation_list_keeps_its_leading_conclusion(self):
        from bs4 import BeautifulSoup
        from scrape_sgsiken import parse_q_explanation_fields
        html = '<div id="kaisetsu">定義。<ul><br>したがってアです。<ul><li class="lia">A。</li><li class="lii">B。</li></ul></ul>補足。</div>'
        prefix, _, summary, snippets, _ = parse_q_explanation_fields(BeautifulSoup(html, "html.parser"), choice_count=2)
        self.assertIn("したがってアです。", prefix[0])
        self.assertEqual(summary, ["補足。"])
        self.assertEqual(snippets, [["A。"], ["B。"]])

    def test_ordered_list_preserves_roman_and_alphabetic_reference_labels(self):
        from bs4 import BeautifulSoup
        from scrape_sgsiken import extract_q_text
        for style, expected in (("i", "i. Aii. B"), ("I", "I. AII. B"), ("a", "a. Ab. B")):
            with self.subTest(style=style):
                node = BeautifulSoup(f'<div><ol type="{style}"><li>A</li><li>B</li></ol></div>', "html.parser").div
                self.assertEqual(extract_q_text(node), expected)

    def test_pm_plain_headings_and_multiple_selection_form_one_question(self):
        html = """<h2>情報セキュリティマネジメント平成31年春期 午後問2</h2>
        <h3 class="qno">問2</h3><div class="mondai">共通本文。</div>
        <h3 id="s1">設問1</h3><div class="mondai">該当するものを二つ選べ。</div>
        <div class="inputAnswerBox">
        <select name="sel_11"><option>-</option><option>ア A</option><option>イ B</option><option>ウ C</option></select>
        <select name="sel_12"><option>-</option><option>ア A</option><option>イ B</option><option>ウ C</option></select>
        </div><div class="answerChars"><span id="ans_11">ア</span><span id="ans_12">ウ</span></div>
        <h3 id="s2">設問2</h3><div class="mondai">適切なものを選べ。</div>
        <div class="inputAnswerBox"><select name="sel_2"><option>-</option><option>ア D</option><option>イ E</option></select></div>
        <div class="answerChars"><span id="ans_2">イ</span></div>"""
        records = parse_pm_question_page(
            html, "https://www.sg-siken.com/kakomon/31_haru/pm02.html",
            http_session=None, download_images=False, output_list_group_id="201901",
        )
        self.assertEqual(len(records), 2)
        self.assertEqual(records[0]["answer_result_inferred_correct_choice_numbers"], [1, 3])
        self.assertEqual(records[1]["answer_result_inferred_correct_choice_numbers"], [2])
        self.assertEqual(records[0]["questionLabel"], "午後問2 設問1")
        self.assertEqual(records[1]["questionLabel"], "午後問2 設問2")
        self.assertIn("共通本文。\n\n設問1", records[0]["questionBodyText"])
        from scripts.check.check_sgsiken_acquisition import audit_page
        for record in records:
            record["examYear"] = 2019
        self.assertEqual(audit_page(html, records, records[0]["question_url"]), [])

    def test_explanation_keeps_plain_text_prefix_normal_lists_and_summary(self):
        from bs4 import BeautifulSoup
        from scrape_sgsiken import parse_q_explanation_fields
        html = """<div id="kaisetsu"><strong>用語</strong>とは説明です。<br>
        <ul><li>共通の条件。</li></ul>続く説明。X<sub>1</sub>
        <ul><li class="lia">Aの理由。</li><li class="lii">Bの理由。</li></ul>
        最後の結論。</div>"""
        prefix, _, summary, snippets, _ = parse_q_explanation_fields(
            BeautifulSoup(html, "html.parser"), choice_count=2,
        )
        self.assertIn("用語とは説明です。", prefix[0])
        self.assertIn("共通の条件。", prefix[0])
        self.assertIn("続く説明。X₁", prefix[0])
        self.assertEqual(summary, ["最後の結論。"])
        self.assertEqual(snippets, [["Aの理由。"], ["Bの理由。"]])

    def test_pm_keeps_common_statement_section_images_and_exact_existing_ids(self):
        html = """<div class="main kako">
        <h2>情報セキュリティマネジメント令和元年秋期 午後問1</h2>
        <h3 class="qno">問1 ECサイト</h3>
        <div class="mondai">J社の対策を読め。<img src="common.png">X<sub>1</sub></div>
        <div class="mondai"><h3 class="inline">設問1</h3>攻撃1への対応を答えよ。</div>
        <div class="mondai">(1) 本文中のaに入れる字句はどれか。</div>
        <div class="inputAnswerBox"><select name="answer_a">
        <option>-</option><option>ア 対策A</option><option>イ 対策B</option>
        </select><select name="answer_b">
        <option>-</option><option>ア 対策C</option><option>イ 対策D</option>
        </select></div>
        <div class="answerChars"><span id="correct_a">イ</span><span id="correct_b">ア</span></div>
        <div class="kaisetsu">対策の根拠。</div></div>"""
        url = "https://www.sg-siken.com/kakomon/01_aki/pm01.html"
        ids = {
            f"201902:pm1:setumon1:1:{key}:{url}": {
                "public_question_id": f"public-{key}",
                "original_question_id": f"original-{key}",
            } for key in ("a", "b")
        }
        with patch("scrape_sgsiken.download_and_save_images", return_value=["common.png"]) as download:
            records = parse_pm_question_page(
                html, url, http_session=None, download_images=True,
                output_list_group_id="201902", existing_identities=ids,
            )
        self.assertEqual(download.call_count, 1)
        self.assertEqual(len(records), 2)
        self.assertEqual(records[0]["choiceTextList"], ["対策A", "対策B"])
        self.assertEqual(records[1]["choiceTextList"], ["対策C", "対策D"])
        self.assertEqual(records[0]["answer_result_inferred_correct_choice_numbers"], [2])
        self.assertEqual(records[1]["answer_result_inferred_correct_choice_numbers"], [1])
        for record in records:
            self.assertIn("J社の対策を読め。X₁", record["questionBodyText"])
            self.assertIn("攻撃1への対応を答えよ。", record["questionBodyText"])
            self.assertIn("(1) 本文中のa", record["questionBodyText"])
            self.assertEqual(len(record["questionImageStorageUrls"]), 1)
            identity = ids[record["source_question_id"]]
            self.assertEqual(record["public_question_id"], identity["public_question_id"])
            self.assertEqual(record["original_question_id"], identity["original_question_id"])
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "00_source").mkdir()
            (root / "00_source" / "question.json").write_text(json.dumps({"question_bodies": records}))
            loaded = load_existing_identities(root)
            self.assertEqual(set(loaded), set(ids))

    def test_inline_math_keeps_complements_powers_indices_and_stable_ids(self):
        html = """<h2>ネットワークスペシャリスト令和元年秋期 午前Ⅰ 問1</h2>
        <h3 class="qno">問1</h3>
        <div id="mondai"><span class="ol">A</span>∩<span class="ol">B</span>に等しい集合はどれか。<br>2<sup>3</sup>とX<sub>1</sub>を用いる。</div>
        <ul class="selectList">
        <li><button class="selectBtn">ア</button><span id="select_a"><span class="ol">A</span>－B</span></li>
        <li><button class="selectBtn">イ</button><span id="select_i">(A∪B)－(A∩B)</span></li>
        </ul><div class="answerBox"><span id="answerChar">ア</span></div>
        <div id="kaisetsu">否定は<span class="ol">A</span>で表す。</div>"""
        url = "https://www.nw-siken.com/kakomon/01_aki/am1_1.html"
        identity = {"source_question_id": f"201902:am:問1:{url}",
                    "public_question_id": "stable-public", "original_question_id": "stable-original"}
        record = parse_q_question_page(html, url, http_session=None, download_images=False,
                                      output_list_group_id="201902", existing_identity=identity)
        self.assertEqual(record["questionBodyText"], "A̅∩B̅に等しい集合はどれか。2³とX₁を用いる。")
        self.assertEqual(record["choiceTextList"], ["A̅－B", "(A∪B)－(A∩B)"])
        self.assertEqual(record["answer_result_inferred_correct_choice_numbers"], [1])
        self.assertIn("A̅", record["explanation_choice_snippets"][0][0])
        for field, value in identity.items():
            self.assertEqual(record[field], value)

    def test_inline_math_span_does_not_truncate_unwrapped_choice(self):
        from bs4 import BeautifulSoup
        from scrape_sgsiken import extract_choice_text_from_li
        from scripts.scrape.common import extract_text_with_subsup

        li = BeautifulSoup('<li><button class="selectBtn">ア</button><span class="ol">A</span>－B</li>', 'html.parser').li
        original = str(li)
        self.assertEqual(extract_choice_text_from_li(li, "ア"), "A̅－B")
        self.assertEqual(str(li), original)
        node = BeautifulSoup('<span class="ol">A</span>', 'html.parser').span
        self.assertEqual(extract_text_with_subsup(node), "A")
        self.assertEqual(extract_text_with_subsup(node, overline_classes=("ol",)), "A̅")

    def test_css_fraction_and_radical_keep_grouping_and_nested_math(self):
        from bs4 import BeautifulSoup
        from scrape_sgsiken import extract_q_text
        node = BeautifulSoup('<div>平均時間×<span class="frac"><span>ρ</span>1－ρ</span>。'
                             '<span class="root"><span class="frac"><span>A<sup>2</sup></span>'
                             '<span class="ol">B</span>＋1</span></span></div>', 'html.parser').div
        before = str(node)
        self.assertEqual(extract_q_text(node), '平均時間×(ρ)/(1－ρ)。√((A²)/(B̅＋1))')
        self.assertEqual(str(node), before)
        separator = BeautifulSoup('<span class="frac">10,000×0.01＋0.1×n<span></span>0.01＋n</span>', 'html.parser').span
        self.assertEqual(extract_q_text(separator), '(10,000×0.01＋0.1×n)/(0.01＋n)')
        double = BeautifulSoup('<span class="dol"><span class="ol">A</span></span>', 'html.parser').span
        self.assertEqual(extract_q_text(double), 'A̅̅')
        for markup in ('<span class="frac">A</span>',
                       '<span class="frac"><span>A</span></span>'):
            with self.subTest(markup=markup), self.assertRaises(ValueError):
                extract_q_text(BeautifulSoup(markup, 'html.parser').span)

    def test_question_number_excludes_heading_tools_and_keeps_existing_ids(self):
        html = """<h2>ネットワークスペシャリスト令和7年春期 午前Ⅰ 問1</h2>
        <h3 class="qno">問1<div class="tool-box">note_alt calculate</div></h3>
        <div id="mondai">適切な説明はどれか。</div>
        <ul class="selectList"><li><button class="selectBtn">ア</button>A</li>
        <li><button class="selectBtn">イ</button>B</li></ul>
        <div class="answerBox"><span id="answerChar">ア</span></div>"""
        url = "https://www.nw-siken.com/kakomon/07_haru/am1_1.html"
        identity = {"source_question_id": f"202501:am:問1:{url}", "public_question_id": "old-public", "original_question_id": "old-original"}
        record = parse_q_question_page(html, url, http_session=None, download_images=False,
                                      output_list_group_id="202501", existing_identity=identity)
        self.assertIsNotNone(record)
        self.assertEqual(record["questionLabel"], "問1")
        self.assertEqual(record["source_question_id"], identity["source_question_id"])
        self.assertEqual(record["public_question_id"], "old-public")
        self.assertEqual(record["original_question_id"], "old-original")

    def test_missing_image_stops_source_acquisition(self):
        with patch("scrape_sgsiken._download_and_save_images", return_value=[]):
            with self.assertRaises(ValueError):
                download_and_save_images(None, ["https://example.com/figure.png"], "q1", base_dir=".")

    def test_http_200_html_response_is_not_accepted_as_an_image(self):
        with tempfile.TemporaryDirectory() as directory:
            (Path(directory) / 'error.png').write_text('<html>service unavailable</html>')
            with patch('scrape_sgsiken._download_and_save_images', return_value=['error.png']), self.assertRaises(OSError):
                download_and_save_images(None, ['https://example.com/figure.png'], 'q1', base_dir=directory)

    def test_explanation_images_are_downloaded_and_bound_to_same_question(self):
        html = '''<h2>情報処理安全確保支援士令和7年春期 午前Ⅱ 問1</h2><h3 class="qno">問1</h3>
        <div id="mondai">説明を選べ。</div><ul class="selectList"><li><button class="selectBtn">ア</button>A</li>
        <li><button class="selectBtn">イ</button>B</li></ul><div class="answerBox"><span id="answerChar">ア</span></div>
        <div id="kaisetsu">図を参照。<img src="img/explanation.png"></div>'''
        url = 'https://www.sc-siken.com/kakomon/07_haru/am2_1.html'
        identity = {'source_question_id': f'202501:am:問1:{url}', 'public_question_id': 'stable', 'original_question_id': 'stable'}
        with patch('scrape_sgsiken.download_and_save_images', return_value=['qstable_exp_img01.png']) as download:
            record = parse_q_question_page(html, url, http_session=None, download_images=True,
                                          output_list_group_id='202501', existing_identity=identity)
        self.assertEqual(record['explanationImageSourceUrls'], ['https://www.sc-siken.com/kakomon/07_haru/img/explanation.png'])
        self.assertTrue(record['explanationImageStorageUrls'][0].endswith('qstable_exp_img01.png?alt=media'))
        self.assertEqual(download.call_args.args[2], 'qstable_exp')

    def test_choice_explanations_bind_by_label_when_dom_order_changes(self):
        from bs4 import BeautifulSoup
        from scrape_sgsiken import parse_q_explanation_fields
        node = BeautifulSoup('<div id="kaisetsu">定義。<ul><li class="lii">イの理由。</li><li class="lia">アの理由。</li></ul>結論。</div>', 'html.parser')
        prefix, _, summary, snippets, _ = parse_q_explanation_fields(node, choice_count=2)
        self.assertEqual(snippets, [['アの理由。'], ['イの理由。']])
        self.assertEqual(prefix, ['定義。'])
        self.assertEqual(summary, ['結論。'])

    def test_refresh_preserves_ids_and_reports_changes_after_full_validation(self):
        record = {"source_question_id": "stable", "questionBodyText": "本文", "choiceTextList": ["A", "B"],
                  "answer_result_inferred_correct_choice_numbers": [1], "public_question_id": "old-public",
                  "original_question_id": "old-original"}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            save_validated_source(root, "202501", [record], expected_count=1)
            path = root / "question_202501_1.json"
            baseline = path.read_bytes()
            with self.assertRaises(ValueError):
                save_validated_source(root, "202501", [record], expected_count=55)
            with self.assertRaises(ValueError):
                save_validated_source(root, "202501", [{**record, "public_question_id": "new"}], expected_count=1)
            with self.assertRaises(ValueError):
                save_validated_source(root, "202501", [{**record, "answer_result_inferred_correct_choice_numbers": [3]}], expected_count=1)
            self.assertEqual(path.read_bytes(), baseline)
            result = save_validated_source(root, "202501", [{**record, "questionBodyText": "取得元の新しい本文"}], expected_count=1)
            self.assertEqual(result["changedSourceQuestionIds"], ["stable"])
            result = save_validated_source(root, "202501", [{**record, "questionBodyText": "取得元の新しい本文"}], expected_count=1)
            self.assertEqual(result["unchangedSourceQuestionIds"], ["stable"])

    def test_identity_recovery_reads_only_exact_identity_and_rejects_conflict(self):
        record = {"source_question_id": "stable", "question_url": "https://example.com/q1", "public_question_id": "old-public",
                  "original_question_id": "old-original", "questionBodyText": "古い本文"}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for stage in ("00_source", "12_merged_questionType"):
                (root / stage).mkdir()
                (root / stage / "question.json").write_text(json.dumps({"question_bodies": [record]}))
            identities = load_existing_identities(root)
            self.assertNotIn("questionBodyText", identities[record["question_url"]])
            (root / "12_merged_questionType" / "question.json").write_text(json.dumps({"question_bodies": [{**record, "original_question_id": "conflict"}]}))
            with self.assertRaises(ValueError):
                load_existing_identities(root)

    def test_legacy_patch_identity_alias_survives_a_changed_id_secret(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "10_questionType_fixed").mkdir()
            (root / "10_questionType_fixed" / "question.json").write_text(json.dumps([
                {"question_url": "https://example.com/q1", "original_question_id": "old-id"}
            ]))
            identity = load_existing_identities(root)["https://example.com/q1"]
            self.assertEqual(identity["public_question_id"], "old-id")
            self.assertEqual(identity["original_question_id"], "old-id")

    def setUp(self) -> None:
        os.environ.setdefault("QUESTION_ID_SECRET_KEY", "test-secret")

    def test_collect_question_page_urls_normalizes_nw_mobile_links(self) -> None:
        list_html = """
        <main>
          <ul class="menu">
            <li><a href="am1_1.html">問1</a></li>
            <li><a href="am2_1.html">問1</a></li>
            <li><a href="am2_25.html">問25</a></li>
          </ul>
        </main>
        """

        q_urls, pm_urls = collect_question_page_urls(
            list_html,
            "https://www.nw-siken.com/s/kakomon/07_haru/",
        )

        self.assertEqual(
            q_urls,
            [
                "https://www.nw-siken.com/kakomon/07_haru/am1_1.html",
                "https://www.nw-siken.com/kakomon/07_haru/am2_1.html",
                "https://www.nw-siken.com/kakomon/07_haru/am2_25.html",
            ],
        )
        self.assertEqual(pm_urls, [])

    def test_split_classification_hierarchy(self) -> None:
        hierarchy, major, middle, small = split_classification_hierarchy(
            "テクノロジ系 » セキュリティ » 情報セキュリティ対策"
        )

        self.assertEqual(
            hierarchy,
            ["テクノロジ系", "セキュリティ", "情報セキュリティ対策"],
        )
        self.assertEqual(major, "テクノロジ系")
        self.assertEqual(middle, "セキュリティ")
        self.assertEqual(small, "情報セキュリティ対策")

    @unittest.skipUnless(RUN_LIVE_TESTS, "live site dependent (set RUN_LIVE_TESTS=1)")
    def test_parse_live_am_q14(self) -> None:
        url = "https://www.sg-siken.com/kakomon/01_aki/q14.html"
        html = requests.get(url, timeout=20, headers={"User-Agent": "Mozilla/5.0"}).text

        qb = parse_q_question_page(
            html,
            url,
            http_session=None,
            download_images=False,
            output_list_group_id="201902",
        )
        self.assertIsNotNone(qb)
        assert qb is not None

        self.assertEqual(qb["examYear"], 2019)
        self.assertIn("午前", qb["examLabel"])
        self.assertIn("問14", qb["questionLabel"])
        self.assertEqual(qb["questionType"], "true_false")
        self.assertEqual(len(qb["choiceTextList"]), 4)
        self.assertEqual(qb["answer_result_inferred_correct_choice_numbers"], [1])
        self.assertEqual(qb["answer_result_text"], "正解は 1 です。")
        self.assertEqual(qb["questionIntent"], "select_correct")
        self.assertEqual(qb["correctChoiceText"], ["正しい", "間違い", "間違い", "間違い"])
        self.assertEqual(qb["categoryMajor"], "テクノロジ系")
        self.assertEqual(qb["categoryMiddle"], "セキュリティ")
        self.assertEqual(qb["categorySmall"], "情報セキュリティ対策")
        self.assertEqual(len(qb["explanation_choice_snippets"]), 4)
        self.assertTrue(qb["public_question_id"])
        self.assertIn("source_question_id", qb)
        self.assertNotIn("questionSetId", qb)

    @unittest.skipUnless(RUN_LIVE_TESTS, "live site dependent (set RUN_LIVE_TESTS=1)")
    def test_parse_live_pm01_splits_multiple_questions(self) -> None:
        url = "https://www.sg-siken.com/kakomon/01_aki/pm01.html"
        html = requests.get(url, timeout=20, headers={"User-Agent": "Mozilla/5.0"}).text

        qbs = parse_pm_question_page(
            html,
            url,
            http_session=None,
            download_images=False,
            output_list_group_id="201902",
        )
        self.assertTrue(qbs)
        self.assertEqual({qb["examYear"] for qb in qbs}, {2019})
        self.assertTrue(any("午後問1" in qb["questionLabel"] for qb in qbs))
        # 少なくとも1件は正解番号が取れている
        self.assertTrue(any(qb["answer_result_inferred_correct_choice_numbers"] for qb in qbs))
        # すべて true_false で出す
        self.assertTrue(all(qb["questionType"] == "true_false" for qb in qbs))


if __name__ == "__main__":
    unittest.main()
