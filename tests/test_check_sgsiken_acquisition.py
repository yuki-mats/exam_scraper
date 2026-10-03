import copy
import unittest

from scripts.check.check_sgsiken_acquisition import audit_page, verify_inventory_html


class SikenAcquisitionAuditTests(unittest.TestCase):
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
