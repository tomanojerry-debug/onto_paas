"""Compile confirmed prototype-to-MU mappings to the supported application UI."""
from __future__ import annotations

import re


UI_TRACEABILITY_VERSION = "page-evidence-v1"
UI_ELEMENT_MAPPING_PROMPT = (
    "\nMU 输出最终校验：每个 screens[].elements 是已确认页面字段和结果列的一维清单。"
    "每项必须且只能引用一个原型字段 prototypeFieldRef 或一个原型结果列 prototypeColumnRef。"
    "为每个原型字段、每个结果列分别生成一项。元素不得使用 BUTTON、GRID、SECTION 或任何容器，"
    "不另建表格包装元素、标题、分组，也不添加占位元素。已确认的按钮只出现在 actionPoints，逐一映射已有 prototypeActionRef。"
    "默认省略额外 permissionRef 或设为空数组，由 M2 和 M5 决定角色。仅在需求明确另设界面权限时填写可同时满足的额外权限；"
    "同一查询的 OWN 与 ALL 是不同数据范围的授权，不能在 permissionRef 中同时要求或只留下其一从而隐藏另一角色。"
    "示例中的默认退出或其他按钮只有实际原型明确提供时才能出现。"
    "每个屏幕的 ASCII layout 必须原样包含全部 elements[].id 以及 actionPoints[].actionId（若使用 id 则是该 id）；"
    "例如 actionPoints[].id 是 actSubmit 时，布局中写 [actSubmit]，不要另外造 btnSubmit 或只画按钮文字。"
)
UI_TRACEABILITY_PROMPT = """
平台 MU 界面追溯契约（有已确认原型的业务应用必须采用）：
- application.prototypeContract 必须是 page-evidence-v1。application.menus 分为两级，一级 name 取原型 group（未分组时为「业务页面」），二级 name 取对应原型页 name。每个可导航页在菜单中出现一次并保持组与组内顺序，每个弹窗页只能由 open_form 打开，不直接进菜单。
- screens 必须为每个已确认 pages[].id 建立唯一 screen，保留 prototypePageRef、name、screenType 和 ASCII layout。form/single_form -> SINGLE_FORM，list/list_maintenance -> LIST_MAINTENANCE，query_list -> QUERY_LIST，detail -> DETAIL，master_detail_form -> MASTER_DETAIL_FORM。弹窗写 presentation: dialog。单表、列表、详情写 objectRef，查询写 queryReportRef，必要时同时写同一主对象 objectRef。
- 每个原型字段在 elements 以 prototypeFieldRef 引用；每个原型结果列以 prototypeColumnRef 引用，id 均在屏内唯一。普通页面以 dataBinding: 聚合别名.属性名 绑定 M1；查询过滤项写 parameterRef，查询结果列写 resultColumnRef（均取 M7 name）。保留原型 label、required 和控件类型 TEXTBOX/TEXTAREA/NUMBER/DATEPICKER/CHECKBOX/COMBO/POPUP_SELECT；结果列为 LABEL。所有标识在 ASCII layout 中出现。不补造额外字段、结果列和动作。
- actionPoints 逐一以 prototypeActionRef 引用原型动作，保留按钮 name。普通业务按钮写 behaviorRef，查询按钮绑定该报表唯一的 M2 QUERY。草稿和流程提交须分别绑定对应 M2，流程提交必须是 M6 入口。审批决定以行为绑定，不能映射为查询、取消或另一个对象的行为。navigate/open_form 只写 targetScreenRef；cancel/reset 为界面动作不写行为。所有 M2 USER_ACTION 行为须能由界面调用，确认原型漏动作时反馈补充原型，不虚构按钮。
- 同一页写入动作的 runtime.editableFields 必须可由本页字段逐一输入。列表/查询与弹窗之间有明确关联时，M1 对象、M7 主来源与弹窗对象须保持一致。M1 的引用目标和原型的 reference_page 一致。文案与 ID 保持已确认版本，不用参考样例的业务。
""" + UI_ELEMENT_MAPPING_PROMPT


def portable_design(models):
    from .prototype import validate_design

    application = models.get("mu-ui-model.yaml", {}).get("application", {})
    _need(isinstance(application, dict) and application.get("prototypeContract") == UI_TRACEABILITY_VERSION,
          "导入的 MU 缺少界面追溯契约")
    _need(isinstance(application.get("prototypeDesign"), dict), "导入的 MU 缺少界面设计基线")
    return validate_design(application["prototypeDesign"])


