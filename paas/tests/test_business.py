import copy
import json
import unittest

from paas.business import _is_full_prd, generate_models, validate_model_baseline, validate_models, validate_requirements
from paas.prototype import generate
from paas.requirements_review import complete_check_evidence
from paas.store import digest
from paas.tests.ui_traceability_fixture import loan_design, loan_models
from paas.tests.requirements_fixture import evidence_prd, loan_requirement_models


def fixture_models():
    """A new equipment-loan domain, intentionally unrelated to upstream samples."""
    header = {"version": "1.0", "domain": "设备借还"}
    behaviors = [
        {"id": "Loan_Create", "name": "保存借用草稿", "ownerEntity": "AGG-LOAN", "behaviorType": "COMMAND", "triggerType": "USER_ACTION", "preconditions": [], "postconditions": [], "appliedRules": ["RULE-QTY"], "requiredPermissions": ["PERM-CREATE"], "syncTriggers": [], "runtime": {"operation": "CREATE", "editableFields": ["deviceName", "quantity"], "stateField": "status"}},
        {"id": "Loan_Submit", "name": "提交借用审批", "ownerEntity": "AGG-LOAN", "behaviorType": "COMMAND", "triggerType": "USER_ACTION", "preconditions": ["record.status == '草稿'"], "postconditions": [], "appliedRules": ["RULE-QTY"], "requiredPermissions": ["PERM-SUBMIT"], "syncTriggers": [], "runtime": {"operation": "TRANSITION", "editableFields": [], "allowedStates": ["草稿"], "stateField": "status", "targetState": "待审批", "submitterField": "submitterId"}},
        {"id": "Loan_Approve", "name": "批准借用", "ownerEntity": "AGG-LOAN", "behaviorType": "COMMAND", "triggerType": "USER_ACTION", "preconditions": ["record.status == '待审批'"], "postconditions": [], "appliedRules": [], "requiredPermissions": ["PERM-APPROVE"], "syncTriggers": [], "runtime": {"operation": "TRANSITION", "editableFields": [], "allowedStates": ["待审批"], "stateField": "status", "targetState": "已批准", "submitterField": "submitterId", "separationOfDuties": True}},
        {"id": "Loan_Query", "name": "借用查询", "ownerEntity": "AGG-LOAN", "behaviorType": "QUERY", "triggerType": "USER_ACTION", "queryReportRef": "REP-LOAN", "preconditions": [], "postconditions": [], "appliedRules": [], "requiredPermissions": ["PERM-QUERY"], "syncTriggers": [], "runtime": {"operation": "QUERY", "editableFields": []}},
    ]
    return {
        "m1-object-model.yaml": {**header, "model_type": "OBJECT", "aggregates": [{"id": "AGG-LOAN", "name": "设备借用", "alias": "Loan", "aggregateType": "AGGREGATE_ROOT", "runtime": {"tableName": "t_equipment_loan", "codePrefix": "LOA"}, "attributes": [
            {"name": "loanId", "label": "借用编号", "type": "String", "required": True, "unique": True, "primaryKey": True},
            {"name": "deviceName", "label": "设备名称", "type": "String", "required": True},
            {"name": "quantity", "label": "数量", "type": "Integer", "required": True},
            {"name": "status", "label": "状态", "type": "Enum", "enumValues": ["草稿", "待审批", "已批准"], "required": True, "defaultValue": "草稿"},
            {"name": "submitterId", "label": "提交人", "type": "String", "required": False},
        ]}]},
        "m2-behavior-model.yaml": {**header, "model_type": "BEHAVIOR", "behaviors": behaviors},
        "m3-rule-model.yaml": {**header, "model_type": "RULE", "rules": [{"id": "RULE-QTY", "name": "借用数量大于零", "ruleType": "VALIDATION", "inputParams": [{"name": "quantity", "sourceField": "record.quantity"}], "expression": "quantity > 0", "violationMessage": "借用数量必须大于零"}]},
        "m5-actor-model.yaml": {**header, "model_type": "ACTOR", "actors": [], "roles": [{"roleId": "ROLE-REQUESTER", "name": "申请人", "permissions": ["PERM-CREATE", "PERM-SUBMIT", "PERM-QUERY"]}, {"roleId": "ROLE-MANAGER", "name": "设备主管", "permissions": ["PERM-APPROVE", "PERM-QUERY"]}], "permissions": [{"permissionId": p, "targetType": "BEHAVIOR", "targetRef": b, "dataScope": "ALL"} for p, b in [("PERM-CREATE", "Loan_Create"), ("PERM-SUBMIT", "Loan_Submit"), ("PERM-APPROVE", "Loan_Approve"), ("PERM-QUERY", "Loan_Query")]]},
        "m6-flow-model.yaml": {**header, "model_type": "FLOW", "flows": [{"id": "FLOW-LOAN", "name": "借用审批", "flowType": "APPROVAL", "businessObjectRefs": ["AGG-LOAN"], "runtime": {"startBehaviorRef": "Loan_Submit", "stateField": "status"}, "startActivity": "START", "endActivities": ["END"], "activities": [
            {"activityId": "START", "name": "开始", "activityType": "START", "nextActivities": ["SUBMIT"]},
            {"activityId": "SUBMIT", "name": "提交借用", "activityType": "USER_TASK", "roleRef": "ROLE-REQUESTER", "behaviorRef": "Loan_Submit", "nextActivities": ["APPROVE"]},
            {"activityId": "APPROVE", "name": "主管审批", "activityType": "USER_TASK", "roleRef": "ROLE-MANAGER", "behaviorRef": "Loan_Approve", "nextActivities": ["END"]},
            {"activityId": "END", "name": "结束", "activityType": "END", "nextActivities": []},
        ]}]},
        "m7-report-model.yaml": {**header, "model_type": "REPORT", "query_reports": [{"id": "REP-LOAN", "name": "借用查询", "behaviorRef": "Loan_Query", "sourceObjects": [{"objectRef": "AGG-LOAN", "alias": "loan", "primary": True}], "joins": [], "parameters": [{"name": "status", "label": "状态", "required": False}], "conditions": [{"leftExpression": "loan.status", "operator": "EQ", "parameterRef": "status", "skipWhenParameterEmpty": True}], "resultColumns": [{"name": "deviceName", "label": "设备名称", "sourceExpression": "loan.deviceName", "aggregateFunction": "NONE"}, {"name": "quantity", "label": "数量", "sourceExpression": "loan.quantity", "aggregateFunction": "NONE"}, {"name": "status", "label": "状态", "sourceExpression": "loan.status", "aggregateFunction": "NONE"}], "sortBy": [{"field": "deviceName", "direction": "ASC"}]}]},
        "mu-ui-model.yaml": {**header, "model_type": "UI", "application": {"name": "设备借还", "menus": [{"menuId": "menu-loan", "name": "设备借用", "screenRef": "screen-loan"}]}, "screens": [{"screenId": "screen-loan", "name": "设备借用", "screenType": "SINGLE_TABLE_FORM", "objectRef": "AGG-LOAN", "layout": "设备名称 数量 [保存草稿] [提交]", "elements": [], "actionPoints": [{"id": "action-"+b["id"], "name": b["name"], "behaviorRef": b["id"]} for b in behaviors]}]},
    }


