from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import json
from bs4 import BeautifulSoup
from scripts.check.check_scsiken_acquisition import assert_text_coverage, assert_numbered_explanation, assert_explanation_content, assert_image_references, assert_live_targets, assert_document_inventory, compact, discover_targets, document_links, official_answers, visible_source, official_difference_receipt
from scripts.scrape.qualification_presets import build_list_first_page_url, load_scrape_preset
from scrape_sgsiken import extract_q_text
from scripts.scrape.common import to_subscript, to_superscript


class ScAcquisitionTests(unittest.TestCase):
    def test_failed_audit_overwrites_previous_success_and_keeps_other_groups(self):
        from scripts.check import check_scsiken_acquisition as checker
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); reports = root / 'output/sc/reports'; reports.mkdir(parents=True)
            path = reports / 'acquisition_verification.json'
            path.write_text('{"status":"passed"}')
            preset = SimpleNamespace(list_group_ids=['202501', '202502'], get_target=lambda g: None)
            good = {'group': '202502', 'questionCount': 55, 'imageReferenceCount': 0, 'pdfCount': 0,
                    'afternoonHtmlCount': 0, 'afternoonImageCount': 0, 'publicationHolds': [], 'explanationImageReferenceCount': 0}
            with patch.object(checker, 'ROOT', root), patch.object(checker, 'load_scrape_preset', return_value=preset), \
                    patch.object(checker, 'fetch_html_text', return_value='html'), patch.object(checker, 'assert_live_targets', return_value=[1, 2]), \
                    patch.object(checker, 'build_list_first_page_url', return_value='url'), \
                    patch.object(checker, 'verify_group', side_effect=[ValueError('画像集合が不一致'), good]), patch('sys.argv', ['check']):
                self.assertEqual(checker.main(), 1)
            report = json.loads(path.read_text())
            self.assertEqual(report['status'], 'failed')
            self.assertEqual(report['groupCount'], 1)
            self.assertEqual(report['failedGroups'][0]['group'], '202501')
            with patch.object(checker, 'ROOT', root), patch.object(checker, 'load_scrape_preset', return_value=preset), \
                    patch.object(checker, 'fetch_html_text', side_effect=ValueError('公開一覧取得失敗')), patch('sys.argv', ['check']):
                self.assertEqual(checker.main(), 1)
            self.assertEqual(json.loads(path.read_text())['reason'], '公開一覧取得失敗')

    def test_discovery_uses_era_and_maps_special_exam_without_duplicate_latest(self):
        html = '''<a href="/kakomon/07_aki/">過去問題解説</a>
        <a href="/kakomon/07_aki/">令和7年秋期</a>
        <a href="/kakomon/23_toku/">平成23年特別</a>
        <a href="/kakomon/21_haru/">平成21年春期</a>'''
        self.assertEqual(discover_targets(html), [
            {'source_list_group_id': '07_aki', 'output_list_group_id': '202502'},
            {'source_list_group_id': '23_toku', 'output_list_group_id': '201101'},
            {'source_list_group_id': '21_haru', 'output_list_group_id': '200901'},
        ])
        with self.assertRaises(ValueError):
            discover_targets('<a href="/kakomon/23_toku/">平成23年特別</a><a href="/kakomon/23_haru/">平成23年春期</a>')
        with self.assertRaises(ValueError):
            discover_targets('<html>取得できませんでした</html>')

    def test_css_number_and_html_list_are_content_and_keep_original_dom(self):
        node = BeautifulSoup('<div>手順(2)へ戻る。<ul><li class="li1">X<sub>1</sub>を初期化。</li>'
                             '<li class="li2">2<sup>3</sup>を加算。</li></ul>'
                             '<ol start="3"><li>条件</li><li value="7">分岐</li><li>終了</li></ol></div>', 'html.parser').div
        original = str(node)
        text = extract_q_text(node)
        self.assertIn('(1) X₁を初期化。', text)
        self.assertIn('(2) 2³を加算。', text)
        self.assertIn('3. 条件', text)
        self.assertIn('7. 分岐', text)
        self.assertIn('8. 終了', text)
        self.assertEqual(str(node), original)

    def test_invisible_comments_and_scripts_do_not_enter_question_text(self):
        node = BeautifulSoup('<div>取得ができない<!--可能な-->もの。<script>hidden()</script><style>hidden</style></div>', "html.parser").div
        self.assertEqual(extract_q_text(node), "取得ができないもの。")
        self.assertEqual(visible_source(node), "取得ができないもの。")

    def test_css_circled_steps_preserve_reference_numbers_and_detect_omission(self):
        node = BeautifulSoup('<div>②で取得した値を渡す。<ul><li class="maru1">要求する。</li>'
                             '<li class="maru7">値を取得する。</li><li class="maru1">再開する。</li></ul></div>', 'html.parser').div
        original = str(node)
        text = extract_q_text(node)
        self.assertIn('① 要求する。\n② 値を取得する。\n① 再開する。', text)
        self.assertEqual(visible_source(node), '②で取得した値を渡す。①要求する。②値を取得する。①再開する。')
        assert_numbered_explanation(node, text, 'test')
        with self.assertRaisesRegex(ValueError, '手順番号'):
            assert_numbered_explanation(node, text.replace('② ', ''), 'test')
        self.assertEqual(str(node), original)

    def test_semantic_math_markers_cannot_be_normalized_away(self):
        for left, right in [('A̅', 'A'), ('A̅̅', 'A̅'), ('2³', '23'), ('X₁', 'X1')]:
            self.assertNotEqual(compact(left), compact(right))
        node = BeautifulSoup('<div><span style="text-decoration:overline">A</span>'
                             '<span class="dol"><span class="ol">B</span></span></div>', 'html.parser').div
        self.assertEqual(visible_source(node), 'A̅B̅̅')

    def test_explanation_binding_detects_swapped_choices_and_lost_negation(self):
        page = BeautifulSoup('<div id="kaisetsu">定義。<ul><li class="lia">A<span class="ol">B</span></li>'
                             '<li class="lii">C</li><li class="liu">D</li><li class="lie">E</li></ul>結論。</div>', 'html.parser')
        record = {'explanation_common_prefix': ['定義。'], 'explanation_common_summary': ['結論。'],
                  'explanation_choice_snippets': [['AB̅'], ['C'], ['D'], ['E']]}
        assert_explanation_content(page, record, 'test')
        record['explanation_choice_snippets'][0] = ['AB']
        with self.assertRaisesRegex(ValueError, '対応又は表記'):
            assert_explanation_content(page, record, 'test')
        record['explanation_choice_snippets'] = [['C'], ['AB̅'], ['D'], ['E']]
        with self.assertRaisesRegex(ValueError, '対応又は表記'):
            assert_explanation_content(page, record, 'test')

    def test_missing_explanation_images_and_wrong_question_reference_fail(self):
        from PIL import Image
        node = BeautifulSoup('<div><img src="figure.png"></div>', 'html.parser').div
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory); images = output / 'question_images' / '202501'; images.mkdir(parents=True)
            Image.new('RGB', (1, 1)).save(images / 'qstable_exp_img01.png')
            url = 'https://www.sc-siken.com/kakomon/07_haru/am2_1.html'
            source = ['https://www.sc-siken.com/kakomon/07_haru/figure.png']
            refs = ['https://example.com/qstable_exp_img01.png']
            self.assertEqual(len(assert_image_references(node, refs, 'qstable_exp', url, output, '202501', source_urls=source)), 1)
            with self.assertRaisesRegex(ValueError, '画像集合'):
                assert_image_references(node, [], 'qstable_exp', url, output, '202501', source_urls=[])
            with self.assertRaisesRegex(ValueError, '別問題'):
                assert_image_references(node, ['https://example.com/qother_exp_img01.png'], 'qstable_exp', url, output, '202501', source_urls=source)

    def test_image_only_choice_explanations_preserve_common_text(self):
        page = BeautifulSoup('<div id="kaisetsu">定義。<ul><li class="lia"><img src="a.png"></li>'
                             '<li class="lii"><img src="i.png"></li><li class="liu"><img src="u.png"></li>'
                             '<li class="lie"><img src="e.png"></li></ul>結論。</div>', 'html.parser')
        record = {'explanation_choice_snippets': [['定義。結論。'] for _ in range(4)]}
        assert_explanation_content(page, record, 'test')

    def test_live_inventory_rejects_missing_session_without_pdf_download(self):
        preset = SimpleNamespace(scrape_targets=[SimpleNamespace(source_list_group_id='07_aki', output_list_group_id='202502')])
        assert_live_targets('<a href="/kakomon/07_aki/">令和7年秋期</a>', preset)
        with self.assertRaisesRegex(ValueError, '公開回とpreset'):
            assert_live_targets('<a href="/kakomon/07_haru/">令和7年春期</a>', preset)

    def test_pdf_inventory_detects_missing_file_and_wrong_session(self):
        html = '<a href="https://www.ipa.go.jp/sc_ans.pdf">午前Ⅱ解答</a><a href="/pdf/07_haru/pm1.pdf">問1</a>'
        url = 'https://www.sc-siken.com/kakomon/07_haru/'
        docs = document_links(html, url)
        for doc in docs:
            folder = 'official_pdfs' if doc['kind'] == 'official' else 'afternoon_explanations'
            doc['path'] = f'{folder}/202501/{doc["filename"]}'
        assert_document_inventory(html, url, docs, '202501')
        with self.assertRaisesRegex(ValueError, 'PDF集合'):
            assert_document_inventory(html, url, docs[:-1], '202501')
        docs[0]['path'] = 'official_pdfs/202401/sc_ans.pdf'
        with self.assertRaisesRegex(ValueError, '別試験'):
            assert_document_inventory(html, url, docs, '202501')

    def test_unreviewed_official_difference_does_not_pass(self):
        with tempfile.TemporaryDirectory() as tmp, self.assertRaisesRegex(ValueError, "目視照合receipt"):
            official_difference_receipt(Path(tmp), "https://example.com/q", BeautifulSoup("<div></div>", "html.parser"), 1, 2, [])

    def test_script_notation_keeps_uppercase_and_unsupported_letters(self):
        self.assertEqual(to_subscript("A"), "_(A)")
        self.assertEqual(to_subscript("B"), "_(B)")
        self.assertEqual(to_subscript("b"), "_(b)")
        self.assertEqual(to_superscript("N+1"), "^(N+1)")
        self.assertEqual(to_subscript("12"), "₁₂")
        self.assertEqual(to_superscript("n+1"), "ⁿ⁺¹")

    def test_official_answers_reject_missing_or_duplicate_question_numbers(self):
        def reader(text):
            return patch('scripts.check.check_scsiken_acquisition.PdfReader', return_value=SimpleNamespace(pages=[SimpleNamespace(extract_text=lambda: text)]))
        with reader('午前Ⅰ 問 1 ア\n問 2 エ'):
            self.assertEqual(official_answers(Path('answers.pdf'), 2), {1: 1, 2: 4})
        with reader('\n'.join(f'問{n} ア' for n in range(1, 10)) + '\n問1 0 エ'):
            self.assertEqual(official_answers(Path('answers.pdf'), 10)[10], 4)
        for text in ['問1 ア', '問1 ア\n問1 イ\n問2 エ', '問1 ア\n問3 エ']:
            with reader(text), self.assertRaises(ValueError):
                official_answers(Path('answers.pdf'), 2)

    def test_text_coverage_finds_omitted_plain_text_and_keeps_inline_math(self):
        node = BeautifulSoup('<div>定義<strong>用語</strong>には条件がある。X<sub>1</sub>と2<sup>3</sup>。</div>', 'html.parser').div
        assert_text_coverage(node, '定義用語には条件がある。X₁と2³。', 'test')
        with self.assertRaisesRegex(ValueError, 'テキストが欠落'):
            assert_text_coverage(node, '用語X₁と2³。', 'test')

    def test_documents_exclude_score_distribution_and_separate_official_from_explanation(self):
        html = '''<a href="https://www.ipa.go.jp/2025_sc_am2_ans.pdf">午前Ⅱ解答</a>
        <a href="/pdf/07_haru/pm1.pdf">問1</a><a href="/pdf/sc07h_bunpu.pdf">得点分布</a>'''
        docs = document_links(html, 'https://www.sc-siken.com/kakomon/07_haru/')
        self.assertEqual([d['kind'] for d in docs], ['official', 'explanation'])
        with self.assertRaises(ValueError):
            document_links('<a href="https://example.com/a.pdf">解答</a>', 'https://www.sc-siken.com/')

    def test_sc_preset_declares_morning_json_scope_and_special_session_mapping(self):
        preset = load_scrape_preset('sc')
        self.assertEqual(preset.scraper_type, 'sgsiken')
        self.assertEqual(preset.expected_question_count, 55)
        self.assertFalse(preset.include_afternoon_questions)
        self.assertEqual(len(preset.list_group_ids), 33)
        self.assertEqual(build_list_first_page_url(preset, '201101'), 'https://www.sc-siken.com/kakomon/23_toku/')
        self.assertTrue(load_scrape_preset('sg').include_afternoon_questions)

    def test_afternoon_scope_is_validated_as_boolean(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'config.json'
            path.write_text(json.dumps({'sc': {'include_afternoon_questions': 'false'}}))
            with self.assertRaisesRegex(ValueError, 'bool'):
                load_scrape_preset('sc', path)


if __name__ == '__main__':
    unittest.main()
