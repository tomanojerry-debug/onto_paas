"""The platform's seven model files and a reversible analysis import adapter.

Analysis uses the common model documents with a separate physical source binding.
Imported formulae, SQL templates and steps are descriptive model data.
"""
from __future__ import annotations

from copy import deepcopy


STANDARD = "onto-paas-seven-v1"
_OBJECT, _BEHAVIOR, _RULE, _ACTOR, _FLOW, _REPORT, _UI = (
    "m1-object-model.yaml", "m2-behavior-model.yaml", "m3-rule-model.yaml",
    "m5-actor-model.yaml", "m6-flow-model.yaml", "m7-report-model.yaml", "mu-ui-model.yaml",
)
# Business generation uses this order to pass prior definitions to later models.
MODEL_FILES = {
    _OBJECT: ("OBJECT", "aggregates"),
    _ACTOR: ("ACTOR", "roles"),
    _RULE: ("RULE", "rules"),
    _BEHAVIOR: ("BEHAVIOR", "behaviors"),
    _REPORT: ("REPORT", "query_reports"),
    _FLOW: ("FLOW", "flows"),
    _UI: ("UI", "screens"),
}

_LEGACY_OBJECT, _LEGACY_BEHAVIOR, _LEGACY_RULE, _LEGACY_SCENARIO, _LEGACY_METRIC, _LEGACY_BINDING = (
    "m1_object_model.yaml", "m2_behavior_model.yaml", "m3_rule_model.yaml",
    "m4_scenario_model.yaml", "m_metric_model.yaml", "data_mapping.yaml",
)
_LEGACY_LAYOUT = {
    _LEGACY_OBJECT: ("OBJECT", "entities"),
    _LEGACY_BEHAVIOR: ("BEHAVIOR", "behaviors"),
    _LEGACY_RULE: ("RULE", "rules"),
    _LEGACY_SCENARIO: ("SCENARIO", "scenarios"),
    _LEGACY_METRIC: ("METRIC", "metrics"),
}
_FLOW_FIELDS = {"flowType", "activities", "startActivity", "endActivities"}
_ACTIVITY_FIELDS = {"activityId", "activityType", "nextActivities"}


def is_seven(models: object) -> bool:
    """Identify a bundle by its seven filenames, independently of validation."""
    return isinstance(models, dict) and MODEL_FILES.keys() <= models.keys()


def _binding(value: object) -> dict:
    if not isinstance(value, dict) or not isinstance(value.get("entity_mappings"), list):
        raise ValueError("缺少有效的数据源绑定（entity_mappings 列表）")
    return value


def _rename_list(doc: dict, before: str, after: str) -> dict:
    if after in doc:
        raise ValueError(f"模型同时包含 {before} 与 {after}，无法确认权威定义")
    doc[after] = doc.pop(before)
    return doc


def _import_flow(scenario: dict) -> dict:
    if not isinstance(scenario, dict):
        raise ValueError("M4 场景必须为对象")
    if _FLOW_FIELDS.intersection(scenario):
        raise ValueError("M4 场景已含平台 M6 流程字段，无法无损转换")
    scenario["flowType"] = "ANALYSIS"
    if "primaryFlow" not in scenario:
        return scenario

    steps = scenario.pop("primaryFlow")
    if not isinstance(steps, list):
        raise ValueError("M4 primaryFlow 必须为步骤列表")
    if any(not isinstance(step, dict) or _ACTIVITY_FIELDS.intersection(step) for step in steps):
        raise ValueError("M4 步骤必须为对象，不可同时声明平台 M6 活动字段")
    ids = [step.get("stepId") for step in steps]
    if any(not isinstance(value, str) or not value for value in ids) or len(set(ids)) != len(ids):
        raise ValueError("M4 分析流程步骤缺少有效 stepId 或存在重复 stepId")
    for index, step in enumerate(steps):
        if not isinstance(step.get("type"), str) or not step["type"]:
            raise ValueError(f"M4 步骤 {ids[index]} 缺少类型")
        step["activityId"] = step.pop("stepId")
        step["activityType"] = step.pop("type")
        step["nextActivities"] = ids[index + 1:index + 2]

    scenario.update(activities=steps, startActivity=ids[0] if ids else None, endActivities=ids[-1:])
    return scenario


def _project_flow(flow: dict) -> dict:
    if not isinstance(flow, dict) or flow.get("flowType") != "ANALYSIS" or "primaryFlow" in flow:
        raise ValueError("数据分析项目仅支持统一 M6 ANALYSIS 流程，活动须使用 activities")
    flow.pop("flowType")
    if "activities" not in flow:
        if "startActivity" in flow or "endActivities" in flow:
            raise ValueError("缺少分析流程 activities")
        return flow

    activities = flow.pop("activities")
    if not isinstance(activities, list) or any(not isinstance(item, dict) for item in activities):
        raise ValueError("M6 activities 必须为活动对象列表")
    ids = [step.get("activityId") for step in activities]
    if any(not isinstance(value, str) or not value for value in ids) or len(set(ids)) != len(ids):
        raise ValueError("M6 分析活动缺少有效 activityId 或存在重复 activityId")
    if flow.pop("startActivity", None) != (ids[0] if ids else None) or flow.pop("endActivities", []) != ids[-1:]:
        raise ValueError("M6 分析流程起止活动与当前顺序不一致")
    for index, step in enumerate(activities):
        if {"stepId", "type"}.intersection(step):
            raise ValueError(f"M6 活动 {ids[index]} 同时声明旧版步骤字段")
        if step.pop("nextActivities", None) != ids[index + 1:index + 2]:
            raise ValueError("当前分析流程只支持声明式有序活动，无法投影分支或循环")
        if not isinstance(step.get("activityType"), str) or not step["activityType"]:
            raise ValueError(f"M6 活动 {ids[index]} 缺少类型")
        step["stepId"] = step.pop("activityId")
        step["type"] = step.pop("activityType")

    flow["primaryFlow"] = activities
    return flow