def bind_portable_design(models, prototype):
    from .prototype import validate_design

    application = models.get("mu-ui-model.yaml", {}).get("application")
    if prototype and isinstance(application, dict) and application.get("prototypeContract") == UI_TRACEABILITY_VERSION:
        design = {key: prototype[key] for key in ("summary", "pages", "title", "assistant") if key in prototype}
        application["prototypeDesign"] = validate_design(design)

_LAYOUTS = {
    "form": "SINGLE_FORM",
    "single_form": "SINGLE_FORM",
    "list": "LIST_MAINTENANCE",
    "list_maintenance": "LIST_MAINTENANCE",
    "query_list": "QUERY_LIST",
    "detail": "DETAIL",
    "master_detail_form": "MASTER_DETAIL_FORM",
}
_CONTROLS = {
    "text": "TEXTBOX", "textarea": "TEXTAREA", "number": "NUMBER", "date": "DATEPICKER",
    "checkbox": "CHECKBOX", "select": "COMBO", "reference": "POPUP_SELECT",
}
_TYPED = {
    "int": "number", "integer": "number", "float": "number", "double": "number",
    "decimal": "number", "money": "number", "number": "number", "boolean": "checkbox",
    "bool": "checkbox", "date": "date", "datetime": "date", "timestamp": "date",
    "enum": "select", "dictionaryref": "select", "aggregaterootref": "reference",
}
_MUTATIONS = {
    "create": {"CREATE"}, "save": {"CREATE", "UPDATE"}, "draft": {"CREATE", "UPDATE"},
    "submit": {"CREATE", "UPDATE", "TRANSITION"}, "edit": {"UPDATE", "TRANSITION"},
    "delete": {"UPDATE", "TRANSITION"},
}


def _need(condition, message):
    if not condition:
        raise ValueError("MU 界面追溯：" + message)


def _index(items, key, label, *, fallback=None):
    _need(isinstance(items, list), f"{label}应为列表")
    result = {}
    for item in items:
        _need(isinstance(item, dict), f"{label}须使用对象")
        value = item.get(key) or (item.get(fallback) if fallback else None)
        _need(isinstance(value, str) and value and value not in result, f"{label}的 {key} 缺失或重复")
        result[value] = item
    return result


def _target(attribute):
    return (attribute.get("targetAggregate") or attribute.get("referenceEntity")
            or (attribute.get("aggregateRootRef") or {}).get("aggregateId"))


def _attribute(element, entity):
    binding = element.get("dataBinding")
    owner, _, name = (binding if isinstance(binding, str) else "").rpartition(".")
    _need(owner and owner in {entity.get("id"), entity.get("alias"), str(entity.get("alias", "")).lower(),
                                str(entity.get("alias", ""))[:1].lower() + str(entity.get("alias", ""))[1:]},
          f"元素 {element['id']} 的 dataBinding 必须指向本页 M1 对象")
    attributes = {field["name"]: field for field in entity.get("attributes", [])}
    _need(name in attributes, f"元素 {element['id']} 不能绑定未知字段 {binding}")
    return attributes[name]


def _report_attribute(report, parameter, entities):
    condition = next((item for item in report.get("conditions", []) if item.get("parameterRef") == parameter["name"]), None)
    if not condition:
        return {}
    alias, _, name = condition["leftExpression"].rpartition(".")
    source = next((item for item in report.get("sourceObjects", []) if item.get("alias") == alias), None)
    attributes = entities.get(source["objectRef"], {}).get("attributes", []) if source else []
    return next((field for field in attributes if field["name"] == name), {})


