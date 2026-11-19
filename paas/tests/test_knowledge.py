"""Knowledge source, human gate, reference and citation checks; no provider calls."""
import copy
import hashlib
import json
import unittest

from paas import knowledge
from paas.store import digest


def fixture_documents():
    text = ('评审检查是逐项核对材料并记录结论。\n'
            '清单工具用于记录检查结果。\n'
            '收到申请后按照检查清单逐项评审。\n'
            '所需输入是申请材料。\n'
            '按清单逐项检查申请材料。\n'
            '最后形成书面检查结论。')
    return [{'path': 'raw/评审.md', 'name': '评审.md', 'text': text,
             'sha256': hashlib.sha256(text.encode()).hexdigest()}]


def fixture_inventory():
    return {'scenarios': [{'id': '如何完成评审', 'define': '收到申请后按照检查清单逐项评审。', 'sources': ['raw/评审.md']}],
            'concepts': [{'id': '评审检查', 'define': '评审检查是逐项核对材料并记录结论。', 'sources': ['raw/评审.md']}],
            'entities': [{'id': '清单工具', 'define': '清单工具用于记录检查结果。', 'sources': ['raw/评审.md']}]}


def fixture_knowledge_models():
    header = {'version': '2.0', 'updated_at': '2026-11-18'}
    input_text, step_text, output_text = ('所需输入是申请材料。', '按清单逐项检查申请材料。', '最后形成书面检查结论。')
    return {
        'scenarios.yaml': {**header, 'scenarios': [{'id': '如何完成评审', 'sources': ['raw/评审.md'],
            'trigger': '收到申请', 'goal': '记录检查结论',
            'composition': [{'phase': '检查', 'uses': ['concept://评审检查', 'entity://清单工具']}]}]},
        'concepts.yaml': {**header, 'concepts': [{'id': '评审检查', 'sources': ['raw/评审.md'],
            'define': '评审检查是逐项核对材料并记录结论。', 'type': '思维方法',
            'ipo': {'input': [input_text], 'process': {'steps': [step_text], 'tools': ['entity://清单工具']},
                    'output': [output_text]},
            'ipo_evidence': {key: [{'source': 'raw/评审.md', 'quote': quote}]
                             for key, quote in (('input', input_text), ('process_steps', step_text), ('output', output_text))}}]},
        'entities.yaml': {**header, 'entities': [{'id': '清单工具', 'sources': ['raw/评审.md'],
            'category': '工具与平台', 'type': '工具', 'relations': {'uses': ['concept://评审检查']}}]},
    }


def fixture_locator():
    return {'mode': 'scenario', 'scenarios': [{'id': '如何完成评审', 'phases': ['检查']}],
            'concepts': ['concept://评审检查'], 'entities': ['entity://清单工具'], 'sources': ['raw/评审.md']}


class KnowledgeStub:
    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    def json(self, system, user, *, purpose, max_tokens):
        self.calls.append(purpose)
        return copy.deepcopy(self.responses[purpose])