class BusinessValidationTests(unittest.TestCase):
    def test_placeholder_rows_reject_empty_appendix_even_when_all_reported_pass(self):
        empty = "# 测试需求\n## 附录 B 待澄清问题\n无待确认问题\n## 附录 C 七模型建模输入基线\n"
        empty += "## 需求完整性自检\n" + "\n".join(f"| {i} | 占位 | 通过 | 占位依据 |" for i in range(1, 48))
        self.assertFalse(validate_requirements(empty)["passed"])
        self.assertFalse(_is_full_prd(empty))

    def test_v9_checks_bind_to_real_numbered_evidence_and_keep_user_approval(self):
        document = evidence_prd()
        audit = validate_requirements(document)
        self.assertTrue(audit["passed"], audit["errors"])
        self.assertTrue(audit["human_approval_required"])
        self.assertTrue(all(check["evidence"] for check in audit["checks"]))
        self.assertEqual(audit["baseline"]["report_to_query"], {"REP-01": "Q-01"})
        self.assertFalse(validate_requirements(document + "\n审批人[待确认]")["passed"])
        declared_fail = document.replace("| 通过 |", "| 未通过 |", 1)
        self.assertFalse(_is_full_prd(declared_fail))

    def test_not_applicable_check_needs_confirmed_evidence(self):
        document = evidence_prd().replace("用户确认不存在跨对象联动", "初步认为不存在跨对象联动")
        audit = validate_requirements(document)
        self.assertFalse(audit["passed"])
        self.assertIn("实际经用户确认", " ".join(audit["checks"][26]["diagnostics"]))

    def test_nested_function_cards_and_category_labels_keep_v9_evidence_valid(self):
        document = evidence_prd().replace(
            "## 2.2 功能详述\n\n", "## 2.2 功能详述\n\n### FUNC-01 保存草稿\n\n"
        ).replace("| B-APP-01 批准 | APPROVAL |", "| B-APP-01 批准 | B-APP |")
        audit = validate_requirements(document)
        self.assertTrue(audit["passed"], audit["errors"])
        self.assertEqual(audit["checks"][11]["evidence"][0]["section"], "2.2")
        self.assertEqual(audit["baseline"]["entries"]["B-APP-01"]["references"], ["OBJ-01", "ROLE-02"])

    def test_missing_approval_diagram_and_false_report_binding_are_rejected(self):
        document = evidence_prd().replace("flowchart TD", "flowchart INVALID")
        audit = validate_requirements(document)
        self.assertFalse(audit["passed"])
        self.assertIn("独立", " ".join(audit["checks"][30]["diagnostics"]))
        wrong_steps = evidence_prd().replace("| 3 | Q-01 查询 |", "| 3 | FUNC-02 提交 |")
        self.assertIn("步骤表", " ".join(validate_requirements(wrong_steps)["checks"][6]["diagnostics"]))
        missing = evidence_prd().replace("使用 REP-01 |", "只查询本人 |")
        self.assertTrue(any("反向引用" in error for error in validate_requirements(missing)["errors"]))

    def test_approval_diagram_context_and_index_annotations_preserve_structure(self):
        document = evidence_prd().replace("### 4.3.1 PROC-APP-01 主管审批", "### 4.3.1 主管审批")
        document = document.replace("| 协同流 | 1.4.1 |", "| 协同流 | 1.4.1（图内）；4.2 引用 |")
        audit = validate_requirements(document)
        self.assertTrue(audit["structure_passed"], audit["errors"])
        no_index = document.replace("1.4.1（图内）；4.2 引用", "4.2（参考）")
        self.assertFalse(validate_requirements(no_index)["structure_passed"])
        no_matching_approval = document.replace("PROC-APP-01 从提交开始", "审批由记录提交开始")
        self.assertFalse(validate_requirements(no_matching_approval)["structure_passed"])
        only_bad_audit = document.replace("| 通过 |", "| 未通过 |", 1)
        self.assertTrue(validate_requirements(only_bad_audit)["structure_passed"])
        self.assertFalse(validate_requirements(only_bad_audit)["passed"])

    def test_nested_flow_inherits_parent_identity_and_closed_appendix_is_not_a_question(self):
        document = evidence_prd().replace("```mermaid\nflowchart LR", "#### Mermaid 流程图\n\n```mermaid\nflowchart LR", 1)
        document = document.replace(
            "无遗留问题，已完成业务范围确认。",
            "无遗留问题，已完成业务范围确认。附录 B 无 `[待确认]` 条目。",
            1,
        )
        audit = validate_requirements(document)
        self.assertTrue(audit["passed"], audit["errors"])
        changed = document.replace("1.4.1 PROC-01 借用登记与审核", "1.4.1 借用登记与审核")
        self.assertFalse(validate_requirements(changed)["structure_passed"])
        actual_open_question = document.replace("附录 B 无", "审批人归属尚需核对")
        self.assertFalse(validate_requirements(actual_open_question)["structure_passed"])
        self.assertFalse(validate_requirements(document + "\n交接方式[待确认]")["structure_passed"])

    def test_model_baseline_rejects_missing_and_misclassified_coverage(self):
        document = evidence_prd()
        models = loan_models(loan_requirement_models(fixture_models()))
        self.assertEqual([], validate_model_baseline(models, document))
        models["m2-behavior-model.yaml"]["behaviors"][0]["requirementRefs"] = ["REP-01"]
        diagnostics = validate_model_baseline(models, document)
        self.assertTrue(any("归属不符" in error for error in diagnostics))
        self.assertTrue(any("FUNC-01 未落实" in error for error in diagnostics))

    def test_review_can_ground_citations_by_id_but_cannot_rewrite_structure(self):
        document = evidence_prd()
        original = validate_requirements(document)['checks'][0]['reason']
        missing = document.replace(original, '§1.1「并不存在的说明」', 1)

        class Review:
            def json(self, system, user, *, purpose, max_tokens):
                self.calls = getattr(self, 'calls', 0) + 1
                data = json.loads(user)
                self.assertion = (purpose, len(data['checks']))
                check = data['checks'][0]
                return {'items': [{'number': check['number'], 'status': '通过',
                                   'evidence_id': check['candidate_refs'][0][0]}]}

        model = Review()
        reviewed = complete_check_evidence(missing, validate_requirements(missing), model, max_tokens=1024)
        self.assertEqual(model.assertion, ('business.requirements.evidence-review', 1))
        self.assertTrue(validate_requirements(reviewed)['passed'])
        self.assertNotIn('并不存在的说明', reviewed)
        broken = missing.replace('flowchart TD', 'flowchart INVALID')
        self.assertEqual(complete_check_evidence(broken, validate_requirements(broken), model, max_tokens=1024), broken)
        self.assertEqual(model.calls, 1)

    def test_generation_receives_verified_baseline_and_keeps_ui_evidence_in_bundle(self):
        models = loan_models(loan_requirement_models(fixture_models()))
        observed = {}

        class FixtureLLM:
            def json(self, system, user, *, purpose, **kwargs):
                if purpose == "interface-prototype.generate":
                    return loan_design()
                if purpose == "business.model.ui.repair.1":
                    observed["MU_REPAIR"] = json.loads(user)
                    return copy.deepcopy(models["mu-ui-model.yaml"])
                model_type = purpose.rsplit(".", 1)[-1].upper()
                observed[model_type] = json.JSONDecoder().raw_decode(user)[0]["validated_modeling_baseline"]
                result = copy.deepcopy(next(model for model in models.values() if model["model_type"] == model_type))
                if model_type == "UI":
                    result['screens'][0]['elements'].append({'id': 'unmapped-button', 'type': 'BUTTON'})
                return result

        project = {"kind": "business", "id": "fixture-loan", "name": "设备借用", "requirements": evidence_prd()}
        llm = FixtureLLM()
        project["requirements_confirmation"] = {"sha256": digest(project["requirements"])}
        project["prototype"] = generate(project, llm)
        project["prototype_confirmation"] = {
            key: project["prototype"][key] for key in ("sha256", "requirements_sha256")
        }
        result = generate_models(project, llm)
        self.assertEqual(len(observed), 8)
        self.assertIn('unmapped-button', observed['MU_REPAIR']['errors'][0])
        self.assertEqual(result['m2-behavior-model.yaml'], models['m2-behavior-model.yaml'])
        self.assertEqual(observed["REPORT"]["report_to_query"], {"REP-01": "Q-01"})
        self.assertEqual(result["manifest.json"]["requirements_baseline_standard"], "v9-appendix-c-v1")
        self.assertEqual(result["mu-ui-model.yaml"]["application"]["prototypeDesign"]["pages"],
                         project["prototype"]["pages"])

    def test_checklist_and_usecase_require_real_artifacts(self):
        document = evidence_prd().replace("  Manager --> Query\n", "")
        result = validate_requirements(document)
        self.assertFalse(result["passed"])
        self.assertIn("实际连线", " ".join(result["checks"][10]["diagnostics"]))
        document = evidence_prd().replace("| 序号 | 自检项", "```text\n| 序号 | 自检项")
        document = document.replace("# 修订记录", "```\n\n# 修订记录")
        self.assertEqual(validate_requirements(document)["checks"][0]["status"], "缺失")

    def test_seven_models_have_complete_references(self):
        self.assertEqual([], validate_models(fixture_models()))

    def test_query_back_reference_is_required(self):
        models = fixture_models()
        models["m2-behavior-model.yaml"]["behaviors"][-1]["queryReportRef"] = "missing"
        self.assertTrue(any("一对一" in error for error in validate_models(models)))

    def test_user_action_needs_ui_reference(self):
        models = fixture_models()
        models["mu-ui-model.yaml"]["screens"][0]["actionPoints"].pop()
        self.assertTrue(any("无MU操作点" in error for error in validate_models(models)))

    def test_missing_permission_is_rejected(self):
        models = fixture_models()
        models["m5-actor-model.yaml"]["roles"][0]["permissions"].append("fake")
        self.assertTrue(any("不存在权限" in error for error in validate_models(models)))

    def test_subflow_recursion_is_rejected(self):
        models = fixture_models()
        node = models["m6-flow-model.yaml"]["flows"][0]["activities"][1]
        node.update(activityType="SUB_FLOW_CALL", subFlowRef="FLOW-LOAN")
        self.assertIn("M6子流程调用图成环", validate_models(models))


if __name__ == "__main__":
    unittest.main()
