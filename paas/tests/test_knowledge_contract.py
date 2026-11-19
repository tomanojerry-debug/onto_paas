"""Confirmed meanings, upstream Schema and extractive answer contracts."""
import copy
import json
import unittest
from unittest.mock import patch

from paas import knowledge
from paas.tests import test_knowledge as fixtures


class FillSequence:
    def __init__(self, responses):
        self.responses = copy.deepcopy(responses)
        self.calls = []
        self.inputs = []

    def json(self, system, user, *, purpose, max_tokens):
        self.calls.append(purpose)
        self.inputs.append(json.loads(user))
        return self.responses.pop(0)


class KnowledgeContractTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.KnowledgeTests()
        self.project = self.fixture.project()

    def structured(self, models=None):
        return self.fixture.structured(models or fixtures.fixture_knowledge_models())

    def test_second_pass_cannot_change_a_confirmed_definition(self):
        for kind in knowledge.KINDS:
            with self.subTest(kind=kind):
                models = self.structured()
                models[kind][kind][0]['define'] = '全部申请无需检查即可直接批准。'
                with self.assertRaisesRegex(ValueError, '定义|define'):
                    knowledge.fill(self.project, fixtures.KnowledgeStub({'knowledge.fill': models}))

    def test_omitted_optional_definitions_are_restored_from_confirmed_inventory(self):
        result = knowledge.fill(self.project, fixtures.KnowledgeStub({'knowledge.fill': self.structured()}))
        for kind in knowledge.KINDS:
            self.assertEqual(result[kind + '.yaml'][kind][0]['define'],
                             self.project['knowledge_inventory'][kind][0]['define'])

    def test_second_round_ipo_requires_source_evidence_while_legacy_artifact_loads(self):
        old = self.structured()
        concept = old['concepts']['concepts'][0]
        concept.pop('ipo_evidence')
        knowledge.validate(old, self.project['documents'])
        with self.assertRaisesRegex(ValueError, 'IPO.*原文'):
            knowledge.fill(self.project, FillSequence([old, old]))

        fabricated = self.structured()
        concept = fabricated['concepts']['concepts'][0]
        fake = '申请必须等待 912 天。'
        concept['ipo']['process']['steps'][0] = fake
        concept['ipo_evidence']['process_steps'][0]['quote'] = fake
        result = knowledge.fill(self.project, FillSequence([fabricated, self.structured()]))
        self.assertEqual(result['concepts.yaml']['ipo_evidence_version'], 1)
        self.assertNotIn('912', str(result))

    def test_unknown_concept_and_entity_enumerations_are_rejected(self):
        for kind, field in (('concepts', 'type'), ('entities', 'type'), ('entities', 'category')):
            with self.subTest(kind=kind, field=field):
                models = self.structured()
                models[kind][kind][0][field] = '不存在的分类或类型'
                with self.assertRaisesRegex(ValueError, '枚举|分类|类型'):
                    knowledge.validate(models, self.project['documents'])

    def test_entity_category_must_agree_with_its_type(self):
        models = self.structured()
        models['entities']['entities'][0].update(category='人物与组织', type='工具')
        with self.assertRaisesRegex(ValueError, '分类|类型'):
            knowledge.validate(models, self.project['documents'])

    def test_nested_entity_requires_the_direct_parent_reference(self):
        models = self.structured()
        parent = models['entities']['entities'][0]
        child = copy.deepcopy(parent)
        child['id'] = '子清单'
        parent['children'] = [child]
        with self.assertRaisesRegex(ValueError, '父|is_a'):
            knowledge.validate(models, self.project['documents'])
        child['relations']['is_a'] = ['entity://' + parent['id']]
        knowledge.validate(models, self.project['documents'])

    def test_entity_parent_cycle_is_rejected(self):
        models = self.structured()
        parent = models['entities']['entities'][0]
        child = copy.deepcopy(parent)
        child['id'] = '子清单'
        child['relations']['is_a'] = ['entity://' + parent['id']]
        parent['children'] = [child]
        parent['relations']['is_a'] = ['entity://子清单']
        with self.assertRaisesRegex(ValueError, '循环|父'):
            knowledge.validate(models, self.project['documents'])

    def test_free_answer_does_not_enter_the_verified_extractive_output(self):
        responses = {'knowledge.locate': {'sources': ['raw/评审.md'], 'outline': ['审批额度']},
                     'knowledge.answer': {'answer': '每份申请自动获得100万元补助。[raw/评审.md]',
                                          'citations': [{'source': 'raw/评审.md', 'quote': '清单工具用于记录检查结果。'}]}}
        result = knowledge.ask(self.project, '申请能获得多少补助？', fixtures.KnowledgeStub(responses))
        self.assertNotIn('100万元', result['answer'])
        self.assertIn('清单工具用于记录检查结果。', result['answer'])
        self.assertEqual(result['answer_mode'], 'source_excerpt')
        self.assertFalse(result['quality']['semantic_quality_passed'])
        self.assertEqual(result['quality']['relevance'], 'not_independently_evaluated')

    def test_rejected_paraphrase_is_not_published_as_a_supported_claim(self):
        responses = {'knowledge.locate': fixtures.fixture_locator(),
                     'knowledge.answer': {'claims': [{'text': '全部申请自动批准。', 'source': 'raw/评审.md',
                                                     'quote': '清单工具用于记录检查结果。'}]},
                     'knowledge.verify': {'claims': [{'id': 'C1', 'supported': False, 'reason': '原文未说明自动批准'}],
                                          'question_answered': False}}
        result = knowledge.ask(self.project, '怎样评审？', fixtures.KnowledgeStub(responses))
        self.assertNotIn('自动批准', result['answer'])
        self.assertEqual(result['answer_mode'], 'source_excerpt')
        self.assertFalse(result['verification']['supported'])

    def test_claims_are_rendered_only_from_current_raw_quotes(self):
        quote = '评审检查是逐项核对材料并记录结论。'
        responses = {'knowledge.locate': fixtures.fixture_locator(),
                     'knowledge.answer': {'claims': [{'text': quote, 'source': 'raw/评审.md', 'quote': quote}],
                                          'answer': '不能展示的额外结论'}}
        stub = fixtures.KnowledgeStub(responses)
        result = knowledge.ask(self.project, '怎样评审？', stub)
        self.assertIn(quote, result['answer'])
        self.assertNotIn('额外结论', result['answer'])
        self.assertEqual(stub.calls, ['knowledge.locate', 'knowledge.answer'])
        self.assertEqual(result['claims'][0]['quote'], quote)

    def test_retrieval_rejects_unknown_scene_phase_nodes_and_detached_model_sources(self):
        self.project['documents'].append({'path': 'raw/预算.md', 'text': '评审检查是逐项核对材料并记录结论。'})
        extra_concept = copy.deepcopy(self.project['models']['concepts.yaml']['concepts'][0])
        extra_concept.update(id='预算分析', sources=['raw/预算.md'])
        extra_concept.pop('ipo_evidence')
        self.project['models']['concepts.yaml']['concepts'].append(extra_concept)
        self.project['models_confirmation'] = {
            'sha256': fixtures.digest(self.project['models']),
            'documents_sha256': knowledge.documents_digest(self.project),
        }
        for override in (
            {'scenarios': [{'id': '如何审计预算', 'phases': ['检查']}]},
            {'scenarios': [{'id': '如何完成评审', 'phases': ['不存在阶段']}]},
            {'concepts': ['concept://不存在']},
            {'sources': ['raw/评审.md', 'raw/预算.md']},
            {'concepts': ['concept://评审检查', 'concept://预算分析'], 'sources': ['raw/评审.md', 'raw/预算.md']},
        ):
            with self.subTest(override=override):
                stub = fixtures.KnowledgeStub({'knowledge.locate': fixtures.fixture_locator() | override})
                with self.assertRaisesRegex(ValueError, '纲要|阶段|问答原文'):
                    knowledge.ask(self.project, '怎样评审？', stub)
                self.assertEqual(stub.calls, ['knowledge.locate'])

    def test_concept_only_and_legacy_source_fallback_keep_different_guarantees(self):
        quote = '评审检查是逐项核对材料并记录结论。'
        for locator in (
            fixtures.fixture_locator() | {'mode': 'concept_only', 'scenarios': []},
            {'sources': ['raw/评审.md'], 'outline': ['不存在的场景及概念']},
        ):
            stub = fixtures.KnowledgeStub({
                'knowledge.locate': locator,
                'knowledge.answer': {'claims': [{'text': quote, 'source': 'raw/评审.md', 'quote': quote}]},
            })
            result = knowledge.ask(self.project, '评审检查是什么？', stub)
            self.assertEqual(result['retrieval']['mode'], locator.get('mode', 'source_fallback'))
            self.assertEqual(result['answer_mode'], 'source_excerpt')

    def test_paraphrase_has_a_separate_bounded_model_review_without_a_quality_guarantee(self):
        responses = {'knowledge.locate': fixtures.fixture_locator(),
                     'knowledge.answer': {'claims': [{'text': '应逐项检查材料并记录结论。', 'source': 'raw/评审.md',
                                                     'quote': '评审检查是逐项核对材料并记录结论。'}]},
                     'knowledge.verify': {'claims': [{'id': 'C1', 'supported': True, 'reason': '结论与原文一致'}],
                                          'question_answered': True}}
        stub = fixtures.KnowledgeStub(responses)
        result = knowledge.ask(self.project, '怎样评审？', stub)
        self.assertEqual(stub.calls, ['knowledge.locate', 'knowledge.answer', 'knowledge.verify'])
        self.assertEqual(result['answer_mode'], 'model_checked_claims')
        self.assertTrue(result['verification']['supported'])
        self.assertFalse(result['verification']['deterministic_guarantee'])
        self.assertFalse(result['quality']['semantic_quality_passed'])

    def test_invented_numbers_fall_back_before_another_model_can_approve_them(self):
        responses = {'knowledge.locate': fixtures.fixture_locator(),
                     'knowledge.answer': {'claims': [{'text': '每份申请获得100万元。', 'source': 'raw/评审.md',
                                                     'quote': '清单工具用于记录检查结果。'}]}}
        stub = fixtures.KnowledgeStub(responses)
        result = knowledge.ask(self.project, '申请能获得多少？', stub)
        self.assertNotIn('100万元', result['answer'])
        self.assertEqual(result['answer_mode'], 'source_excerpt')
        self.assertEqual(stub.calls, ['knowledge.locate', 'knowledge.answer'])

    def test_missing_or_duplicate_review_entries_cannot_approve_all_claims(self):
        for review in ({'claims': [], 'question_answered': True},
                       {'claims': [{'id': 'C2', 'supported': True, 'reason': 'wrong id'}], 'question_answered': True}):
            responses = {'knowledge.locate': fixtures.fixture_locator(),
                         'knowledge.answer': {'claims': [{'text': '应核对材料。', 'source': 'raw/评审.md',
                                                         'quote': '评审检查是逐项核对材料并记录结论。'}]},
                         'knowledge.verify': review}
            result = knowledge.ask(self.project, '怎样评审？', fixtures.KnowledgeStub(responses))
            self.assertEqual(result['answer_mode'], 'source_excerpt')
            self.assertFalse(result['verification']['supported'])

    def test_definition_outside_current_sources_is_rejected_during_model_validation(self):
        models = self.structured()
        models['concepts']['concepts'][0]['define'] = '原文不存在的知识定义。'
        with self.assertRaisesRegex(ValueError, '定义|原文'):
            knowledge.validate(models, self.project['documents'])

    def test_fill_repairs_one_invalid_scenario_relation_without_relaxing_schema(self):
        invalid = self.structured()
        invalid['concepts']['concepts'][0]['relations'] = {'related_to': ['scenario://如何完成评审']}
        stub = FillSequence([invalid, self.structured()])
        result = knowledge.fill(self.project, stub)
        self.assertEqual(stub.calls, ['knowledge.fill', 'knowledge.fill.repair'])
        self.assertIn('scenario://如何完成评审', stub.inputs[1]['validator_error'])
        for kind in knowledge.KINDS:
            self.assertEqual(result[kind + '.yaml'][kind][0]['define'],
                             self.project['knowledge_inventory'][kind][0]['define'])
        knowledge.validate(self.structured(result), self.project['documents'])

    def test_fill_rejects_after_a_second_invalid_structure(self):
        invalid = self.structured()
        invalid['concepts']['concepts'][0]['relations'] = {'related_to': ['scenario://如何完成评审']}
        stub = FillSequence([invalid, invalid, self.structured()])
        with self.assertRaisesRegex(ValueError, '一次|修复|引用'):
            knowledge.fill(self.project, stub)
        self.assertEqual(stub.calls, ['knowledge.fill', 'knowledge.fill.repair'])

    def test_confirmed_id_definition_or_source_changes_do_not_enter_repair(self):
        for field, value in (('define', '全部申请无需检查即可通过。'),
                             ('sources', ['raw/另一个来源.md']), ('id', '替代概念')):
            with self.subTest(field=field):
                changed = self.structured()
                changed['concepts']['concepts'][0][field] = value
                stub = FillSequence([changed, self.structured()])
                with self.assertRaises(ValueError):
                    knowledge.fill(self.project, stub)
                self.assertEqual(stub.calls, ['knowledge.fill'])

    def test_repair_cannot_replace_a_confirmed_definition_or_source(self):
        invalid = self.structured()
        invalid['concepts']['concepts'][0]['relations'] = {'related_to': ['scenario://如何完成评审']}
        for field, value in (('define', '全部申请无需检查即可通过。'), ('sources', ['raw/另一个来源.md'])):
            with self.subTest(field=field):
                repaired = self.structured()
                repaired['concepts']['concepts'][0][field] = value
                stub = FillSequence([invalid, repaired, self.structured()])
                with self.assertRaises(ValueError):
                    knowledge.fill(self.project, stub)
                self.assertEqual(stub.calls, ['knowledge.fill', 'knowledge.fill.repair'])

    def test_fill_updated_at_is_service_utc_metadata_not_a_model_claim(self):
        models = self.structured()
        for model in models.values():
            model['updated_at'] = '2025-01-01'
        stub = FillSequence([models])
        with patch.object(knowledge, '_utc_today', return_value='2040-01-02'):
            result = knowledge.fill(self.project, stub)
        self.assertEqual({model['updated_at'] for model in result.values()}, {'2040-01-02'})
        self.assertEqual(stub.calls, ['knowledge.fill'])


if __name__ == '__main__':
    unittest.main()
