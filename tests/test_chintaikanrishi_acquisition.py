from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import scrape_sgsiken as dojo
from scripts.scrape.common import save_source_snapshot
from scripts.scrape.qualification_presets import load_scrape_preset


def numbered_html(*, count_question: bool = False) -> str:
    body = '正しいものはいくつあるか。<ol class="kanaList"><li>命題ア。</li><li>命題イ。</li></ol>' if count_question else '正しい記述はどれか。'
    explanation_class = 'kaisetsuList kanaList' if count_question else 'kaisetsuList'
    return f'''<h2>賃貸不動産経営管理士 令和7年試験 問2</h2>
    <div class="mondai">{body}</div><ol class="selectList">
    <li>A<li data-answer="t">B<li>C<li>D</ol>
    <section class="answerBox"><span class="answerChar">2</span></section>
    <section class="kaisetsu"><h3>解説</h3><ol class="{explanation_class}">
    <li>理由A</li><li>理由B</li><li>理由C</li><li>理由D</li></ol>結論。</section>'''


class ChintaikanrishiAcquisitionTests(unittest.TestCase):
    def parse(self, html: str) -> dict:
        with patch.dict(os.environ, {"QUESTION_ID_SECRET_KEY": "test-secret"}), patch.object(dojo, "QUALIFICATION_CODE", "chintaikanrishi"):
            return dojo.parse_q_question_page(html, "https://chintaikanrishi-siken.com/kakomon/2025/02.html",
                                             http_session=None, download_images=False, output_list_group_id="2025")

    def test_omitted_li_closing_tags_do_not_mix_choices(self):
        record = self.parse(numbered_html())
        self.assertEqual(record['choiceTextList'], ['A', 'B', 'C', 'D'])
        self.assertEqual(record['correctChoiceText'], 'B')
        self.assertEqual(record['answer_result_inferred_correct_choice_numbers'], [2])
        self.assertNotIn('questionType', record)
        self.assertEqual(record['canonical_question_key'], 'chintaikanrishi:2025:q002')

    def test_answer_mark_disagreement_and_wrong_heading_stop_acquisition(self):
        for html in (numbered_html().replace('data-answer="t"', ''), numbered_html().replace('令和7年', '令和6年')):
            with self.assertRaises(ValueError):
                self.parse(html)

    def test_count_question_keeps_statement_labels_and_whole_explanation(self):
        record = self.parse(numbered_html(count_question=True))
        self.assertIn('ア　命題ア。', record['questionBodyText'])
        self.assertIn('イ　命題イ。', record['questionBodyText'])
        self.assertEqual(record['explanation_choice_snippets'], [[], [], [], []])
        self.assertIn('理由B', record['explanation_common_prefix'][0])

    def test_presets_cover_500_dojo_questions_and_existing_source_groups(self):
        recent = load_scrape_preset('chintaikanrishi_dojo')
        old = load_scrape_preset('chintaikanrishi_dojo_legacy')
        self.assertEqual(set(recent.list_group_ids + old.list_group_ids), {str(y) for y in range(2015, 2026)})
        self.assertEqual(len(recent.list_group_ids) * recent.expected_question_count + len(old.list_group_ids) * old.expected_question_count, 500)
        existing = load_scrape_preset('chintaikanrishi_kakomonn')
        self.assertEqual(set(existing.list_group_ids), {str(group) for group in range(88001, 88012)})

    def test_refresh_preserves_partial_file_layout_and_existing_ids(self):
        old = dict(source_question_id='old', public_question_id='published', original_question_id='original',
                   questionBodyText='旧本文', choiceTextList=['A', 'B'], answer_result_inferred_correct_choice_numbers=[1])
        updated = {**old, 'public_question_id': 'recomputed', 'questionBodyText': '新本文'}
        new = {**old, 'source_question_id': 'new', 'public_question_id': 'new-public'}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / 'question_88002_2.json'
            path.write_text(json.dumps({'list_group_id': '88002', 'question_bodies': [old]}))
            baseline = path.read_bytes()
            for invalid in ([new], [updated, updated], [{**updated, 'answer_result_inferred_correct_choice_numbers': [3]}]):
                with self.assertRaises(ValueError):
                    save_source_snapshot(directory, '88002', invalid, expected_count=len(invalid))
                self.assertEqual(path.read_bytes(), baseline)
            report = save_source_snapshot(directory, '88002', [new, updated], expected_count=2)
            stored = json.loads(path.read_text())['question_bodies'][0]
            self.assertEqual(stored['public_question_id'], 'published')
            self.assertEqual(stored['original_question_id'], 'original')
            self.assertEqual(report['changedSourceQuestionIds'], ['old'])
            self.assertEqual(report['newSourceQuestionIds'], ['new'])
            self.assertTrue((root / 'question_88002_1.json').exists())
            report = save_source_snapshot(directory, '88002', [updated, new], expected_count=2)
            self.assertEqual(set(report['unchangedSourceQuestionIds']), {'old', 'new'})
            report = save_source_snapshot(directory, '88002', [updated, {**new, 'public_question_id': 'corrected-new-id'}],
                                          expected_count=2, identity_baseline={'old': old})
            self.assertEqual(json.loads(path.read_text())['question_bodies'][0]['public_question_id'], 'published')
            self.assertEqual(report['preservedIdentityCount'], 1)
            new_path = root / 'question_88002_1.json'
            self.assertEqual(json.loads(new_path.read_text())['question_bodies'][0]['public_question_id'], 'corrected-new-id')


if __name__ == '__main__':
    unittest.main()