def analysis_to_seven(legacy: dict) -> tuple[dict, dict]:
    """Convert five analysis documents without changing IDs or repairing their meanings."""
    if not isinstance(legacy, dict):
        raise ValueError("待导入的数据分析模型包必须为对象")
    models = deepcopy(legacy)
    expected_files = _LEGACY_LAYOUT.keys() | {_LEGACY_BINDING}
    if models.keys() != expected_files:
        missing = sorted(expected_files - models.keys())
        extra = sorted(str(name) for name in models.keys() - expected_files)
        raise ValueError(f"须提供五模型和一份字段映射；缺少 {missing}；额外 {extra}")
    for filename, (model_type, root) in _LEGACY_LAYOUT.items():
        doc = models[filename]
        if (not isinstance(doc, dict) or doc.get("model_type") != model_type
                or not isinstance(doc.get(root), list)):
            raise ValueError(f"{filename} 必须是 {model_type} 模型并包含 {root} 列表")
    source_bindings = _binding(models[_LEGACY_BINDING])
    objects = models[_LEGACY_OBJECT]
    if "aggregates" in objects:
        raise ValueError("分析 M1 同时声明 entities 和业务 aggregates")
    header = {key: objects[key] for key in ("version", "domain") if key in objects}
    scenarios = _rename_list(models[_LEGACY_SCENARIO], "scenarios", "flows")
    scenarios["model_type"] = "FLOW"
    scenarios["flows"] = [_import_flow(scenario) for scenario in scenarios["flows"]]
    reports = models[_LEGACY_METRIC]
    if "query_reports" in reports:
        raise ValueError("旧指标文件已含查询报表，无法无损转换")
    reports.update(model_type="REPORT", query_reports=[])

    return {
        _OBJECT: objects,
        _ACTOR: dict(header, model_type="ACTOR", roles=[], permissions=[]),
        _RULE: models[_LEGACY_RULE],
        _BEHAVIOR: models[_LEGACY_BEHAVIOR],
        _REPORT: reports,
        _FLOW: scenarios,
        _UI: dict(header, model_type="UI", screens=[]),
    }, source_bindings


def analysis_projection(models: dict, source_bindings: dict | None = None) -> dict:
    """Return the analysis reader's shape, accepting existing legacy projects."""
    if not isinstance(models, dict):
        raise ValueError("数据分析模型包必须为对象")
    data = deepcopy(models)
    if not is_seven(data):
        if source_bindings is not None or MODEL_FILES.keys() & data.keys():
            raise ValueError("统一数据分析模型包缺少七模型文件")
        return data
    if data.keys() != MODEL_FILES.keys():
        raise ValueError("统一数据分析七模型包含额外文件；物理来源绑定应独立提交")
    for filename, (model_type, root) in MODEL_FILES.items():
        doc = data[filename]
        if filename == _OBJECT:
            root = "entities"
        if (not isinstance(doc, dict) or doc.get("model_type") != model_type
                or not isinstance(doc.get(root), list)):
            raise ValueError(f"{filename} 必须是 {model_type} 模型并包含 {root} 列表")

    if data[_ACTOR]["roles"] or data[_ACTOR].get("permissions"):
        raise ValueError("数据分析执行暂不支持模型中的角色与权限定义")
    if data[_UI]["screens"] or data[_UI].get("application"):
        raise ValueError("数据分析执行暂不支持模型中的应用界面定义")
    reports = data[_REPORT]
    if reports.pop("query_reports"):
        raise ValueError("数据分析执行暂不支持固定查询报表定义")
    reports["model_type"] = "METRIC"
    scenarios = _rename_list(data[_FLOW], "flows", "scenarios")
    scenarios["model_type"] = "SCENARIO"
    scenarios["scenarios"] = [_project_flow(flow) for flow in scenarios["scenarios"]]
    objects = data[_OBJECT]
    if "aggregates" in objects:
        raise ValueError("分析 M1 同时声明 entities 和业务 aggregates")

    return {
        _LEGACY_OBJECT: objects, _LEGACY_BEHAVIOR: data[_BEHAVIOR], _LEGACY_RULE: data[_RULE],
        _LEGACY_SCENARIO: scenarios, _LEGACY_METRIC: reports, _LEGACY_BINDING: deepcopy(_binding(source_bindings)),
    }


def analysis_diagnostics(models: dict, source_bindings: dict | None, schema: dict, *, allow_unmapped_fields=False) -> list[str]:
    """Validate the standard analysis profile and its read-only physical binding."""
    from .analysis import validate_models

    try:
        projected = analysis_projection(models, source_bindings)
    except ValueError as exc:
        return [str(exc)]
    return validate_models(projected, schema, allow_unmapped_fields=allow_unmapped_fields)