def _field(record, element, screen, entity, report, entities, screens_by_page):
    key = record["id"]
    _need(element.get("label") == record["label"], f"{screen['screenId']}.{key} 的字段文案与确认原型不同")
    field_type = record["type"]
    _need(element.get("type") == _CONTROLS[field_type], f"{screen['screenId']}.{key} 的控件类型与确认原型不同")
    if report:
        parameter = next((item for item in report.get("parameters", []) if item["name"] == element.get("parameterRef")), None)
        _need(parameter is not None, f"{screen['screenId']}.{key} 缺有效 M7 parameterRef")
        attribute = _report_attribute(report, parameter, entities)
        field_name = parameter["name"]
        typed = parameter.get("type") or attribute.get("type")
        required = parameter.get("required", False)
        default = parameter.get("defaultValue", "")
    else:
        attribute = _attribute(element, entity)
        field_name = attribute["name"]
        typed = attribute.get("type")
        required = attribute.get("required", False)
        default = attribute.get("defaultValue", attribute.get("default", ""))
    expected = "reference" if _target(attribute) else _TYPED.get(str(typed or "").lower())
    _need((expected == field_type) if expected else field_type in {"text", "textarea"},
          f"{screen['screenId']}.{key} 的控件与绑定的 M1/M7 类型不符")
    _need(required == record.get("required", False) and element.get("required", False) == required,
          f"{screen['screenId']}.{key} 的必填约束与 M1/M7 或原型不符")
    options = attribute.get("options") or attribute.get("enumValues") or []
    if field_type == "select":
        labels = [option["label"] if isinstance(option, dict) else option for option in options]
        _need(labels and set(labels) == set(record.get("options", [])),
              f"{screen['screenId']}.{key} 的选项与确认原型或 M1 字典不符")
    field = {"id": key, "element_id": element["id"], "name": field_name, "label": record["label"],
             "control": field_type, "required": required, "default": default,
             "wide": record.get("wide", False), "options": options,
             "help": list(dict.fromkeys(value.strip() for value in (
                 record.get("help"), element.get("help"), attribute.get("help"), attribute.get("description"),
                 *(rule.get("violationMessage") for rule in attribute.get("refRules", [])),
             ) if isinstance(value, str) and value.strip()))}
    if field_type == "reference":
        candidate = screens_by_page.get(record.get("reference_page"), {})
        target = entities.get(_target(attribute))
        _need(target and (not candidate or candidate.get("objectRef") == target["id"]),
              f"{screen['screenId']}.{key} 的跳选对象与 M1 引用目标不符")
        _need(not record.get("reference_scope_field"),
              f"{screen['screenId']}.{key} 的级联跳选需要专门运行时支持")
        label = (record.get("reference_field") or next(
            (item["name"] for item in target["attributes"] if re.search(r"name|title|label", item["name"], re.I)), "code"))
        _need(label == "code" or any(item["name"] == label for item in target["attributes"]),
              f"{screen['screenId']}.{key} 的跳选显示字段与 M1 不符")
        field["reference"] = {"entity_id": target["id"], "value": attribute.get("referenceField", "id"), "label": label}
    return field


def _columns(page, screen, entity, report, by_column):
    columns = []
    for column in page.get("columns", []):
        element = by_column[column["id"]]
        _need(element.get("label") == column["label"] and element.get("type") == "LABEL",
              f"{screen['screenId']}.{column['id']} 的结果列标题或控件与确认原型不同")
        if report:
            name = element.get("resultColumnRef")
            _need(name in {item["name"] for item in report.get("resultColumns", [])},
                  f"{screen['screenId']}.{column['id']} 缺有效 M7 resultColumnRef")
        else:
            name = _attribute(element, entity)["name"]
        columns.append({"id": column["id"], "element_id": element["id"], "name": name, "label": column["label"]})
    if report:
        _need({item["name"] for item in columns} == {item["name"] for item in report["resultColumns"]}
              and len(columns) == len(report["resultColumns"]), f"{screen['screenId']} 的结果列须与 M7 一一对应")
    return columns


def _check_action_permissions(grants, behavior, roles, permissions, is_query, label):
    if not grants:
        return
    eligible = []
    scopes = set()
    target = behavior["id"] if behavior else None
    required = set(behavior.get("requiredPermissions", [])) if behavior else set()
    for role in roles:
        role_permissions = set(role.get("permissions", []))
        target_permissions = [permissions[identifier] for identifier in role_permissions
                              if identifier in permissions and permissions[identifier].get("targetRef") == target]
        if behavior and (not target_permissions or not required.issubset(role_permissions)):
            continue
        eligible.append(role_permissions)
        scopes.update(permission.get("dataScope", "ALL") for permission in target_permissions)
    proposed = set(grants)
    guidance = "可留空 permissionRef，沿用已确认的 M2/M5 授权；只附加独立确认的界面权限"
    if is_query and {"OWN", "ALL"}.issubset(scopes):
        _need(all(proposed.issubset(role_permissions) for role_permissions in eligible),
              f"{label} 的 permissionRef 会隐藏具有 OWN 或 ALL 授权的角色。{guidance}")
    _need(any(proposed.issubset(role_permissions) for role_permissions in eligible),
          f"{label} 的额外 permissionRef 无法由任何可执行角色同时满足。{guidance}")


