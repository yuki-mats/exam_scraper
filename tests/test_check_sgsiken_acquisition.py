import copy
import unittest

from scripts.check.check_sgsiken_acquisition import audit_page, verify_inventory_html, image_bindings


class SikenAcquisitionAuditTests(unittest.TestCase):
    def test_live_hash_audit_rejects_a_valid_but_different_image(self):
        import gzip
        import io
        import json
        import tempfile
        from pathlib import Path
        from types import SimpleNamespace
        from unittest.mock import patch
        from PIL import Image
        from scripts.check.check_sgsiken_acquisition import main
        def png(color):
            buffer = io.BytesIO()
            Image.new('RGB', (2, 2), color).save(buffer, format='PNG')
            return buffer.getvalue()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / 'sg'
            evidence = root / 'verification/dojo/202501'
            evidence.mkdir(parents=True)
            for path, html in [(evidence.parent / 'inventory.html.gz', '<a href="/kakomon/07_haru/">年度</a>'),
                               (evidence / 'index.html.gz', '<a href="a1.html">問1</a>'),
                               (evidence / 'a1.html.gz', self.html)]:
                with gzip.open(path, 'wt') as stream:
                    stream.write(html)
            record = copy.deepcopy(self.record)
            record.update(public_question_id='stable', source_question_id='202501:q1', list_group_id='202501',
                          question_url=self.url, questionImageStorageUrls=['qstable_q_img01.png'])
            source = root / 'questions_json/202501/00_source'
            source.mkdir(parents=True)
            (source / 'question.json').write_text(json.dumps({'question_bodies': [record]}))
            image = root / 'question_images/202501/qstable_q_img01.png'
            image.parent.mkdir(parents=True)
            image.write_bytes(png('red'))
            preset = SimpleNamespace(list_group_ids=['202501'])
            for expected_result, remote in [(False, png('red')), (True, png('blue'))]:
                with patch('sys.argv', ['check', 'sg', '202501', '--output-dir', directory, '--verify-live-images']), \
                     patch('scripts.check.check_sgsiken_acquisition.load_scrape_preset', return_value=preset), \
                     patch('scripts.check.check_sgsiken_acquisition.build_list_first_page_url', return_value='https://www.sg-siken.com/kakomon/07_haru/'), \
                     patch('scripts.check.check_sgsiken_acquisition.download_image_with_retry', return_value=remote):
                    self.assertEqual(main(), expected_result)

    def test_explanation_image_missing_or_wrong_question_reference_fails(self):
        html = self.html.replace('定義と根拠。', '定義と根拠。<img src="exp.png">')
        record = copy.deepcopy(self.record)
        record['public_question_id'] = 'stable'
        record['explanationImageSourceUrls'] = ['https://www.sg-siken.com/kakomon/07_haru/exp.png']
        record['explanationImageStorageUrls'] = ['qstable_exp_img01.png']
        self.assertEqual(audit_page(html, [record], self.url), [])
        record['explanationImageStorageUrls'] = ['qother_exp_img01.png']
        self.assertIn('explanation_image_binding', audit_page(html, [record], self.url))
        record['explanationImageStorageUrls'] = []
        self.assertIn('explanation_image_binding', audit_page(html, [record], self.url))
        record['explanationImageSourceUrls'] = []
        self.assertIn('explanation_image_sources', audit_page(html, [record], self.url))

    def test_pm_explanation_deletion_math_and_numbering_loss_fail(self):
        from scrape_sgsiken import parse_pm_question_page
        html = '''<h2>平成29年秋期 午後問2</h2><h3 class="qno">問2</h3><div class="mondai">共通。</div>
        <h3 id="s1">設問1</h3><div class="mondai">(1) 適切なものはどれか。</div><div class="inputAnswerBox">
        <select name="sel_1"><option>-</option><option>ア A</option><option>イ B</option></select></div>
        <div><div class="answerChars"><span id="ans_1">イ</span></div></div>
        <div class="kaisetsu">10<sup>4</sup>通り。<ol type="i"><li>A。</li><li>B。</li></ol></div>'''
        url = 'https://www.sg-siken.com/kakomon/29_aki/pm02.html'
        import os
        from unittest.mock import patch
        with patch.dict(os.environ, {'QUESTION_ID_SECRET_KEY': 'test-secret'}):
            records = parse_pm_question_page(html, url, http_session=None, download_images=False, output_list_group_id='201702')
        self.assertEqual(audit_page(html, records, url), [])
        for bad in ['', '104通り。i. A。ii. B。', '10⁴通り。A。B。', '別設問の説明。']:
            record = copy.deepcopy(records[0])
            record['explanation_common_prefix'] = [bad] if bad else []
            self.assertIn('explanation_text', audit_page(html, [record], url))

    def test_image_order_is_bound_to_source_url_and_purpose(self):
        from bs4 import BeautifulSoup
        nodes = [BeautifulSoup('<div><img src="a.png"><img src="b.png"></div>', 'html.parser').div]
        self.assertEqual(len(image_bindings(nodes, ['q1_exp_img01.png', 'q1_exp_img02.png'], 'q1_exp', self.url)), 2)
        with self.assertRaises(ValueError):
            image_bindings(nodes, ['q1_exp_img02.png', 'q1_exp_img01.png'], 'q1_exp', self.url)

    def test_new_year_missing_from_preset_and_missing_listed_year_fail(self):
        index = "https://www.sg-siken.com/sgkakomon.php"
        old = "https://www.sg-siken.com/kakomon/07_haru/"
        new = "https://www.sg-siken.com/kakomon/08_haru/"
        html = '<a href="/kakomon/07_haru/">旧年度</a><a href="/kakomon/08_haru/">新年度</a><a href="/kakomon/sample/">サンプル</a>'
        self.assertEqual(verify_inventory_html(html, {old, new}, index), {old, new})
        with self.assertRaises(ValueError):
            verify_inventory_html(html, {old}, index)
        with self.assertRaises(ValueError):
            verify_inventory_html('<a href="/kakomon/07_haru/">旧年度</a>', {old, new}, index)

    def setUp(self):
        self.url = "https://www.sg-siken.com/kakomon/07_haru/a1.html"
        self.html = """<h2>情報セキュリティマネジメント令和7年度 問1</h2>
        <div id="mondai">正しい説明はどれか。<img src="figure.png"></div>
        <ul class="selectList"><li><button class="selectBtn">ア</button>説明A</li>
        <li><button class="selectBtn">イ</button>説明B</li></ul>
        <div class="answerBox"><span id="answerChar">イ</span></div>
        <div id="kaisetsu">定義と根拠。<ul><li class="lia">Aの理由。</li>
        <li class="lii">Bの理由。</li></ul></div>"""
        self.record = {
            "examYear": 2025, "questionBodyText": "正しい説明はどれか。",
            "choiceTextList": ["説明A", "説明B"],
            "questionImageStorageUrls": ["figure.png"],
            "originalQuestionChoiceImageUrls": [[], []],
            "answer_result_inferred_correct_choice_numbers": [2],
            "explanation_common_prefix": ["定義と根拠。"],
            "explanation_choice_snippets": [["Aの理由。"], ["Bの理由。"]],
        }

    def test_complete_record_passes(self):
        self.assertEqual(audit_page(self.html, [self.record], self.url), [])

    def test_missing_content_wrong_answer_and_swapped_choices_fail(self):
        cases = (
            ("questionBodyText", "説明はどれか。", "question_text"),
            ("choiceTextList", ["説明B", "説明A"], "choice_text_order"),
            ("answer_result_inferred_correct_choice_numbers", [1], "answer"),
            ("explanation_common_prefix", [], "explanation_text"),
            ("questionImageStorageUrls", [], "question_image_count"),
        )
        for field, value, failure in cases:
            with self.subTest(field=field):
                record = copy.deepcopy(self.record)
                record[field] = value
                self.assertIn(failure, audit_page(self.html, [record], self.url))


if __name__ == "__main__":
    unittest.main()
