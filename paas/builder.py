"""Compile a validated seven-model bundle to a runnable, isolated application.

This extends the author's techbase rather than launching its customer demo.
The exact upstream source and license are retained alongside generated source,
models, migration, and the build manifest. Unsupported semantics stop a build.
"""
from __future__ import annotations

import ast
import copy
import hashlib
import json
import re
import secrets
import shutil
import sqlite3
import subprocess
import sys
import uuid
from pathlib import Path

import yaml

from .business import MODEL_FILES, entities, model_document, validate_models, _prototype_context
from .app_runtime import BusinessError, Runtime, expression_tree, ident, password_hash, now
from .ui_contract import compile_ui

UPSTREAM = Path(__file__).resolve().parent / "upstream/ontology-driven-dev"
RESERVED = {"id", "code", "gmt_create", "gmt_modified", "flag", "created_by", "version"}
SUPPORTED_TYPES = {"string", "text", "integer", "int", "decimal", "money", "number", "float", "double", "boolean", "bool", "date", "datetime", "timestamp", "enum", "aggregaterootref", "dictionaryref"}


def model_digest(models):
    return hashlib.sha256(json.dumps(models, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def _snake(value):
    value = re.sub(r"(?<!^)(?=[A-Z])", "_", value).lower()
    ident(value)
    return value


def _all_expressions(spec):
    for entity in spec["entities"]:
        for constraint in list(entity.get("constraints", []))+list(entity.get("invariants", [])):
            yield "对象约束"+entity["id"], constraint.get("expression") if isinstance(constraint, dict) else None
        for attribute in entity["attributes"]:
            for rule in attribute.get("refRules", []):
                yield "属性约束"+entity["id"]+"."+attribute["name"], rule.get("expression") if isinstance(rule, dict) else None
    for rule in spec["rules"]:
        yield "规则"+rule["id"], rule.get("expression")
        for param in rule.get("inputParams", []):
            if param.get("sourceField"):
                yield "规则参数"+rule["id"], param["sourceField"]
    for behavior in spec["behaviors"]:
        for condition in behavior.get("preconditions", []):
            yield "行为前提"+behavior["id"], condition.get("expression") if isinstance(condition, dict) else condition
        for condition in behavior.get("postconditions", []):
            if condition.get("expression"):
                yield "行为结果"+behavior["id"], condition["expression"]
    for flow in spec["flows"]:
        for node in flow.get("activities", []):
            for branch in node.get("branches", []):
                if branch.get("conditionExpression"):
                    yield "流程分支"+flow["id"], branch["conditionExpression"]


def compile_spec(project: dict, *, require_ui_traceability: bool = False) -> dict:
    models = project.get("models") or {}
    errors = validate_models(models)
    if errors:
        raise BusinessError("七模型校验失败："+"；".join(errors[:20]))
    def walk(value, path="models"):
        if isinstance(value, dict):
            runtime = value.get("runtime")
            if isinstance(runtime, dict) and runtime.get("unsupported"):
                errors.append(path+"声明未实现能力："+str(runtime["unsupported"]))
            for key, child in value.items():
                walk(child, path+"."+str(key))
        elif isinstance(value, list):
            for index, child in enumerate(value):
                walk(child, path+"["+str(index)+"]")
    walk(models)
    object_doc = model_document(models, "OBJECT")
    if object_doc.get("valueObjects") or object_doc.get("value_objects") or object_doc.get("aggregate_associations") or object_doc.get("associations") or object_doc.get("relations"):
        errors.append("M1含尚未完整编译的值对象或聚合关联结构，需要具体适配；不能忽略或改写已确认业务")
    for entity in object_doc.get("aggregates", object_doc.get("entities", [])):
        if entity.get("entities") or entity.get("valueObjects") or entity.get("value_objects"):
            errors.append(f"M1 {entity.get('id')}含主从子实体/值对象，尚未实现同事务与基数，禁止扁平化编译")
    all_entities = copy.deepcopy(entities(models))
    for entity in all_entities:
        runtime = entity.get("runtime") or {}
        if runtime.get("unsupported"):
            errors.append(f"对象{entity['id']}含未实现能力：{runtime['unsupported']}")
        entity["table"] = runtime.get("tableName") or "t_"+_snake(entity.get("alias") or entity["id"].replace("-", "_"))
        try:
            ident(entity["table"])
        except ValueError as exc:
            errors.append(str(exc))
        entity["codePrefix"] = runtime.get("codePrefix") or re.sub("[^A-Z]", "", entity.get("alias", "REC").upper())[:3].ljust(3, "X")
        for attribute in entity["attributes"]:
            if attribute["name"] in RESERVED:
                errors.append(f"{entity['id']}.{attribute['name']}与运行时字段冲突，请使用业务属性名")
            if attribute.get("type", "String").lower() not in SUPPORTED_TYPES:
                errors.append(f"不支持属性类型：{entity['id']}.{attribute['name']} {attribute.get('type')}")
            if attribute.get("type", "").lower() == "dictionaryref":
                # Resolve dictionary options from the same ontology instead of demo data.
                ref = attribute.get("dictionaryRef") or {}
                dictionary = next((d for d in model_document(models, "OBJECT").get("data_dictionaries", []) if d.get("id") == ref.get("dictionaryId")), None)
                options = []
                for group in (dictionary or {}).get("types", []):
                    if not ref.get("typeCode") or group.get("typeCode") == ref["typeCode"]:
                        options.extend({"value": x["code"], "label": x["label"]} for x in group.get("items", []) if x.get("enabled", True))
                if not options:
                    errors.append(f"字典字段{entity['id']}.{attribute['name']}没有当前模型内的选项")
                attribute["options"] = options
    if len({e["table"] for e in all_entities}) != len(all_entities):
        errors.append("不同业务对象映射到重复数据表")
    spec = {
        "name": project.get("name", "本体业务应用"), "project_id": project.get("id"),
        "model_hash": model_digest(models), "source": "sharptoolbox/ontology-driven-dev techbase + V9 deterministic compiler",
        "entities": all_entities,
        "behaviors": copy.deepcopy(model_document(models, "BEHAVIOR").get("behaviors", [])),
        "rules": copy.deepcopy(model_document(models, "RULE").get("rules", [])),
        "roles": copy.deepcopy(model_document(models, "ACTOR").get("roles", [])),
        "permissions": copy.deepcopy(model_document(models, "ACTOR").get("permissions", [])),
        "flows": copy.deepcopy(model_document(models, "FLOW").get("flows", [])),
        "reports": copy.deepcopy(model_document(models, "REPORT").get("query_reports", [])),
        "screens": copy.deepcopy(model_document(models, "UI").get("screens", [])),
        "menus": copy.deepcopy(model_document(models, "UI").get("application", {}).get("menus", [])),
        "confirmed_prototype": ({key: project["prototype"].get(key) for key in ("id", "revision", "sha256", "requirements_sha256", "summary", "pages")} if project.get("prototype") else None),
    }
    entity_map = {e["id"]: e for e in all_entities}
    behavior_map = {b["id"]: b for b in spec["behaviors"]}
    rules_by_id = {r["id"]: r for r in spec["rules"]}
    for behavior in spec["behaviors"]:
        runtime = behavior.get("runtime") or {}
        operation = str(runtime.get("operation", "")).upper()
        if operation not in {"CREATE", "UPDATE", "TRANSITION", "QUERY"}:
            errors.append(f"行为{behavior['id']}缺可执行runtime.operation")
        runtime["operation"] = operation
        behavior["runtime"] = runtime
        if (behavior.get("behaviorType") == "QUERY") != (operation == "QUERY"):
            errors.append(f"行为{behavior['id']}的查询类型与runtime.operation不一致")
        if behavior.get("triggerType") not in {"USER_ACTION", "SYSTEM"}:
            errors.append(f"行为{behavior['id']}triggerType必须为USER_ACTION或SYSTEM")
        if operation == "QUERY" and any(behavior.get(key) for key in ("preconditions", "postconditions", "appliedRules", "syncTriggers")):
            errors.append(f"QUERY行为{behavior['id']}含当前查询运行时未执行的前提/规则/后置或联动，需具体适配后构建；不能忽略")
        if runtime.get("unsupported"):
            errors.append(f"行为{behavior['id']}含未实现能力：{runtime['unsupported']}")
        entity = entity_map.get(behavior["ownerEntity"], {})
        fields = {a["name"] for a in entity.get("attributes", [])}
        if not isinstance(runtime.get("editableFields", []), list) or set(runtime.get("editableFields", [])) - fields:
            errors.append(f"行为{behavior['id']}editableFields包含未定义字段")
        for key in ("stateField", "submitterField"):
            if runtime.get(key) and runtime[key] not in fields:
                errors.append(f"行为{behavior['id']}的{key}不是对象字段")
        if runtime.get("separationOfDuties") is True and not runtime.get("submitterField"):
            errors.append(f"行为{behavior['id']}启用了职责分离，必须绑定提交行为写入的真实submitterField")
        if any(isinstance(condition, dict) and "violationMessage" in condition
               and (not isinstance(condition["violationMessage"], str) or not condition["violationMessage"].strip())
               for condition in behavior.get("preconditions", [])):
            errors.append(f"行为{behavior['id']}前置条件的violationMessage须为非空文本")
        for rule_ref in behavior.get("appliedRules", []):
            rule_id = rule_ref.get("ruleRef") if isinstance(rule_ref, dict) else rule_ref
            if rules_by_id.get(rule_id, {}).get("ruleType") in {"DERIVATION", "CALCULATION"}:
                if (runtime.get("ruleBindings") or {}).get(rule_id) not in fields:
                    errors.append(f"行为{behavior['id']}的计算/推导规则{rule_id}缺少runtime.ruleBindings真实字段")
        for effect in behavior.get("postconditions", []):
            if not isinstance(effect, dict) or not effect.get("field") or effect["field"].split(".")[-1] not in fields:
                errors.append(f"行为{behavior['id']}后置条件没有明确真实字段")
            elif "setValue" not in effect and "expression" not in effect:
                errors.append(f"行为{behavior['id']}后置条件没有赋值或表达式")
        if operation == "CREATE":
            assigned = set(runtime.get("editableFields", []))
            assigned.update(effect["field"].split(".")[-1] for effect in behavior.get("postconditions", []) if isinstance(effect, dict) and isinstance(effect.get("field"), str))
            if runtime.get("targetState") is not None:
                assigned.add(runtime.get("stateField", "status"))
            if runtime.get("submitterField"):
                assigned.add(runtime["submitterField"])
            for attribute in entity.get("attributes", []):
                if attribute.get("required") and attribute["name"] not in assigned and attribute.get("defaultValue", attribute.get("default")) in (None, "") and not attribute.get("autoGenerate") and not attribute.get("primaryKey"):
                    errors.append(f"新建行为{behavior['id']}无法填充必填字段{attribute['name']}：缺输入/默认/自动生成/后置赋值")
        if operation != "QUERY" and not any(p.get("targetRef") == behavior["id"] for p in spec["permissions"]):
            errors.append(f"行为{behavior['id']}没有可授权的M5权限")
        required = set(behavior.get("requiredPermissions", []))
        executable_roles = []
        for role in spec["roles"]:
            grants = set(role.get("permissions", []))
            target_grants = {p.get("permissionId", p.get("id")) for p in spec["permissions"] if p.get("targetRef") == behavior["id"]}
            if required.issubset(grants) and target_grants.intersection(grants):
                executable_roles.append(role.get("roleId", role.get("id")))
        if not executable_roles:
            errors.append(f"行为{behavior['id']}没有任何预置角色可执行：requiredPermissions按AND判断且仍需M5 targetRef授权；不同scope的替代授权可令requiredPermissions为空数组，由M5各角色的targetRef和dataScope继续强制")
        for trigger in behavior.get("syncTriggers", []):
            trigger = {"behaviorRef": trigger} if isinstance(trigger, str) else trigger
            target = behavior_map.get(trigger.get("behaviorRef") or trigger.get("targetBehaviorRef"), {})
            if target.get("ownerEntity") != behavior["ownerEntity"] and trigger.get("targetRefField") not in fields:
                errors.append(f"行为{behavior['id']}跨对象联动缺明确targetRefField")
    for permission in spec["permissions"]:
        if permission.get("dataScope", "ALL") not in {"ALL", "OWN"}:
            errors.append(f"当前运行时尚不支持数据范围{permission.get('dataScope')}，请明确实现后构建")
    for rule in spec["rules"]:
        if rule.get("ruleType", "VALIDATION") not in {"VALIDATION", "VALIDATION_RULE", "DERIVATION", "CALCULATION"}:
            errors.append(f"规则{rule['id']}类型未支持")
    for label, expression in _all_expressions(spec):
        try:
            tree = expression_tree(expression)
            valid_names = {"record", "input", "actor", "value", "len", "abs", "min", "max", "round"}
            for entity in all_entities:
                alias = entity.get("alias", "")
                valid_names.update([alias, alias[:1].lower()+alias[1:]])
                valid_names.update(a["name"] for a in entity["attributes"])
            valid_names.update(param["name"] for rule in spec["rules"] for param in rule.get("inputParams", []))
            all_fields = {a["name"] for entity in all_entities for a in entity["attributes"]} | RESERVED
            namespaces = {"record": all_fields, "input": all_fields, "actor": {"id", "username", "name", "roles"}}
            for entity in all_entities:
                alias = entity.get("alias", "")
                aliases = [alias, alias[:1].lower()+alias[1:]]
                for candidate in aliases:
                    namespaces[candidate] = {a["name"] for a in entity["attributes"]} | RESERVED
            for node in ast.walk(tree):
                if isinstance(node, ast.Name) and node.id not in valid_names:
                    raise BusinessError("表达式引用未定义名称："+node.id)
                if isinstance(node, ast.Attribute):
                    if not isinstance(node.value, ast.Name) or node.value.id not in namespaces or node.attr not in namespaces[node.value.id]:
                        raise BusinessError("表达式引用未定义属性："+node.attr)
        except ValueError as exc:
            errors.append(label+"："+str(exc))
    _validate_reports(spec, errors)
    _expand_subflows(spec, errors)
    for flow in spec["flows"]:
        for activity in flow.get("activities", []):
            behavior = behavior_map.get(activity.get("behaviorRef"))
            if activity.get("activityType") in {"USER_TASK", "SYSTEM_TASK"} and behavior and behavior["runtime"]["operation"] == "QUERY":
                errors.append(f"流程{flow['id']}的任务{activity['activityId']}不能绑定只读QUERY，查询不会推进流程")
    # Do not silently omit model-level integrity constraints.
    for entity in all_entities:
        entity["constraints"] = list(entity.get("constraints", []))+list(entity.get("invariants", []))
        for constraint in entity.get("constraints", []):
            if not isinstance(constraint, dict) or not constraint.get("expression"):
                errors.append(f"对象{entity['id']}的约束缺可执行expression")
            else:
                try:
                    expression_tree(constraint["expression"])
                except ValueError as exc:
                    errors.append(f"对象{entity['id']}约束：{exc}")
        for attribute in entity["attributes"]:
            for rule in attribute.get("refRules", []):
                if not isinstance(rule, dict) or not rule.get("expression"):
                    errors.append(f"属性{entity['id']}.{attribute['name']}约束缺expression")
                else:
                    try:
                        expression_tree(rule["expression"])
                    except ValueError as exc:
                        errors.append(f"属性{entity['id']}.{attribute['name']}约束：{exc}")
            target = attribute.get("targetAggregate") or attribute.get("referenceEntity") or (attribute.get("aggregateRootRef") or {}).get("aggregateId")
            if target:
                reference_field = attribute.get("referenceField", "id")
                target_entity = entity_map.get(target, {})
                if reference_field != "id" and not any(a["name"] == reference_field and (a.get("unique") or a.get("primaryKey")) for a in target_entity.get("attributes", [])):
                    errors.append(f"引用目标{target}.{reference_field}不是唯一字段")
    if errors:
        raise BusinessError("模型尚不能安全编译："+"；".join(errors[:30]))
    application = model_document(models, "UI").get("application", {})
    if require_ui_traceability or application.get("prototypeContract"):
        try:
            spec["ui_plan"], spec["ui_traceability"] = compile_ui(application, _prototype_context(project), spec)
        except (KeyError, TypeError, ValueError) as error:
            raise BusinessError(f"模型尚不能安全编译：{error}") from None
    return spec


def _expand_subflows(spec, errors):
    flows = {f["id"]: f for f in spec["flows"]}
    used = set()
    for flow in spec["flows"]:
        def expand(nodes, stack):
            result = []
            for node in nodes:
                kind = node.get("activityType")
                if any(key in node and node[key] is not None for key in ("conditionExpression", "condition", "ruleRef")):
                    errors.append(f"流程{flow['id']}活动{node.get('activityId')}含尚未执行的节点条件；只能使用GATEWAY分支条件")
                if kind != "GATEWAY" and len(node.get("nextActivities", [])) > 1:
                    errors.append(f"流程{flow['id']}活动{node.get('activityId')}含多后继，当前未实现并发/分支语义，不能只取第一项")
                if kind == "SUB_FLOW_CALL":
                    ref = node.get("subFlowRef")
                    if ref in stack or ref not in flows:
                        errors.append("流程子调用不存在或成环")
                        continue
                    used.add(ref)
                    sub = flows[ref]
                    children = expand(copy.deepcopy(sub.get("activities", [])), stack+[ref])
                    prefix = node["activityId"]+"__"
                    for child in children:
                        old_id = child["activityId"]
                        child["activityId"] = prefix+old_id
                        child["nextActivities"] = [prefix+x for x in child.get("nextActivities", [])]
                        for branch in child.get("branches", []):
                            branch["targetActivity"] = prefix+branch["targetActivity"]
                        if child.get("activityType") == "END":
                            child["activityType"] = "START"
                            child["nextActivities"] = list(node.get("nextActivities", []))
                    result.append({"activityId": node["activityId"], "activityType": "START", "name": node.get("name", "子流程"), "nextActivities": [prefix+sub["startActivity"]]})
                    result.extend(children)
                else:
                    if kind not in {"START", "END", "USER_TASK", "SYSTEM_TASK", "GATEWAY"}:
                        errors.append(f"流程{flow['id']}活动类型{kind}未实现")
                    result.append(node)
            return result
        flow["activities"] = expand(copy.deepcopy(flow.get("activities", [])), [flow["id"]])
        nodes = {a["activityId"]: a for a in flow["activities"]}
        # Auto-executed nodes must terminate at a user task or END.  Reject an
        # automatic cycle rather than allowing unbounded in-transaction calls.
        def auto_cycle(node_id, visiting, visited):
            if node_id in visiting:
                return True
            if node_id in visited or node_id not in nodes:
                return False
            node = nodes[node_id]
            if node.get("activityType") in {"USER_TASK", "END"}:
                return False
            visiting.add(node_id)
            targets = list(node.get("nextActivities", []))+[b["targetActivity"] for b in node.get("branches", [])]
            if any(auto_cycle(target, visiting, visited) for target in targets):
                return True
            visiting.remove(node_id); visited.add(node_id)
            return False
        if any(auto_cycle(node_id, set(), set()) for node_id in nodes):
            errors.append(f"流程{flow['id']}存在自动节点循环")
    for ref in used:
        flows[ref].setdefault("runtime", {})["embedded"] = True


def _validate_reports(spec, errors):
    entities_by_id = {e["id"]: e for e in spec["entities"]}
    for report in spec["reports"]:
        sources = {s["alias"]: entities_by_id.get(s["objectRef"], {}) for s in report.get("sourceObjects", [])}
        for source in report.get("sourceObjects", []):
            if source.get("preAggregation") or source.get("entityPath"):
                errors.append(f"报表{report['id']}含尚未实现的来源预聚合或子实体路径，不能按普通表关联执行")
        if report.get("having"):
            errors.append(f"报表{report['id']}含尚未实现的HAVING条件")
        pagination = report.get("pagination") or {}
        if pagination:
            if not isinstance(pagination.get("enabled"), bool):
                errors.append(f"报表{report['id']}分页enabled必须为布尔值")
            if pagination.get("enabled") is True:
                default, maximum = pagination.get("defaultPageSize", 20), pagination.get("maxPageSize", 200)
                if isinstance(default, bool) or isinstance(maximum, bool) or not isinstance(default, int) or not isinstance(maximum, int) or not 1 <= default <= maximum <= 1000:
                    errors.append(f"报表{report['id']}分页必须满足1<=默认页长<=最大页长<=1000")
        def field(value, allow_star=False):
            if value == "*" and allow_star:
                return True
            if not isinstance(value, str) or value.count(".") != 1:
                return False
            alias, attribute = value.split(".")
            return alias in sources and attribute in ({a["name"] for a in sources[alias].get("attributes", [])} | RESERVED)
        cols = report.get("resultColumns", [])
        if not cols:
            errors.append(f"报表{report['id']}没有结果列")
        for col in cols:
            try:
                ident(col.get("name", ""))
            except ValueError:
                errors.append(f"报表{report['id']}列名非法")
            aggregate = (col.get("aggregateFunction") or "NONE").upper()
            if aggregate not in {"NONE", "SUM", "COUNT", "COUNT_DISTINCT", "AVG", "MIN", "MAX"} or not field(col.get("sourceExpression"), aggregate == "COUNT"):
                errors.append(f"报表{report['id']}结果表达式未支持")
        for condition in report.get("conditions", []):
            if not field(condition.get("leftExpression")) or condition.get("operator") not in {"EQ", "NE", "GT", "GE", "LT", "LE", "LIKE", "IN"}:
                errors.append(f"报表{report['id']}筛选定义未支持")
            if condition.get("logicalConnector", "AND").upper() != "AND":
                errors.append(f"报表{report['id']}当前编译器仅支持AND筛选组合，不能忽略OR语义")
            if condition.get("parameterRef") not in {p["name"] for p in report.get("parameters", [])}:
                errors.append(f"报表{report['id']}筛选未绑定明确的输入参数")
        for group in report.get("groupBy", []):
            if not field(group.get("sourceExpression", "") if isinstance(group, dict) else group):
                errors.append(f"报表{report['id']}分组字段未定义")
        for order in report.get("orderBy", report.get("sortBy", [])):
            expression = order.get("expression") or order.get("field") or order.get("column")
            if order.get("direction", "ASC").upper() not in {"ASC", "DESC"} or (not field(expression) and expression not in {c["name"] for c in cols}):
                errors.append(f"报表{report['id']}排序表达式未定义")
        for join in report.get("joins", []):
            match = re.fullmatch(r"\s*([A-Za-z_]\w*\.[A-Za-z_]\w*)\s*={1,2}\s*([A-Za-z_]\w*\.[A-Za-z_]\w*)\s*", join.get("conditionExpression", ""))
            if not match or not field(match[1]) or not field(match[2]):
                errors.append(f"报表{report['id']}关联表达式不是明确字段等值关联")


def _ddl(spec):
    statements = ["PRAGMA foreign_keys=ON;"]
    for entity in spec["entities"]:
        columns = ['"id" TEXT PRIMARY KEY', '"code" TEXT NOT NULL UNIQUE', '"gmt_create" TEXT NOT NULL', '"gmt_modified" TEXT NOT NULL', '"flag" INTEGER NOT NULL DEFAULT 1 CHECK(flag IN (0,1))', '"created_by" TEXT NOT NULL', '"version" INTEGER NOT NULL DEFAULT 1']
        for attr in entity["attributes"]:
            typ = attr.get("type", "String").lower()
            sql_type = "INTEGER" if typ in {"integer", "int", "boolean", "bool"} else "NUMERIC" if typ in {"decimal", "money", "number", "float", "double"} else "TEXT"
            definition = ident(attr["name"])+" "+sql_type
            if attr.get("required"):
                definition += " NOT NULL"
            if attr.get("unique"):
                definition += " UNIQUE"
            columns.append(definition)
        entity_by_id = {e["id"]: e for e in spec["entities"]}
        for attr in entity["attributes"]:
            target_id = attr.get("targetAggregate") or attr.get("referenceEntity") or (attr.get("aggregateRootRef") or {}).get("aggregateId")
            if target_id:
                target = entity_by_id[target_id]
                target_field = attr.get("referenceField", "id")
                columns.append("FOREIGN KEY ("+ident(attr["name"])+") REFERENCES "+ident(target["table"])+"("+ident(target_field)+") ON DELETE RESTRICT")
        statements.append("CREATE TABLE "+ident(entity["table"])+" (\n  "+",\n  ".join(columns)+"\n);")
    statements += [
        "CREATE TABLE runtime_audit(id TEXT PRIMARY KEY, occurred_at TEXT NOT NULL, actor_id TEXT NOT NULL, entity_id TEXT NOT NULL, record_id TEXT NOT NULL, behavior_id TEXT NOT NULL, details TEXT NOT NULL);",
        "CREATE TABLE runtime_flows(id TEXT PRIMARY KEY, flow_id TEXT NOT NULL, entity_id TEXT NOT NULL, record_id TEXT NOT NULL, activity_id TEXT NOT NULL, status TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);",
        "CREATE TABLE runtime_migrations(version TEXT PRIMARY KEY, applied_at TEXT NOT NULL, model_hash TEXT NOT NULL);",
        "CREATE TABLE runtime_chat(id TEXT PRIMARY KEY, created_at TEXT NOT NULL, actor_id TEXT NOT NULL, role TEXT NOT NULL, content TEXT NOT NULL);",
    ]
    return "\n\n".join(statements)+"\n"


def _register_upstream(code_app: Path):
    """Run the author's actual semantic registry against this generated bundle."""
    backend = code_app / "upstream-techbase/backend"
    script = (
        "import json,sys; sys.path.insert(0,sys.argv[1]); "
        "from ontology.registry import load_ontology; "
        "r=load_ontology(sys.argv[2]); "
        "print(json.dumps({k:len(v) for k,v in r.items()}))"
    )
    completed = subprocess.run([sys.executable, "-c", script, str(backend), str(code_app / "models")], capture_output=True, text=True, timeout=30)
    if completed.returncode:
        raise BusinessError("作者语义注册表无法读取生成模型："+completed.stderr[-1500:])
    return json.loads(completed.stdout.strip().splitlines()[-1])


def build_application(project: dict, output_dir: Path, *, require_ui_traceability: bool = False) -> dict:
    from .contract_reference import build_reference_application
    if project.get('reference_adoption'):
        return build_reference_application(project, output_dir, model_digest(project.get('models') or {}))
    prototype = _prototype_context(project)
    if prototype and (project.get("models", {}).get("manifest.json") or {}).get("prototype_sha256") != prototype["sha256"]:
        raise BusinessError("模型尚未绑定当前已确认原型，请重新校核并确认模型后构建")
    spec = compile_spec(project, require_ui_traceability=require_ui_traceability)
    output_dir = Path(output_dir).resolve()
    if output_dir.exists() and any(output_dir.iterdir()):
        raise BusinessError("构建目录已存在内容，请使用新的不可变版本目录")
    output_dir.mkdir(parents=True, exist_ok=True)
    output_dir.chmod(0o700)
    code_app = output_dir / "code-app"
    code_app.mkdir()
    for name in ("models", "data", "web", "migrations"):
        (code_app / name).mkdir()
    shutil.copytree(UPSTREAM / "techbase", code_app / "upstream-techbase", ignore=shutil.ignore_patterns("node_modules", "__pycache__", "dist", ".env", "*.db", "*.pyc"))
    shutil.copy2(UPSTREAM / "LICENSE", code_app / "UPSTREAM-LICENSE")
    for filename, model in project["models"].items():
        if filename not in MODEL_FILES and filename != "manifest.json":
            raise BusinessError("模型包包含未知文件")
        if filename.endswith(".yaml"):
            (code_app / "models" / filename).write_text(yaml.safe_dump(model, allow_unicode=True, sort_keys=False), encoding="utf-8")
    manifest = copy.deepcopy(project["models"].get("manifest.json") or {})
    manifest["model_files"] = list(MODEL_FILES)
    (code_app / "models/manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2))
    registry = _register_upstream(code_app)
    spec["upstream_registry_counts"] = registry
    ddl = _ddl(spec)
    spec["migration_sha256"] = hashlib.sha256(ddl.encode()).hexdigest()
    (code_app / "runtime.json").write_text(json.dumps(spec, ensure_ascii=False, indent=2))
    (code_app / "migrations/001_initial.sql").write_text(ddl)
    with sqlite3.connect(code_app / "data/application.db") as conn:
        conn.executescript(ddl)
        conn.execute("INSERT INTO runtime_migrations VALUES(?,?,?)", ["001", now(), spec["model_hash"]])
    accounts, credentials = [], []
    for index, role in enumerate(spec["roles"]):
        rid = role.get("roleId", role.get("id"))
        password = secrets.token_urlsafe(14)
        salt = secrets.token_hex(16)
        username = "user"+str(index+1)
        account = {"id": uuid.uuid4().hex, "username": username, "name": role.get("name", rid), "roles": [rid], "salt": salt, "password_hash": password_hash(password, salt)}
        accounts.append(account)
        credentials.append({"username": username, "password": password, "role_id": rid, "role": role.get("name", rid)})
    (code_app / "accounts.json").write_text(json.dumps(accounts, ensure_ascii=False, indent=2))
    (code_app / "accounts.json").chmod(0o600)
    (output_dir / "initial-credentials.json").write_text(json.dumps(credentials, ensure_ascii=False, indent=2))
    (output_dir / "initial-credentials.json").chmod(0o600)
    shutil.copy2(Path(__file__).with_name("app_runtime.py"), code_app / "runtime.py")
    shutil.copy2(Path(__file__).with_name("provider.py"), code_app / "provider.py")
    (code_app / "server.py").write_text("from pathlib import Path\nfrom runtime import create_standalone_app\napp = create_standalone_app(Path(__file__).resolve().parent)\nif __name__ == '__main__':\n    app.run(host='127.0.0.1', port=5400, debug=False)\n")
    (code_app / "requirements.txt").write_text("Flask>=3.0,<4\nPyYAML>=6.0,<7\npython-dotenv>=1.0,<2\n")
    package = Path(__file__).parent
    web = package / "web"
    styles = (web / "ui-system.css").read_text(encoding="utf-8")
    styles += "\n" + (package / "generated_app_shell.css").read_text(encoding="utf-8")
    styles += "\n" + (package / "generated_app_ui.css").read_text(encoding="utf-8")
    document = APP_HTML.replace("<style></style>", "<style>\n" + styles + "\n</style>")
    if spec.get("ui_plan"):
        source = (package / "generated_app_ui.js").read_text(encoding="utf-8")
        document = document.replace("const nav=$('#nav');", "if(meta.ui_plan){await renderModelApp();return}const nav=$('#nav');")
        document = document.replace("async function load(id){", "async function load(id){if(meta.ui_plan)return modelOpenEntity(id);")
        document = document.replace("async function reportView(id){", "async function reportView(id){if(meta.ui_plan)return modelOpenReport(id);")
        document = document.replace("async function edit(bid,rid,suggested={}){",
                                    "async function edit(bid,rid,suggested={}){if(meta.ui_plan)return modelAssistantAction(bid,rid,suggested);")
        document = document.replace("start().catch(err);", source + "\nstart().catch(err);")
    composer = (web / "elastic-textarea.js").read_text(encoding="utf-8")
    shutil.copy2(web / "elastic-textarea.LICENSE.txt", code_app / "web/elastic-textarea.LICENSE.txt")
    document = document.replace("</body>", "<script>\n" + composer + "\n</script>" + APP_AI_HTML + "</body>")
    (code_app / "web/index.html").write_text(document, encoding="utf-8")
    (code_app / "README.md").write_text(
        f"# {spec['name']}\n\n本应用由当前项目七模型实际编译，数据库初始为空。\n\n"
        "安装：`python -m venv .venv`、激活后 `pip install -r requirements.txt`，运行 `python server.py`。\n"
        "地址：http://127.0.0.1:5400/ 。源码ZIP首次启动时按迁移创建空业务库、按M5生成全新随机角色账号；初始凭据写入data/initial-credentials.json（权限0600），控制台只打印文件路径。\n\n"
        "模型在 models/，DDL在 migrations/001_initial.sql，实际数据在 data/application.db。\n"
        "应用右侧助手使用环境变量PAAS_API_KEY和PAAS_MODEL（默认glm-5.3-flash），可选PAAS_BASE_URL；密钥不包含在开发包。助手只执行当前角色可用查询，写入必须再次点击确认。\n"
        "遵循作者V9模型，runtime扩展用于确定性执行。原techbase源码在upstream-techbase/，许可在UPSTREAM-LICENSE。\n"
        "再次启动校验已有库的迁移ledger、模型SHA和表结构，不匹配时停止，绝不覆盖旧库。修改模型后通过PaaS构建新版本；生产数据迁移需单独核对。源码导出不携带既有数据库、账号hash、会话密钥或日志。\n"
    )
    receipt = {"status": "built", "output_dir": str(output_dir), "app_dir": str(code_app), "database_path": str(code_app / "data/application.db"), "model_hash": spec["model_hash"], "prototype_sha256": (spec.get("confirmed_prototype") or {}).get("sha256"), "tables": [e["table"] for e in spec["entities"]], "app_url": f"/apps/{project.get('id')}/", "accounts": credentials, "upstream_registry_counts": registry, "built_at": now(), "source": spec["source"], "migration": "001_initial.sql", "initial_business_rows": 0}
    if spec.get("ui_traceability"):
        receipt["ui_traceability"] = spec["ui_traceability"]
    (output_dir / "build-manifest.json").write_text(json.dumps({k: v for k, v in receipt.items() if k != "accounts"}, ensure_ascii=False, indent=2))
    return receipt


APP_HTML = r'''<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>业务应用</title>
<style></style>
</head>
<body>
<header class="app-topbar ui-topbar">
  <div class="ui-brand"><span class="ui-brand-mark" aria-hidden="true">O</span><h1 id="title">业务应用</h1></div>
  <div id="session"></div>
</header>
<div id="app"></div><div id="modal"></div>
<script>
const base=location.pathname.replace(/\/?$/,'/'), $=s=>document.querySelector(s); let meta,selected=null,records=[],currentReport=null;
const esc=x=>String(x??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
async function api(path,body){const r=await fetch(base+'api/'+path,{method:body===undefined?'GET':'POST',headers:{'Content-Type':'application/json'},body:body===undefined?undefined:JSON.stringify(body)});const d=await r.json();if(!r.ok)throw Error(d.error||d.message||'操作失败');return d}
function err(e){const el=$('#notice');if(el)el.innerHTML='<div class="error">'+esc(e.message)+'</div>';else alert(e.message)}
async function start(){meta=await api('meta');$('#title').textContent=meta.name;document.title=meta.name;if(!meta.actor){$('#session').innerHTML='';$('#app').innerHTML='<form class="panel login" id="login"><h2>登录业务应用</h2><label>账号<input name="username" autocomplete="username" required></label><label>密码<input name="password" type="password" autocomplete="current-password" required></label><div id="notice"></div><button class="primary">登录</button></form>';$('#login').onsubmit=async e=>{e.preventDefault();try{await api('login',Object.fromEntries(new FormData(e.target)));await start()}catch(x){err(x)}};return}$('#session').innerHTML='<span>'+esc(meta.actor.name)+'</span> <button id="logout">退出</button>';$('#logout').onclick=async()=>{await api('logout',{});await start()};$('#app').innerHTML='<main><nav class="app-navigation ui-navigation" id="nav" aria-label="业务导航"></nav><section class="content"><div id="notice" aria-live="polite"></div><div id="workspace"></div></section></main>';const nav=$('#nav');for(const en of meta.entities){const b=document.createElement('button');b.textContent=en.name;b.onclick=()=>load(en.id);nav.append(b)}for(const report of meta.reports){if(!meta.behaviors.some(b=>b.id===report.behaviorRef))continue;const b=document.createElement('button');b.textContent='报表 · '+report.name;b.onclick=()=>reportView(report.id);nav.append(b)}const task=document.createElement('button');task.textContent='审批待办';task.onclick=tasksView;nav.append(task);mountAssistant();if(meta.entities.length)await load(selected||meta.entities[0].id)}
async function load(id){selected=id;currentReport=null;const en=meta.entities.find(e=>e.id===id);try{records=(await api('entities/'+encodeURIComponent(id)+'/records')).records;renderRecords(en)}catch(e){$('#workspace').innerHTML='<div class="panel"><h2>'+esc(en.name)+'</h2><div class="error">'+esc(e.message)+'</div></div>'}}
function renderRecords(en){const creates=meta.behaviors.filter(b=>b.ownerEntity===en.id&&b.runtime.operation==='CREATE');const fields=en.attributes;$('#workspace').innerHTML='<div class="panel"><div class="toolbar"><h2>'+esc(en.name)+'</h2><div class="actions">'+creates.map(b=>'<button class="primary" data-create="'+esc(b.id)+'">'+esc(b.name)+'</button>').join('')+'</div></div><div class="scroll"><table><thead><tr>'+fields.map(f=>'<th>'+esc(f.label||f.name)+'</th>').join('')+'<th>操作</th></tr></thead><tbody>'+records.map(r=>'<tr>'+fields.map(f=>'<td>'+esc(r[f.name])+'</td>').join('')+'<td><div class="actions">'+meta.behaviors.filter(b=>b.ownerEntity===en.id&&['UPDATE','TRANSITION'].includes(b.runtime.operation)&&(!b.runtime.allowedStates?.length||b.runtime.allowedStates.includes(r[b.runtime.stateField||'status']))).map(b=>'<button data-act="'+esc(b.id)+'" data-record="'+esc(r.id)+'">'+esc(b.name)+'</button>').join('')+'</div></td></tr>').join('')+'</tbody></table></div>'+(records.length?'':'<div class="empty">暂无记录，可从右上角新建</div>')+'</div>';document.querySelectorAll('[data-create]').forEach(b=>b.onclick=()=>edit(b.dataset.create));document.querySelectorAll('[data-act]').forEach(b=>b.onclick=()=>edit(b.dataset.act,b.dataset.record))}
function formFields(entity,behavior){const screens=(meta.screens||[]).filter(s=>s.objectRef===entity.id||s.aggregateRef===entity.id);const screen=screens.find(s=>(s.actionPoints||[]).some(a=>a.behaviorRef===behavior))||screens[0]||{};const elements=[];function walk(list){for(const item of list||[]){elements.push(item);for(const key of ['elements','children','fields'])if(Array.isArray(item[key]))walk(item[key]);}}walk(screen.elements);const page=(meta.confirmed_prototype?.pages||[]).find(p=>p.id===screen.prototypePageRef);return entity.attributes.map(field=>{const element=elements.find(x=>typeof x.dataBinding==='string'&&(x.dataBinding===field.name||x.dataBinding.endsWith('.'+field.name)))||{};const planned=(page?.fields||[]).find(x=>x.id===field.name)||{};const help=[planned.help,element.help,element.helpText,element.description,field.help,field.description,...(field.refRules||[]).flatMap(r=>[r.description,r.violationMessage])].filter(v=>typeof v==='string'&&v.trim());return {...field,label:element.label||field.label,uiHelp:[...new Set(help)].join('；')};});}
async function edit(bid,rid,suggested={}){const b=meta.behaviors.find(x=>x.id===bid),en=meta.entities.find(x=>x.id===b.ownerEntity),r={...(records.find(x=>x.id===rid)||{}),...suggested},fields=formFields(en,bid).filter(f=>b.runtime.editableFields?.includes(f.name));for(const f of fields){const target=f.targetAggregate||f.referenceEntity||f.aggregateRootRef?.aggregateId;if(target){try{const targetEntity=meta.entities.find(x=>x.id===target),data=await api('entities/'+encodeURIComponent(target)+'/records');const labelField=targetEntity?.attributes.find(a=>/name|title|label/i.test(a.name))?.name;f._referenceOptions=data.records.map(x=>({value:x[f.referenceField||'id'],label:(labelField?x[labelField]:x.code)||x.id}))}catch(error){err(error);return}}}$('#modal').innerHTML='<div class="modal"><form class="panel" id="edit"><h2>'+esc(b.name)+'</h2><p class="quiet">'+esc(b.description||'')+'</p><div class="fields">'+fields.map(f=>{const options=f._referenceOptions||f.enumValues||f.options;return '<label>'+esc(f.label||f.name)+(f.required?' *':'')+(options?'<select name="'+esc(f.name)+'"><option value="">请选择</option>'+options.map(v=>{const val=typeof v==='object'?v.value:v,label=typeof v==='object'?v.label:v;return '<option value="'+esc(val)+'" '+(String(r[f.name]??f.defaultValue??'')===String(val)?'selected':'')+'>'+esc(label)+'</option>'}).join('')+'</select>':'<input name="'+esc(f.name)+'" value="'+esc(r[f.name]??f.defaultValue??'')+'" '+(f.required?'required':'')+' type="'+(['Integer','Decimal','Number'].includes(f.type)?'number':f.type==='Date'?'date':'text')+'" '+(f.type==='Decimal'?'step="any"':'')+'>')+(f.uiHelp?'<small class="quiet field-help">'+esc(f.uiHelp)+'</small>':'')+'</label>'}).join('')+'</div><div id="form-error" class="error"></div><footer><button type="button" id="cancel">取消</button><button class="primary">确认执行</button></footer></form></div>';$('#cancel').onclick=()=>$('#modal').innerHTML='';$('#edit').onsubmit=async e=>{e.preventDefault();const input=Object.fromEntries(new FormData(e.target));for(const f of fields){if(['Integer','Decimal','Number'].includes(f.type)&&input[f.name]!=='')input[f.name]=Number(input[f.name])}if(rid)input._version=r.version;try{await api('behaviors/'+encodeURIComponent(bid),{record_id:rid||null,input});$('#modal').innerHTML='';await load(en.id)}catch(x){$('#form-error').textContent=x.message}}}
async function reportView(id){currentReport=id;const r=meta.reports.find(x=>x.id===id);$('#workspace').innerHTML='<div class="panel"><div class="toolbar"><h2>'+esc(r.name)+'</h2></div><form id="filters"><div class="fields">'+(r.parameters||[]).map(p=>'<label>'+esc(p.label||p.name)+'<input name="'+esc(p.name)+'" value="'+esc(p.defaultValue??'')+'" '+(p.required?'required':'')+'></label>').join('')+'</div><button style="margin:16px 0" class="primary">查询</button></form><div id="results"></div></div>';async function fetchPage(page=1){try{const f=Object.fromEntries([...new FormData($('#filters'))].filter(([k,v])=>v!=='')),data=await api('reports/'+encodeURIComponent(id),{filters:f,page});$('#results').innerHTML='<div class="scroll"><table><thead><tr>'+data.columns.map(c=>'<th>'+esc(c.label||c.name)+'</th>').join('')+'</tr></thead><tbody>'+data.rows.map(row=>'<tr>'+data.columns.map(c=>'<td>'+esc(row[c.name])+'</td>').join('')+'</tr>').join('')+'</tbody></table></div><div class="toolbar" style="margin-top:16px"><span class="quiet">共 '+data.total+' 行 · 第 '+data.page+' 页 · 当前页 '+data.rows.length+' 行</span>'+(data.pagination_enabled?'<div class="actions"><button id="report-prev" '+(data.page<=1?'disabled':'')+'>上一页</button><button id="report-next" '+(!data.has_next?'disabled':'')+'>下一页</button></div>':'')+'</div>';if(data.pagination_enabled){$('#report-prev').onclick=()=>fetchPage(data.page-1);$('#report-next').onclick=()=>fetchPage(data.page+1)}}catch(x){err(x)}}$('#filters').onsubmit=async e=>{e.preventDefault();await fetchPage(1)};await fetchPage(1)}
async function tasksView(){try{const data=await api('tasks');$('#workspace').innerHTML='<div class="panel"><h2>审批待办</h2>'+data.tasks.map(t=>'<div class="toolbar"><span>'+esc(t.task.name)+' · '+esc(t.record_id.slice(0,8))+'</span><button data-task="'+esc(t.entity_id)+'">打开业务记录</button></div>').join('')+(data.tasks.length?'':'<div class="empty">当前角色暂无待办</div>')+'</div>';document.querySelectorAll('[data-task]').forEach(b=>b.onclick=()=>load(b.dataset.task))}catch(e){err(e)}}start().catch(err);
</script>
</body>
</html>'''

APP_AI_HTML = r'''
<script>
function mountAssistant(){const main=$('main');if(!main||!meta?.actor||$('.assistant-pane'))return;const pane=document.createElement('aside');pane.className='assistant-pane';pane.innerHTML='<h2>业务助手</h2><div class="assistant-messages" id="assistant-messages"><p class="quiet">可询问业务记录、统计结果，或请助手打开对应页面。</p></div><form id="assistant-form"><elastic-textarea><textarea id="assistant-input" rows="1" aria-label="业务问题" maxlength="6000" placeholder="输入业务问题…" required></textarea></elastic-textarea><button class="primary" id="assistant-send">发送</button></form>';main.append(pane);$('#assistant-input').addEventListener('keydown',event=>{
 if(event.key==='Enter'&&!event.shiftKey&&!event.isComposing){event.preventDefault();event.target.form.requestSubmit();}
});$('#assistant-form').onsubmit=async event=>{event.preventDefault();const input=$('#assistant-input'),question=input.value.trim(),send=$('#assistant-send');if(!question||send.disabled)return;const area=$('#assistant-messages');const user=document.createElement('div');user.className='assistant-message user';user.textContent=question;area.append(user);input.value='';input.parentElement.update(input);send.disabled=true;send.textContent='正在查询…';try{const response=await api('chat',{question,entity_id:selected});const message=document.createElement('div');message.className='assistant-message';const text=document.createElement('div');text.textContent=response.reply;message.append(text);if(response.query){const details=document.createElement('details');const summary=document.createElement('summary');summary.textContent='查询依据 · '+response.query.rows.length+' 行';details.append(summary);const pre=document.createElement('pre');pre.textContent=response.query.sql+'\n\n'+JSON.stringify(response.query.rows,null,2);details.append(pre);message.append(details)}if(response.navigate){const b=document.createElement('button');b.textContent='打开对应页面';b.onclick=()=>response.navigate.entity_id?load(response.navigate.entity_id):reportView(response.navigate.report_id);message.append(b)}if(response.pending_action){const action=response.pending_action;const b=document.createElement('button');b.textContent='核对并确认：'+action.name;b.onclick=async()=>{await load(action.entity_id);edit(action.behavior_id,action.record_id,action.input)};message.append(b)}area.append(message)}catch(error){const message=document.createElement('div');message.className='assistant-message error';message.textContent=error.message;area.append(message)}finally{send.disabled=false;send.textContent='发送';area.scrollTop=area.scrollHeight}}}
</script>'''