def _actions(page, screen, report, entity, fields, behaviors, screen_map, flows, roles, permissions):
    points = _index(screen.get("actionPoints"), "prototypeActionRef", f"{screen['screenId']} 操作")
    _index(list(points.values()), "actionId", f"{screen['screenId']} 操作", fallback="id")
    _need(set(points) == {action["id"] for action in page.get("actions", [])},
          f"{screen['screenId']} 的操作没有一一映射确认原型")
    start_refs = {flow.get("runtime", {}).get("startBehaviorRef") for flow in flows
                  if flow.get("flowType") == "APPROVAL" and entity and entity["id"] in flow.get("businessObjectRefs", [])} - {None}
    actions = []
    for design in page.get("actions", []):
        point = points[design["id"]]
        kind = design["kind"]
        reference = point.get("behaviorRef")
        target = point.get("targetScreenRef")
        label = f"{screen['screenId']}.{design['id']}"
        _need(point.get("name") == design["label"], f"{label} 的按钮文案与确认原型不同")
        action = {"id": design["id"], "action_id": point.get("actionId") or point["id"], "kind": kind,
                  "label": design["label"], "description": design.get("description", "")}
        grants = point.get("permissionRef") or []
        _need(isinstance(grants, list) and all(isinstance(grant, str) and grant in permissions for grant in grants),
              f"{label} 的额外权限不是有效的 M5 权限")
        if grants:
            action["permissions"] = grants
        if kind in {"navigate", "open_form"}:
            _need(not reference and target == screen_map[design["target_page"]]["screenId"],
                  f"{label} 必须指向确认原型对应的 MU 目标界面")
            action["target_screen_id"] = target
        elif kind in {"cancel", "reset"}:
            _need(not reference and not target, f"{label} 是界面操作，不能调用业务行为")
        else:
            behavior = behaviors.get(reference)
            _need(behavior and behavior.get("triggerType") == "USER_ACTION" and not target,
                  f"{label} 必须关联一个人工 M2 行为")
            runtime = behavior["runtime"]
            operation = runtime["operation"]
            if kind == "query":
                _need(report and report["behaviorRef"] == reference and operation == "QUERY",
                      f"{label} 必须关联本页 M7 的唯一查询行为")
            else:
                _need(not report, f"{label} 为查询列表上的直接写入，需要明确记录标识和专门运行时支持")
                _need(entity and behavior["ownerEntity"] == entity["id"] and operation in _MUTATIONS.get(kind, set()),
                      f"{label} 的 M2 行为对象或操作类型与原型不同")
                _need(set(runtime.get("editableFields", [])) <= {field["name"] for field in fields},
                      f"{label} 的 M2 可编辑字段未在本页完整映射")
                if kind == "draft":
                    _need(reference not in start_refs, f"{label} 保存草稿不能启动审批流")
                if kind == "submit" and start_refs:
                    _need(reference in start_refs, f"{label} 的提交行为必须启动本对象对应的 M6 审批流")
            action["behavior_id"] = reference
        _check_action_permissions(grants, behaviors.get(reference), roles, permissions, kind == "query", label)
        actions.append(action)
    if report:
        _need(sum(action["kind"] == "query" for action in actions) == 1,
              f"{screen['screenId']} 需要一个映射到 M7 的查询操作")
    if any(action.get("behavior_id") in start_refs for action in actions) and any(
            action["kind"] in {"draft", "create", "save"} for action in actions):
        _need({"draft", "submit"} <= {action["kind"] for action in actions},
              f"{screen['screenId']} 带审批的录入需要独立的草稿和提交操作")
    return actions