class KnowledgeTests(unittest.TestCase):
    def project(self):
        project = {'documents': fixture_documents(), 'knowledge_inventory': fixture_inventory(),
                   'models': fixture_knowledge_models()}
        docs_hash = knowledge.documents_digest(project)
        project['inventory_confirmation'] = {'sha256': digest(project['knowledge_inventory']), 'documents_sha256': docs_hash}
        project['models_confirmation'] = {'sha256': digest(project['models']), 'documents_sha256': docs_hash}
        return project

    def structured(self, models):
        return {kind: models[kind + '.yaml'] for kind in knowledge.KINDS}

    def test_all_three_models_and_real_sources_validate(self):
        knowledge.validate(self.structured(fixture_knowledge_models()), fixture_documents())
        knowledge.validate(fixture_inventory(), fixture_documents(), skeleton=True)

    def test_round_one_applies_cross_batch_synonym_merge_with_source_and_review(self):
        definition = '独立思维是独立判断信息。'
        documents = [{'path': f'raw/{index}.md', 'text': definition} for index in range(6)]
        merge = {'kind': 'concepts', 'kept_id': '独立思维', 'merged_ids': ['独立思考'], 'reason': '同义'}

        class TwoBatches:
            def json(self, system, payload, *, purpose, max_tokens):
                batch = json.loads(payload)
                if purpose == 'knowledge.inventory.consolidate':
                    assert '综合去重' in system
                    assert len(batch['concepts']) == 2
                    return {'merges': [merge]}
                first = batch[0]['path'] == 'raw/0.md'
                return {'scenarios': [], 'entities': [], 'concepts': [
                    {'id': '独立思维' if first else '独立思考', 'define': definition, 'sources': [batch[0]['path']]},
                ]}

        result = knowledge.inventory({'documents': documents}, TwoBatches())
        self.assertEqual(result['concepts'], [
            {'id': '独立思维', 'define': definition, 'sources': ['raw/0.md', 'raw/5.md']},
        ])
        self.assertEqual(result['_review']['before_counts']['concepts'], 2)
        self.assertEqual(result['_review']['after_counts']['concepts'], 1)
        self.assertEqual(result['_review']['merges'][0]['merged_ids'], ['独立思考'])

    def test_candidates_require_definitions_that_exist_in_cited_raw(self):
        for definition in ('', '原文没有这句知识'):
            candidates = fixture_inventory()
            candidates['concepts'][0]['define'] = definition
            with self.assertRaises(ValueError):
                knowledge.validate(candidates, fixture_documents(), skeleton=True)

    def test_scan_repairs_missing_scenario_definition_and_unverifiable_punctuation_once(self):
        faulty = fixture_inventory()
        del faulty['scenarios'][0]['define']
        faulty['concepts'][0]['define'] = '评审检查是逐项核对材料。'
        model = KnowledgeStub({'knowledge.inventory': faulty,
                               'knowledge.inventory.contract-repair': fixture_inventory()})
        result = knowledge.inventory({'documents': fixture_documents()}, model)
        self.assertEqual(model.calls, ['knowledge.inventory', 'knowledge.inventory.contract-repair'])
        for kind in knowledge.KINDS:
            self.assertEqual(result[kind], fixture_inventory()[kind])
        model = KnowledgeStub({'knowledge.inventory': faulty, 'knowledge.inventory.contract-repair': faulty})
        with self.assertRaisesRegex(ValueError, '缺少可供确认的定义'):
            knowledge.inventory({'documents': fixture_documents()}, model)
        self.assertEqual(len(model.calls), 2)

    def test_invalid_source_and_structured_reference_are_rejected(self):
        models = fixture_knowledge_models()
        models['scenarios.yaml']['scenarios'][0]['composition'][0]['uses'] = ['concept://不存在']
        with self.assertRaisesRegex(ValueError, '引用不存在'):
            knowledge.validate(self.structured(models), fixture_documents())
        candidates = fixture_inventory()
        candidates['concepts'][0]['sources'] = ['raw/其他项目.md']
        with self.assertRaisesRegex(ValueError, '跨项目'):
            knowledge.validate(candidates, fixture_documents(), skeleton=True)

    def test_unknown_relation_and_wrong_instance_direction_are_rejected(self):
        for relation in ({'invented_relation': ['concept://评审检查']}, {'instance_of': ['entity://清单工具']}):
            models = fixture_knowledge_models()
            models['entities.yaml']['entities'][0]['relations'] = relation
            with self.assertRaises(ValueError):
                knowledge.validate(self.structured(models), fixture_documents())
        models = fixture_knowledge_models()
        models['concepts.yaml']['concepts'][0]['relations'] = {'instance_of': ['entity://清单工具']}
        with self.assertRaisesRegex(ValueError, 'instance_of'):
            knowledge.validate(self.structured(models), fixture_documents())

    def test_nested_entity_ids_are_indexed_and_depth_is_bounded(self):
        models = fixture_knowledge_models()
        parent = models['entities.yaml']['entities'][0]
        child = {**copy.deepcopy(parent), 'id': '子清单'}
        child['relations']['is_a'] = ['entity://' + parent['id']]
        parent['children'] = [child]
        models['concepts.yaml']['concepts'][0]['ipo']['process']['tools'] = ['entity://子清单']
        knowledge.validate(self.structured(models), fixture_documents())
        child['children'] = [{**copy.deepcopy(child), 'id': '第三层', 'children': [copy.deepcopy(child)]}]
        with self.assertRaisesRegex(ValueError, '3层'):
            knowledge.validate(self.structured(models), fixture_documents())

    def test_fill_rechecks_inventory_and_source_hash_before_model_call(self):
        for change in ('inventory', 'documents'):
            project = self.project()
            if change == 'inventory':
                project['knowledge_inventory']['concepts'][0]['id'] = '替换'
            else:
                project['documents'][0]['text'] += '\n原文已修订'
                project['documents'][0].pop('sha256')
            stub = KnowledgeStub({})
            with self.assertRaisesRegex(ValueError, '确认'):
                knowledge.fill(project, stub)
            self.assertEqual(stub.calls, [])

    def test_second_pass_cannot_change_confirmed_sources(self):
        project = self.project()
        result = self.structured(fixture_knowledge_models())
        result['concepts']['concepts'][0]['sources'] = ['raw/另一份.md']
        with self.assertRaisesRegex(ValueError, 'ID或来源'):
            knowledge.fill(project, KnowledgeStub({'knowledge.fill': result}))

    def test_second_pass_returns_three_model_files(self):
        project = self.project()
        result = knowledge.fill(project, KnowledgeStub({'knowledge.fill': self.structured(fixture_knowledge_models())}))
        self.assertEqual(set(result), {'scenarios.yaml', 'concepts.yaml', 'entities.yaml'})

    def test_second_pass_materializes_ipo_from_each_verified_evidence_quote(self):
        project = self.project()
        result = self.structured(fixture_knowledge_models())
        concept = result['concepts']['concepts'][0]
        expected = concept.pop('ipo')
        concept['ipo_tools'] = expected['process']['tools']
        model = KnowledgeStub({'knowledge.fill': result})
        finished = knowledge.fill(project, model)['concepts.yaml']['concepts'][0]
        self.assertEqual(finished['ipo'], expected)
        self.assertNotIn('ipo_tools', finished)
        self.assertEqual(model.calls, ['knowledge.fill'])
        concept['ipo_evidence']['output'][0]['quote'] = '原文没有这条输出。'
        model = KnowledgeStub({'knowledge.fill': result, 'knowledge.fill.repair': result})
        with self.assertRaisesRegex(ValueError, '必须逐项匹配其来源原文'):
            knowledge.fill(project, model)

    def test_q_and_a_rechecks_published_model_and_raw_versions(self):
        project = self.project()
        project['models']['concepts.yaml']['concepts'][0]['define'] += '篡改'
        stub = KnowledgeStub({})
        with self.assertRaisesRegex(ValueError, '确认'):
            knowledge.ask(project, '怎样评审？', stub)
        self.assertEqual(stub.calls, [])

    def test_locator_cannot_select_an_unpublished_project_document(self):
        project = self.project()
        project['documents'].append({'path': 'raw/未萃取.md', 'text': '未进入任何模型的资料'})
        project['models_confirmation']['documents_sha256'] = knowledge.documents_digest(project)
        stub = KnowledgeStub({'knowledge.locate': {'sources': ['raw/未萃取.md']}})
        with self.assertRaisesRegex(ValueError, '未进入已确认模型'):
            knowledge.ask(project, '问题', stub)
        self.assertEqual(stub.calls, ['knowledge.locate'])

    def test_answer_quotes_must_exist_verbatim_and_keep_source_hashes(self):
        project = self.project()
        responses = {'knowledge.locate': fixture_locator(),
                     'knowledge.answer': {'answer': '逐项检查并记录。[raw/评审.md]',
                         'citations': [{'source': 'raw/评审.md', 'quote': '评审检查是逐项核对材料并记录结论。'}]}}
        result = knowledge.ask(project, '怎样评审？', KnowledgeStub(responses))
        self.assertEqual(result['source_hashes']['raw/评审.md'], fixture_documents()[0]['sha256'])
        self.assertEqual(result['retrieval']['sources_by_ref']['concept://评审检查'], ['raw/评审.md'])
        responses['knowledge.answer']['citations'][0]['quote'] = '伪造原话'
        with self.assertRaisesRegex(ValueError, '无法回读核实'):
            knowledge.ask(project, '怎样评审？', KnowledgeStub(responses))

    def test_uncovered_question_returns_no_fabricated_answer(self):
        stub = KnowledgeStub({'knowledge.locate': {'sources': [], 'outline': []}})
        result = knowledge.ask(self.project(), '未知业务问题', stub)
        self.assertEqual(result['sources'], [])
        self.assertIn('没有覆盖', result['answer'])
        self.assertEqual(stub.calls, ['knowledge.locate'])


if __name__ == '__main__':
    unittest.main()
