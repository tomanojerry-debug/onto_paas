"""Render the author's offline UI call-chain workbench with the selected models."""
from __future__ import annotations

import base64
import copy
import hashlib
import html
import json
from pathlib import Path


REFERENCE = Path(__file__).with_name("upstream") / "ontology-driven-dev/reference-example/界面调用链"
WEB = Path(__file__).with_name("web")
TEMPLATE = REFERENCE / "contract-management-ui-callchain_v9.html"
TEMPLATE_SHA256 = "902a77a1e0aa2898d9db8c220b629a3ac004e2c4d59042c0633d9503b3f61fd6"
MODEL_FILES = (
    "m1-object-model.yaml", "m2-behavior-model.yaml", "m3-rule-model.yaml", "m5-actor-model.yaml",
    "m6-flow-model.yaml", "m7-report-model.yaml", "mu-ui-model.yaml",
)


def _rows(model, key, *aliases):
    for field in (key, *aliases):
        if field in model:
            value = model[field]
            if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
                raise ValueError(f"模型调用链需要对象列表 {field}")
            return value
    return []


def callchain_data(models):
    missing = [file for file in MODEL_FILES if not isinstance(models.get(file), dict)]
    if missing:
        raise ValueError("模型调用链需要七份模型，缺少：" + "、".join(missing))
    objects, behaviors, rules, actors, flows, reports, ui = (
        copy.deepcopy(models[filename]) for filename in MODEL_FILES
    )
    application = ui.get("application") or {}
    if not isinstance(application, dict):
        raise ValueError("MU application 应为对象")
    manifest = models.get("manifest.json") or {}
    if not isinstance(manifest, dict):
        raise ValueError("模型清单应为对象")
    screens = _rows(ui, "screens")
    for screen in screens:
        screen["actions"] = _rows(screen, "actionPoints", "actions")
        for action in screen["actions"]:
            action.setdefault("actionId", action.get("id"))
    behavior_list = _rows(behaviors, "behaviors")
    used_rules = {}
    for item in behavior_list:
        item["appliedRules"] = [value.get("ruleRef") if isinstance(value, dict) else value
                                for value in item.get("appliedRules", [])]
        for rule_id in item["appliedRules"]:
            if isinstance(rule_id, str):
                used_rules.setdefault(rule_id, []).append(item.get("id"))
    all_rules = []
    for collection, family in (("rules", "RULE"), ("ref_rules", "REF"), ("inv_rules", "INV")):
        for rule in _rows(rules, collection):
            rule["ruleFamily"] = family
            rule["reusedBy"] = [behavior_id for behavior_id in used_rules.get(rule.get("id"), []) if behavior_id]
            all_rules.append(rule)
    return {
        "meta": {
            "domain": str(objects.get("domain") or manifest.get("name") or "本体模型"),
            "application": str(application.get("name") or manifest.get("name") or "本体调用链工作台"),
            "modelFiles": list(MODEL_FILES), "missingFiles": [], "promptVersion": "V9",
        },
        "screens": screens, "menus": _rows(application, "menus"),
        "aggregates": _rows(objects, "aggregates", "entities"),
        "dictionaries": _rows(objects, "data_dictionaries", "dataDictionaries", "dictionaries"),
        "associations": _rows(objects, "aggregate_associations", "associations"),
        "behaviors": behavior_list, "rules": all_rules, "actors": _rows(actors, "actors"),
        "roles": _rows(actors, "roles"), "permissions": _rows(actors, "permissions"),
        "flows": _rows(flows, "flows"), "reports": _rows(reports, "query_reports"),
    }


def render_callchain(models):
    """Replace the author's sample metadata and embedded data with this bundle."""
    data = callchain_data(models)
    template = TEMPLATE.read_bytes()
    if hashlib.sha256(template).hexdigest() != TEMPLATE_SHA256:
        raise ValueError("调用链工作台参考模板完整性校验失败")
    prefix, _, original_script = template.decode("utf-8").partition("<script>const DATA=")
    _, separator, suffix = original_script.partition("\nconst $=s=>")
    if not separator:
        raise ValueError("调用链工作台参考模板缺少脚本边界")
    body, _, tail = suffix.rpartition("</script>")
    if not tail:
        raise ValueError("调用链工作台参考模板缺少结束标签")
    name = html.escape(data["meta"]["application"])
    before_style, _, original_style = prefix.partition("<style>")
    _, _, after_style = original_style.partition("</style>")
    styles = (WEB / "ui-system.css").read_text(encoding="utf-8")
    styles += "\n" + (WEB / "callchain.css").read_text(encoding="utf-8")
    prefix = (before_style + "<style>\n" + styles + "\n</style>" + after_style)
    prefix = prefix.replace("销售合同执行管理系统 · V9 · 本体调用链工作台", f"模型调用链 · {name}")
    prefix = prefix.replace(
        '<header class="top"><div class="brand"><h1>销售合同执行管理系统 · V9</h1>'
        '<small>本体模型 · UI 调用链追踪工作台</small></div>',
        '<header class="top ui-topbar"><div class="brand ui-brand">'
        '<span class="ui-brand-mark" aria-hidden="true">O</span><h1>模型调用链</h1></div>',
    ).replace('<aside class="tree-pane">', '<aside class="tree-pane ui-navigation" aria-label="模型导航">')
    body = body.replace('<span class="badge">MODEL WORKBENCH</span>', '').replace(
        '从当前 7 份本体 YAML 构建的可交互知识导航。选择左侧节点，沿真实引用穿透业务设计。',
        '选择一个模型节点，查看定义与关联。',
    ).replace(
        '<div class="notice" style="margin-top:25px">本工作台以 manifest 声明的七模型 YAML 为唯一业务事实源。</div>',
        '',
    )
    body = body.replace('现代渲染', '界面示意').replace('原始 ASCII', '布局定义').replace(
        '<span class="spacer"></span><button class="btn" data-size="-1">A−</button>'
        '<button class="btn" data-size="1">A+</button>', '',
    ).replace('<i class="dot"></i>' * 3, '').replace('<span class="ui-chip">当前用户</span>', '')
    body = body.replace('<button class="ui-button ${', '<span class="ui-button schematic ${').replace(
        "${esc(e.label||e.id)}</button>`", "${esc(e.label||e.id)}</span>`",
    )
    payload = json.dumps(data, ensure_ascii=False, allow_nan=False).replace("<", r"\u003c")
    script = "const DATA=" + payload + ";\nconst $=s=>" + body
    script_hash = base64.b64encode(hashlib.sha256(script.encode()).digest()).decode()
    csp = (
        "default-src 'none'; img-src data:; style-src 'unsafe-inline'; "
        f"script-src 'sha256-{script_hash}'; connect-src 'none'; base-uri 'none'; "
        "form-action 'none'; sandbox allow-scripts"
    )
    return prefix + "<script>" + script + "</script>" + tail, csp