def _menu(application, pages, source_screens):
    groups = _index(application.get("menus"), "menuId", "一级菜单")
    named = _index(list(groups.values()), "name", "一级菜单")
    screen_to_page = {screen["screenId"]: page for page, screen in source_screens.items()}
    menu_ids = set(groups)
    visited = set()
    normalized = []
    for group_name, group in named.items():
        children = _index(group.get("children"), "menuId", f"{group_name}二级菜单")
        _need(children and not group.get("screenRef") and not (menu_ids & children.keys()),
              f"{group_name} 必须仅包含至少一个 ID 唯一的二级菜单")
        menu_ids.update(children)
        items = []
        for menu_id, child in children.items():
            screen_id = child.get("screenRef")
            page_id = screen_to_page.get(screen_id)
            page = pages.get(page_id, {})
            _need(page and page_id not in visited and not child.get("children") and page.get("presentation") != "dialog"
                  and child.get("name") == page["name"] and (page.get("group") or "业务页面") == group_name,
                  f"二级菜单 {menu_id} 必须唯一对应确认原型的可导航界面、分组与名称")
            visited.add(page_id)
            items.append({"id": menu_id, "name": child["name"], "screen_id": screen_id})
        order = [source_screens[page_id]["screenId"] for page_id in pages
                 if any(item["screen_id"] == source_screens[page_id]["screenId"] for item in items)]
        _need(order == [item["screen_id"] for item in items], f"{group_name} 的菜单顺序与确认原型不同")
        normalized.append({"id": group["menuId"], "name": group_name, "items": items})
    _need(visited == {page_id for page_id, page in pages.items() if page.get("presentation") != "dialog"},
          "所有可导航界面都要有二级菜单，弹窗独立映射")
    group_order = list(dict.fromkeys(page.get("group") or "业务页面" for page in pages.values()
                                    if page.get("presentation") != "dialog"))
    _need(group_order == [group["name"] for group in normalized], "一级菜单顺序须对应确认原型")
    return normalized


