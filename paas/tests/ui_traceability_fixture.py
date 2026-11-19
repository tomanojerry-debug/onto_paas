"""A fictional approval model with explicit links to its confirmed prototype."""
from __future__ import annotations

from paas.ui_contract import UI_TRACEABILITY_VERSION


def loan_design():
    group = "设备借用"
    return {"summary": "设备借用登记、审批和查询", "pages": [
        {"id": "loans", "name": "借用申请", "group": group, "purpose": "查看和处理有权访问的借用申请",
         "layout": "list", "fields": [],
         "columns": [{"id": "deviceName", "label": "设备名称"}, {"id": "status", "label": "状态"}],
         "actions": [
             {"id": "new", "label": "新建申请", "kind": "navigate", "target_page": "apply"},
             {"id": "submit", "label": "提交借用审批", "kind": "submit"},
             {"id": "approve", "label": "批准借用", "kind": "edit"},
         ]},
        {"id": "apply", "name": "登记申请", "group": group, "purpose": "填写设备和数量",
         "layout": "form", "fields": [
             {"id": "deviceName", "label": "设备名称", "type": "text", "required": True},
             {"id": "quantity", "label": "借用数量", "type": "number", "required": True},
         ], "columns": [],
         "actions": [
             {"id": "save", "label": "保存借用草稿", "kind": "draft"},
             {"id": "back", "label": "返回列表", "kind": "navigate", "target_page": "loans"},
         ]},
        {"id": "report", "name": "借用查询", "group": group, "purpose": "按状态查询现有借用",
         "layout": "query_list", "record_source": "", "fields": [
             {"id": "status", "label": "状态", "type": "select", "options": ["草稿", "待审批", "已批准"]},
         ], "columns": [
             {"id": "deviceName", "label": "设备名称"},
             {"id": "quantity", "label": "数量"},
             {"id": "status", "label": "状态"},
         ], "actions": [{"id": "lookup", "label": "查询借用", "kind": "query"}]},
    ]}


def loan_models(models=None):
    if models is None:
        from paas.tests.test_business import fixture_models

        models = fixture_models()
    ui = models["mu-ui-model.yaml"]
    ui["application"] = {
        "name": "设备借还", "prototypeContract": UI_TRACEABILITY_VERSION,
        "menus": [{"menuId": "loan-group", "name": "设备借用", "children": [
            {"menuId": f"menu-{page['id']}", "name": page["name"], "screenRef": f"screen-{page['id']}"}
            for page in loan_design()["pages"]]}],
    }
    actions = {
        ("loans", "submit"): "Loan_Submit", ("loans", "approve"): "Loan_Approve",
        ("apply", "save"): "Loan_Create", ("report", "lookup"): "Loan_Query",
    }
    screens = []
    for index, page in enumerate(loan_design()["pages"], 1):
        page_id = page["id"]
        report = page_id == "report"
        fields = [{
            "id": "input-" + field["id"], "label": field["label"], "prototypeFieldRef": field["id"],
            "type": {"text": "TEXTBOX", "number": "NUMBER", "select": "COMBO"}[field["type"]],
            "required": field.get("required", False),
            **({"parameterRef": field["id"]} if report else {"dataBinding": "Loan." + field["id"]}),
        } for field in page["fields"]]
        columns = [{
            "id": "column-" + field["id"], "type": "LABEL", "label": field["label"], "prototypeColumnRef": field["id"],
            **({"resultColumnRef": field["id"]} if report else {"dataBinding": "Loan." + field["id"]}),
        } for field in page["columns"]]
        points = []
        for action in page["actions"]:
            point = {"actionId": "action-" + action["id"], "name": action["label"], "prototypeActionRef": action["id"]}
            if action.get("target_page"):
                point["targetScreenRef"] = "screen-" + action["target_page"]
            else:
                point["behaviorRef"] = actions[(page_id, action["id"])]
            points.append(point)
        diagram = [" ".join(f"[{item['id']}]" for item in fields + columns),
                   " ".join(f"[{item['actionId']}]" for item in points)]
        screens.append({
            "screenId": f"screen-{page_id}", "name": page["name"], "prototypePageRef": page_id,
            "screenType": {"list": "LIST_MAINTENANCE", "form": "SINGLE_FORM", "query_list": "QUERY_LIST"}[page["layout"]],
            "requirementRefs": [f"UI-{index:02}"], "objectRef": "AGG-LOAN", "layout": "\n".join(diagram),
            **({"queryReportRef": "REP-LOAN"} if report else {}),
            "elements": fields + columns, "actionPoints": points,
        })
    ui["screens"] = screens
    models["m7-report-model.yaml"]["query_reports"][0]["pagination"] = {"enabled": True}
    return models