def compile_ui(application, prototype, spec):
    """Return runtime rendering and audit records after comparing all stable IDs."""
    _need(application.get("prototypeContract") == UI_TRACEABILITY_VERSION, "七模型须按当前原型补齐追溯契约")
    pages = _index((prototype or {}).get("pages"), "id", "已确认原型页面")
    _need(pages, "需要已确认的可追溯界面原型")
    portable = application.get("prototypeDesign")
    if portable is not None:
        _need(isinstance(portable, dict) and portable.get("pages") == prototype["pages"]
              and portable.get("summary") == prototype["summary"], "模型内界面基线与平台确认原型不一致")
    screens = _index(spec["screens"], "prototypePageRef", "屏幕")
    _index(spec["screens"], "screenId", "屏幕")
    _need(set(pages) == set(screens), "每个已确认原型界面须且仅须有一个 MU 屏幕")
    menus = _menu(application, pages, screens)
    entities = {entity["id"]: entity for entity in spec["entities"]}
    behaviors = {behavior["id"]: behavior for behavior in spec["behaviors"]}
    reports = {report["id"]: report for report in spec["reports"]}
    permissions = {item.get("permissionId", item.get("id")): item for item in spec["permissions"]}
    compiled = []
    for page_id, page in pages.items():
        screen = screens[page_id]
        sid = screen["screenId"]
        layout = page["layout"]
        _need(layout in _LAYOUTS and screen.get("screenType") == _LAYOUTS[layout]
              and screen.get("name") == page["name"] and screen.get("presentation") == page.get("presentation"),
              f"{sid} 的屏幕名称、布局或弹窗类型与确认原型不同")
        _need(layout != "master_detail_form", f"{sid} 的主从表需要主从事务与控件的运行时适配")
        entity = entities.get(screen.get("objectRef"))
        report = reports.get(screen.get("queryReportRef"))
        if layout == "query_list":
            _need(report and (report.get("pagination") or {}).get("enabled") is True,
                  f"{sid} 查询列表需要有效且启用分页的 M7 报表")
            primary_id = next(item["objectRef"] for item in report["sourceObjects"] if item.get("primary"))
            _need(not screen.get("objectRef") or (entity and entity["id"] == primary_id),
                  f"{sid} 的对象必须是 M7 主来源")
            _need(not page.get("record_source") or screens[page["record_source"]].get("objectRef") == primary_id,
                  f"{sid} 的记录来源与 M7 主来源不同")
            entity = entities[primary_id]
        else:
            _need(entity and not screen.get("queryReportRef"), f"{sid} 需要有效的本页 M1 对象")
        elements = _index(screen.get("elements"), "id", f"{sid} 元素")
        by_field = _index([el for el in elements.values() if el.get("prototypeFieldRef")],
                          "prototypeFieldRef", f"{sid} 字段")
        by_column = _index([el for el in elements.values() if el.get("prototypeColumnRef")],
                           "prototypeColumnRef", f"{sid} 结果列")
        field_ids = {field["id"] for field in page.get("fields", [])}
        column_ids = {column["id"] for column in page.get("columns", [])}
        mapped_fields = {element["id"] for element in by_field.values()}
        mapped_columns = {element["id"] for element in by_column.values()}
        unmapped = elements.keys() - mapped_fields - mapped_columns
        _need(len(by_field) + len(by_column) == len(elements)
              and not (mapped_fields & mapped_columns)
              and by_field.keys() == field_ids and by_column.keys() == column_ids,
              f"{sid} 每个确认原型字段或结果列须且仅须映射一个元素。"
              f"期望字段 {sorted(field_ids)}，实际 {sorted(by_field)}；"
              f"期望结果列 {sorted(column_ids)}，实际 {sorted(by_column)}；"
              f"没有原型引用的元素 {sorted(unmapped)}，同时绑定字段和结果列的元素 {sorted(mapped_fields & mapped_columns)}")
        fields = [_field(field, by_field[field["id"]], screen, entity, report, entities, screens)
                  for field in page.get("fields", [])]
        _need(len({field["name"] for field in fields}) == len(fields), f"{sid} 的输入重复绑定同一字段")
        if report:
            _need({field["name"] for field in fields} == {item["name"] for item in report.get("parameters", [])},
                  f"{sid} 的过滤项须与 M7 参数一一对应")
        columns = _columns(page, screen, entity, report, by_column)
        _need(layout not in {"list", "list_maintenance", "query_list"} or columns,
              f"{sid} 为列表布局，必须定义实际显示列")
        _need(layout != "detail" or fields or columns, f"{sid} 的详情页须声明查看字段")
        actions = _actions(page, screen, report, entity, fields, behaviors, screens,
                           spec["flows"], spec["roles"], permissions)
        diagram = screen.get("layout")
        symbols = set(re.findall(r"[A-Za-z0-9_-]+", diagram)) if isinstance(diagram, str) else set()
        missing = (elements.keys() | {action["action_id"] for action in actions}) - symbols
        _need(symbols and not missing, f"{sid} ASCII 布局应包含该界面的元素和操作 ID，缺少 {sorted(missing)}")
        compiled.append({"id": sid, "prototype_page_id": page_id, "name": page["name"], "layout": _LAYOUTS[layout],
                         "presentation": page.get("presentation", "page"), "entity_id": entity["id"],
                         "report_id": report["id"] if report else None,
                         "fields": fields, "columns": columns, "actions": actions})
    observed = {action["behavior_id"] for screen in compiled for action in screen["actions"] if "behavior_id" in action}
    expected = {item["id"] for item in spec["behaviors"] if item.get("triggerType") == "USER_ACTION"}
    _need(observed == expected, "缺失 M2 人工行为对应的原型操作：" + "、".join(sorted(expected - observed)))
    ids = {screen["id"]: screen for screen in compiled}
    for screen in compiled:
        for action in screen["actions"]:
            if action["kind"] == "open_form":
                target = ids[action["target_screen_id"]]
                _need(target["presentation"] == "dialog" and target["entity_id"] == screen["entity_id"],
                      f"{screen['id']}.{action['id']} 打开的弹窗须绑定同一个对象")
    evidence = {
        "contract": UI_TRACEABILITY_VERSION, "prototype_sha256": prototype["sha256"], "model_hash": spec["model_hash"],
        "menus": [{"id": item["id"], "name": item["name"], "screen_ids": [child["screen_id"] for child in item["items"]]}
                  for item in menus],
        "screens": [{
            "screen_id": screen["id"], "prototype_page_id": screen["prototype_page_id"],
            "layout": screen["layout"], "presentation": screen["presentation"],
            "entity_id": screen["entity_id"], "report_id": screen["report_id"],
            "field_links": {item["id"]: {"element_id": item["element_id"], "binding": item["name"]}
                            for item in screen["fields"]},
            "column_links": {item["id"]: {"element_id": item["element_id"], "binding": item["name"]}
                             for item in screen["columns"]},
            "action_links": {item["id"]: {
                "mu_action_id": item["action_id"],
                **{key: item[key] for key in ("behavior_id", "target_screen_id") if key in item},
            } for item in screen["actions"]},
        } for screen in compiled],
    }
    return {"menus": menus, "screens": compiled}, evidence
