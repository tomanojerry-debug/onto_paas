"""Evidence and modeling baselines for ordinary V9 requirements documents."""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

_UPSTREAM = Path(__file__).with_name("upstream")
_REFERENCE = _UPSTREAM / "ontology-driven-dev/references/AI需求探索与确认提示词V9.0.md"
_AUTHOR_MANIFEST = _UPSTREAM / "contract_reference/source-sha256.json"
_AUTHOR_FILE = "reference-example/合同管理需求规格说明书-V9.md"
UNRESOLVED = re.compile(r"\[(?:待确认|待澄清|未确认)\]")
_NO_PENDING_ITEMS = re.compile(r"(?:^|[。；])\s*附录\s*B\s*无\s*`\[待确认\]`\s*条目(?=[。；.!！]|$)", re.M)
_ID = re.compile(r"(?<![A-Za-z0-9-])(?:PROC-APP|B-APP|FUNC|PROC(?!-APP\b)|ROLE|REP|REF|INV|OBJ|AGG|UI|B(?!-APP\b)|Q|R)-[A-Z0-9][A-Z0-9_-]*(?![A-Za-z0-9-])")
_SECTION = re.compile(r"^\s*((?:[1-6]|[Cc])(?:\.\d+)+)(?![\d.])")
_EVIDENCE = re.compile(r"(?:§\s*)?((?:[1-6]|[Cc])(?:\.\d+)+|附录\s*[AB])\s*[:：]?\s*[「“]([^」”\n]+)[」”]")
_CHECKLIST = re.compile(r"需求完整性自检|47\s*项.*自检|自检.*47\s*项")
_NA = re.compile(r"(?:不适用|无此需求|不纳入|不涉及|无需|不存在|无遗留问题)")
_USER_DECISION = re.compile(r"(?:用户|业务方)\s*(?:已)?(?:明确)?确认")
_SECTION_FOR_MODEL = {"OBJECT": "C.1", "BEHAVIOR": "C.2", "RULE": "C.3", "ACTOR": "C.4",
                      "FLOW": "C.5", "REPORT": "C.6", "UI": "C.7"}
_PREFIXES = {
    1: ("OBJ-", "AGG-"), 2: ("FUNC-", "B-", "Q-"), 3: ("R-",),
    4: ("ROLE-",), 5: ("PROC-",), 6: ("REP-",), 7: ("UI-",),
}
_CORE_SECTIONS = ("1.1", "1.2", "1.3", "1.4", "1.5", "1.6",
                  "2.1.1", "2.1.2", "2.1.3", "2.2", "2.3.1", "2.3.2", "2.3.3", "2.3.4",
                  "3", "4.1", "4.2", "4.3", "4.4", "5", "6.1", "6.2", "附录A", "附录B", "附录C", "修订记录")

# Give each self-check a relevant evidence scope. A quotation from the audit
# itself, a placeholder citation or a real quote from a different topic fails.
_SCOPE = (
    "1.1", "1.2", "2 3", "1.3", "1.4", "1.4", "1.4", "1.5 1.6", "2.1.1", "2.1.2",
    "2.1.3 6", "2.2", "2.2", "2.2", "2 4 5", "2.3.1", "2.3.2", "2.3.3", "3", "3 2.3",
    "3", "3", "3", "3", "4.1", "4.1", "4.1", "3 4", "3 4", "4.2", "4.3 4.4", "4.3 4.4 6",
    "4.2 4.3 4.4", "5", "5 C.6", "5 C.2 C.6", "6", "附录B", "C.1 C.2 C.3 C.4 C.5 C.6 C.7",
    "C.8", "C.9", "C.9", "C.7", "C.7", "C.7", "C.7", "C.7",
)
_OPTIONAL = {
    13: ("4.1",), 14: ("2.3.2", "2.3.3"), 16: ("2.3.1",), 17: ("2.3.2",), 18: ("2.3.3",),
    20: ("2.3.1", "2.3.2"), 21: ("3",), 22: ("3",), 24: ("3",), 25: ("4.1",), 26: ("4.1",),
    27: ("4.1",), 28: ("3", "4.1"), 29: ("3", "4.1"), 31: ("4.3",), 32: ("4.3",),
    34: ("5",), 35: ("5",), 36: ("5",), 40: ("4.1", "C.8"),
    43: ("C.7",), 44: ("C.7",), 45: ("C.7",), 46: ("4.3", "C.7"), 47: ("C.7",),
}


def audit_evidence_scopes():
    """Expose the actual citation policy to the model authoring the audit."""
    return {number: {"通过时可援引": scope.split(), "不适用时可援引": _OPTIONAL.get(number, ())}
            for number, scope in enumerate(_SCOPE, 1)}


def rows(text):
    fence = ""
    for line in text.splitlines():
        marker = re.match(r"^\s*(`{3,}|~{3,})", line)
        if marker:
            if not fence:
                fence = marker[1]
            elif marker[1].startswith(fence) and not line[marker.end():].strip():
                fence = ""
        elif not fence and line.lstrip().startswith("|"):
            yield [cell.strip().strip("*") for cell in re.split(r"(?<!\\)\|", line.strip().strip("|"))]


@lru_cache(maxsize=1)
def audit_items():
    text = _REFERENCE.read_text(encoding="utf-8").split("## 12 需求完整性自检清单（V9.0）", 1)[1]
    items = {}
    for cells in rows(text):
        if len(cells) >= 3 and cells[0].isdigit() and 1 <= int(cells[0]) <= 47:
            items.setdefault(int(cells[0]), (cells[1], cells[2]))
    if set(items) != set(range(1, 48)):
        raise ValueError("作者 V9 自检清单已缺失或损坏")
    return items


@lru_cache(maxsize=1)
def _author_sha():
    return json.loads(_AUTHOR_MANIFEST.read_text(encoding="utf-8"))["files"][_AUTHOR_FILE]


@dataclass
class Section:
    level: int
    title: str
    line: int
    text: str
    parent_scope: str = ""

    @property
    def key(self):
        if match := _SECTION.match(self.title):
            return match[1].upper()
        for label in "ABC":
            if re.match(rf"附录\s*{label}\b|附录\s*{label}(?=[\u4e00-\u9fff])", self.title, re.I):
                return "附录" + label
        if "修订记录" in self.title:
            return "修订记录"
        return next((str(i) for i, digit in enumerate("一二三四五六", 1)
                     if re.match(rf"第\s*{digit}\s*部分", self.title)), "")

    @property
    def scope(self):
        return self.key or self.parent_scope


def _section_list(document):
    sections = []
    level = None
    current = []
    inside_fence = ""
    for number, line in enumerate(document.splitlines(), 1):
        if fence := re.match(r"^\s*(`{3,}|~{3,})", line):
            marker = fence[1]
            if not inside_fence:
                inside_fence = marker
            elif marker.startswith(inside_fence) and not line[fence.end():].strip():
                inside_fence = ""
        heading = re.match(r"^(#{1,6})[ \t]+(.+?)\s*$", line) if not inside_fence else None
        if heading:
            if level:
                sections.append(Section(level, title, start, "\n".join(current)))
            level, title, start, current = len(heading[1]), heading[2].strip(" #"), number, []
        elif level:
            current.append(line)
    if level:
        sections.append(Section(level, title, start, "\n".join(current)))
    parents = []
    for section in sections:
        while parents and parents[-1].level >= section.level:
            parents.pop()
        section.parent_scope = parents[-1].scope if parents else ""
        parents.append(section)
    return sections


def _content(sections, key):
    return "\n".join(section.title + "\n" + section.text for section in sections
                     if _in_scope(section.scope, (key,)))


def _plain(text):
    return re.sub(r"\s+", "", text.replace("`", "").replace("**", ""))


def _in_scope(key, scopes):
    return any(key == scope or key.startswith(scope + ".") for scope in scopes)


def _proofs(reason, sections, scopes):
    evidence = []
    for scope, quote in _EVIDENCE.findall(reason):
        scope = re.sub(r"\s+", "", scope).upper()
        quote = _plain(quote)
        candidates = [s for s in sections if _in_scope(s.scope, (scope,))]
        if len(quote) >= 6 and _in_scope(scope, scopes):
            for section in candidates:
                if quote in _plain(section.text):
                    evidence.append({"section": section.scope, "line": section.line, "quote": quote})
                    break
    return evidence


def _declarations(text, prefixes):
    definitions = []
    for cells in rows(text):
        if len(cells) < 2:
            continue
        match = _ID.search(cells[0])
        if match and match[0].startswith(prefixes) and _plain(cells[0]).startswith(match[0]):
            definitions.append((match[0], cells))
    return definitions


def _figures(sections):
    figures, errors = [], []
    for section in sections:
        openings = list(re.finditer(r"(?mi)^\s*(`{3,}|~{3,})mermaid[ \t]*$", section.text))
        for opening in openings:
            close = re.search(rf"(?m)^\s*{re.escape(opening[1])}[ \t]*$", section.text[opening.end():])
            if not close:
                errors.append(f"{section.key or section.title} 的 Mermaid 代码块未闭合")
                continue
            code = section.text[opening.end():opening.end() + close.start()]
            lines = [line.strip() for line in code.splitlines() if line.strip() and not line.lstrip().startswith("%%")]
            if not lines:
                errors.append(f"{section.key or section.title} 的 Mermaid 图为空")
                continue
            header = lines[0]
            drawing = "\n".join(lines[1:])
            if re.fullmatch(r"flowchart (?:LR|TD)", header):
                substantive = re.search(r"(?:-->|---|==>|-\.->)", drawing)
            elif header == "erDiagram":
                substantive = re.search(r"\b[A-Za-z][A-Za-z0-9_]*\s*(?:\{|[|o}{]{2}(?:--|\.\.))", drawing)
            elif header == "stateDiagram-v2":
                substantive = " --> " in drawing or re.search(r"\s*-->\s*", drawing)
            else:
                substantive = False
            if not substantive or "usecaseDiagram" in drawing:
                errors.append(f"{section.key or section.title} 的 Mermaid 图缺有效图形声明或节点关系")
                continue
            identifiers = set(_ID.findall(section.title + "\n" + drawing))
            if not section.key and _in_scope(section.scope, ("1.4",)) and header.startswith("flowchart "):
                parents = [candidate for candidate in sections if candidate.key == section.scope]
                if len(parents) == 1:
                    flow_ids = {identifier for identifier in _ID.findall(parents[0].title)
                                if identifier.startswith("PROC-") and not identifier.startswith("PROC-APP-")}
                    if len(flow_ids) == 1:
                        identifiers.update(flow_ids)
            approval_scope = _in_scope(section.scope, ("4.3",)) or "审批流" in section.title
            if (len(openings) == 1 and approval_scope and header.startswith("flowchart ")
                    and not any(identifier.startswith("PROC-APP-") for identifier in identifiers)):
                preceding = {identifier for identifier in _ID.findall(section.text[:opening.start()])
                             if identifier.startswith("PROC-APP-")}
                if len(preceding) == 1:
                    identifiers.update(preceding)
            figures.append({"section": section.scope, "line": section.line, "kind": header.split()[0],
                            "text": drawing, "ids": identifiers})
    return figures, errors


def _baseline(document_sections, appendix_sections):
    entries, blocks, errors = {}, {}, []
    for n in range(1, 10):
        key = f"C.{n}"
        top = [s for s in appendix_sections if s.key == key]
        content = _content(appendix_sections, key)
        if len(top) != 1 or not any(s.text.strip() for s in appendix_sections if _in_scope(s.scope, (key,))):
            errors.append(f"附录 C 缺少唯一、非空的 {key}")
        blocks[key] = content
        if n not in _PREFIXES:
            continue
        declared = _declarations(content, _PREFIXES[n])
        for identifier, cells in declared:
            if identifier in entries:
                errors.append(f"附录 C 重复定义稳定编号 {identifier}")
            entries[identifier] = {"section": key, "definition": cells[0], "details": cells[1:],
                                   "references": sorted(set(_ID.findall(" ".join(cells[1:]))))}
        if not declared and (n in (1, 2, 4, 5) or not (_NA.search(content) and _USER_DECISION.search(content))):
            errors.append(f"{key} 缺少带稳定编号的模型条目或经确认的不适用说明")
    body_text = "\n".join(s.title + "\n" + s.text for s in document_sections)
    body_ids = set(_ID.findall(body_text))
    refs = set(entries)
    errors.extend(f"附录 C 条目 {identifier} 在需求正文或界面章节中无对应编号" for identifier in sorted(refs - body_ids))
    errors.extend(f"正文编号 {identifier} 在附录 C 无对应条目" for identifier in sorted(body_ids - refs)
                  if not identifier.startswith(("REF-", "INV-")))
    known = refs | {identifier for identifier in body_ids if identifier.startswith(("REF-", "INV-"))}
    for identifier, entry in entries.items():
        errors.extend(f"附录 C {identifier} 引用未定义的编号 {ref}" for ref in entry["references"] if ref not in known)
    report_to_query = {}
    for identifier, entry in entries.items():
        if entry["section"] == "C.6":
            queries = [ref for ref in entry["references"] if ref.startswith("Q-")]
            if len(queries) != 1:
                errors.append(f"C.6 {identifier} 必须唯一绑定一个 Q 行为")
            else:
                report_to_query[identifier] = queries[0]
                query = entries.get(queries[0], {})
                if identifier not in query.get("references", []):
                    errors.append(f"C.2 {queries[0]} 缺少对 {identifier} 的反向引用")
    query_ids = {identifier for identifier in entries if identifier.startswith("Q-")}
    if sorted(report_to_query.values()) != sorted(query_ids):
        errors.append("附录 C REP 与 Q 未形成双向一对一")
    return {"entries": entries, "sections": blocks, "report_to_query": report_to_query}, errors


_NODE = r"([A-Za-z][A-Za-z0-9_]*)\s*(?:\[[^\n]*?\]|\([^\n]*?\)|\{[^\n]*?\})?"
_FLOW_EDGE = re.compile(rf"(?m)^\s*{_NODE}\s*(?:-->|==>|-\.->)\s*(?:\|[^|\n]+\|)?\s*{_NODE}")
_LABELED_NODE = re.compile(r'\b([A-Za-z][A-Za-z0-9_]*)\s*[\[({]+\s*"?((?:ROLE|FUNC|B-APP|Q)-[A-Z0-9_-]+)')


def _role_usecase_pairs(figures):
    pairs = set()
    for figure in figures:
        identifiers = dict(_LABELED_NODE.findall(figure["text"]))
        for source, target in _FLOW_EDGE.findall(figure["text"]):
            role, behavior = identifiers.get(source, ""), identifiers.get(target, "")
            if role.startswith("ROLE-") and behavior.startswith(("FUNC-", "B-APP-", "Q-")):
                pairs.add((role, behavior))
    return pairs


def _permission_pairs(body, entries):
    matrix = list(rows(_content(body, "6.2")))
    behavior_prefixes = ("FUNC-", "B-APP-", "Q-")
    by_role = _declarations(_content(body, "6.2"), ("ROLE-",))
    if by_role:
        return {(role, behavior) for role, cells in by_role for behavior in _ID.findall(" ".join(cells[1:]))
                if behavior.startswith(behavior_prefixes)}
    role_labels = {_plain(value["definition"]).removeprefix(role): role
                   for role, value in entries.items() if role.startswith("ROLE-")}
    columns = []
    pairs = set()
    for cells in matrix:
        if not cells:
            continue
        identifiers = _ID.findall(cells[0])
        if not identifiers and "行为" in cells[0]:
            columns = [next((identifier for identifier in _ID.findall(cell) if identifier.startswith("ROLE-")),
                            role_labels.get(_plain(cell))) for cell in cells[1:]]
        elif columns and identifiers[0].startswith(behavior_prefixes):
            pairs.update((role, identifiers[0]) for role, grant in zip(columns, cells[1:])
                         if role and re.match(r"(?:允许|有权|仅本人|仅本部门|√|✅)", grant))
    return pairs


def _content_checks(body, baseline):
    entries = baseline["entries"]
    figures, errors = _figures(body)
    section_issues = {}
    if errors:
        section_issues[42] = errors
    def require(number, condition, message):
        if not condition:
            section_issues.setdefault(number, []).append(message)
    by_kind = lambda kind: [f for f in figures if f["kind"] == kind]
    indexed_figures = {f["section"] for f in figures}
    index_rows = list(rows(baseline["sections"]["C.9"]))
    index = {match[0] for cells in index_rows if len(cells) >= 3
             if (match := re.match(r"[1-6](?:\.\d+)+(?![.\d])(?=$|[\s（(；;、，,])", cells[1].strip("`§ ")))}
    require(41, bool(indexed_figures) and indexed_figures <= index and index <= indexed_figures,
            "C.9 必须逐一索引实际可定位的 Mermaid 图章节")
    usecase = [f for f in by_kind("flowchart") if f["section"] == "2.1.2"]
    role_pairs = _role_usecase_pairs(usecase)
    expected_pairs = {(identifier, behavior)
                      for identifier, entry in entries.items() if entry["section"] == "C.4"
                      for behavior in entry["references"] if behavior.startswith(("FUNC-", "B-APP-", "Q-"))}
    require(10, bool(usecase) and bool(role_pairs), "2.1.2 须提供带角色和行为稳定编号的用例连线")
    matrix_pairs = _permission_pairs(body, entries)
    require(11, bool(expected_pairs) and expected_pairs == role_pairs == matrix_pairs,
            "角色用例图的实际连线、6.2 权限矩阵和附录 C.4 授权行为不一致")
    require(37, bool(expected_pairs) and expected_pairs == matrix_pairs,
            "6.2 权限矩阵缺少可按稳定编号定位的完整角色与行为授权")
    require(23, any(f["section"].startswith("3.") for f in by_kind("erDiagram")), "第三部分缺少整体 Mermaid ER 图")
    status_required = any(re.search(r"初始状态|初始态|状态迁移", s.title + s.text) and not _NA.search(s.text)
                          for s in body if s.scope.startswith("3."))
    if status_required:
        require(28, bool(by_kind("stateDiagram-v2")), "有状态对象必须提供 stateDiagram-v2")
    for identifier, entry in entries.items():
        if entry["section"] != "C.5":
            continue
        approval = identifier.startswith("PROC-APP-")
        candidates = [f for f in by_kind("flowchart")
                      if f["section"].startswith("4." if approval else "1.4.")
                      and identifier in f["ids"]]
        require(31 if approval else 6, len(candidates) == 1,
                f"{identifier} 须有独立且可按编号定位的 Mermaid 流程图")
        if len(candidates) == 1 and not approval:
            figure = candidates[0]
            table_text = " ".join(" ".join(cells) for cells in rows(_content(body, figure["section"])))
            step_ids = {step for step in _ID.findall(table_text)
                        if step.startswith(("FUNC-", "B-", "Q-", "PROC-", "R-"))}
            require(7, bool(step_ids) and step_ids <= figure["ids"],
                    f"{identifier} 的步骤表缺少编号，或表中行为、流程与规则未反映在对应流程图")
    diagram_ids = set.union(set(), *(figure["ids"] for figure in figures))
    require(42, diagram_ids <= set(entries) | set(_ID.findall(_content(body, "2.3"))) |
            set(_ID.findall(_content(body, "3"))), "Mermaid 图引用了正文与附录 C 均未定义的编号")
    require(2, all(label in _content(body, "1.2") for label in ("范围内", "范围外")), "1.2 须同时写明范围内和范围外")
    require(38, bool(re.search(r"无(?:遗留|待确认|待澄清)问题|不存在待确认状态", _content(body, "附录B"))),
            "附录 B 须明确说明无待确认遗留问题")
    require(40, len(list(rows(baseline["sections"]["C.8"]))) >= 3 or _NA.search(baseline["sections"]["C.8"]),
            "C.8 须保留跨对象联动矩阵，或写明经确认不适用")
    return section_issues


def unresolved_requirement_lines(document, *, sections=None):
    lines = [line.strip() for line in document.splitlines() if UNRESOLVED.search(line)]
    if len(lines) != 1 or len(UNRESOLVED.findall(lines[0])) != 1:
        return lines
    sections = _section_list(document) if sections is None else sections
    closed = any(section.scope == "附录B" and "无遗留问题" in section.text
                 and _NO_PENDING_ITEMS.search(section.text) for section in sections)
    return [] if closed else lines


def validate_requirements(document):
    """Verify the author's checklist against local evidence and return the C baseline."""
    if not isinstance(document, str):
        return {"passed": False, "structure_passed": False, "errors": ["需求文档必须为文本"], "checks": []}
    digest = hashlib.sha256(document.encode("utf-8")).hexdigest()
    author_original = digest == _author_sha()
    sections = _section_list(document)
    audit_index = next((i for i, section in enumerate(sections) if _CHECKLIST.search(section.title)), None)
    audit = sections[audit_index] if audit_index is not None else None
    body = [s for i, s in enumerate(sections) if i != audit_index]
    appendix_idx = next((i for i, s in enumerate(body) if s.key == "附录C"), None)
    appendix = []
    if appendix_idx is not None:
        for section in body[appendix_idx + 1:]:
            if section.scope.startswith("C."):
                appendix.append(section)
            elif section.key or section.level <= body[appendix_idx].level:
                break
    document_sections = [s for s in body if not s.scope.startswith("C.")]
    errors = []
    unresolved = unresolved_requirement_lines(document, sections=sections)
    if unresolved:
        count = sum(len(UNRESOLVED.findall(line)) for line in unresolved)
        errors.append(f"文档仍有{count}处待确认标记")
    if author_original:
        baseline, issues = None, {}
    else:
        for target in _CORE_SECTIONS:
            covering = [s for s in document_sections if _in_scope(s.scope, (target,))]
            if not covering:
                errors.append(f"缺少 V9 章节 {target}")
            elif target != "附录C" and not any(s.text.strip(" -\n") for s in covering):
                errors.append(f"V9 章节 {target} 缺少内容")
        baseline, problems = _baseline(document_sections, appendix)
        errors.extend(problems)
        issues = _content_checks(document_sections, baseline)
    structure_passed = not errors and not any(issues.values())
    if not audit:
        errors.append("缺少独立的需求完整性自检 47 项报告")
    check_rows = {}
    for cells in rows(audit.text if audit else ""):
        if len(cells) < 4 or not cells[0].isdigit() or not 1 <= int(cells[0]) <= 47:
            continue
        number = int(cells[0])
        if number in check_rows:
            errors.append(f"自检项{number}重复，需保留唯一评审结果")
        check_rows[number] = cells
    checks = []
    for number, (item, criterion) in audit_items().items():
        cells = check_rows.get(number)
        if cells is None:
            checks.append({"number": number, "item": item, "criterion": criterion, "status": "缺失",
                           "passed": False, "reason": "未找到自检结果", "evidence": []})
            continue
        status = cells[2].replace("✅", "通过").replace("❌", "未通过")
        reason = " | ".join(cells[3:])
        evidence = []
        diagnostics = list(issues.get(number, ()))
        if author_original:
            passed = status == "通过" and bool(reason)
        else:
            if _plain(cells[1]) != _plain(item):
                diagnostics.append("自检项名称与作者 V9 固定清单不符")
            allowed = _OPTIONAL.get(number, ()) if status == "不适用" else _SCOPE[number - 1].split()
            evidence = _proofs(reason, body, allowed)
            if not evidence:
                diagnostics.append("缺少与对应章节一致的证据短引（例如 §1.2「正文原文」）")
            if status == "不适用" and not any(_NA.search(value["quote"]) and _USER_DECISION.search(value["quote"])
                                             for value in evidence):
                diagnostics.append("不适用项须引证该范围实际经用户确认的原文")
            if status not in {"通过", "不适用"}:
                diagnostics.append("自检结果尚未通过")
            passed = not diagnostics
        checks.append({"number": number, "item": item, "criterion": criterion, "status": status, "passed": passed,
                       "reason": reason, "evidence": evidence, "diagnostics": diagnostics})
    if baseline and not any(entry["section"] == "C.7" for entry in baseline["entries"].values()):
        if any(check["status"] != "不适用" for check in checks[42:47]):
            errors.append("C.7 未提供界面条目时，第 43–47 项须逐项以已确认理由标注不适用")
    missing = [str(check["number"]) for check in checks if check["status"] == "缺失"]
    if missing:
        errors.append("47项自检缺少显式结果：" + ",".join(missing))
    failed = [str(check["number"]) for check in checks if not check["passed"] and check["status"] != "缺失"]
    if failed:
        errors.append("自检未通过或证据不足：" + ",".join(failed))
    return {"passed": not errors, "structure_passed": structure_passed, "errors": errors, "checks": checks, "baseline": baseline,
            "audit_standard": "v9-evidence-v1",
            "author_original": author_original, "human_approval_required": True, "document_sha256": digest}


MODEL_BASELINE_PROMPT = (
    "逐项使用验证过的附录 C 作为确定性输入。七份模型主清单中的每个实际对象、行为、规则、角色、流程、报表和屏幕"
    "必须写 requirementRefs:[对应附录 C 稳定编号]，保留确认的业务含义。每个附录 C 编号必须唯一映射在所属模型的实际条目。"
    "requirementRefs 只填当前条目自身在附录 C 同类清单第一列的编号，通常恰好一个。不得把同一行后续列出现的引用编号写进这个数组。"
    "例如 C.7 屏幕身份只写自身 UI 编号，关联 FUNC/Q/REP 使用 actionPoints.behaviorRef 或 queryReportRef；"
    "C.5 流程只写自身 PROC 编号，被调用审批流用 subFlowRef；C.2 行为的 R 规则用 appliedRules，C.6 报表的 Q 行为用 behaviorRef。"
    "C.1 子实体须在其模型条目记录自身编号，C.6 报表仍关联由 C.2 唯一 Q 编号映射的行为。"
    "C.7 用 UI 编号关联屏幕，产品原型决定对应页面、控件和操作；不得用无关条目填满编号。"
    "经用户明确确认原型不适用且 C.7 无 UI 编号时，从业务功能生成可用的最小 MU；此时自然没有对应的 UI requirementRefs。"
)


def model_baseline_diagnostics(models, baseline, model_files):
    """Compare Appendix C stable IDs and query links with actual model entries."""
    if not baseline:
        return ["业务模型需要已通过校验的附录 C 基线"]
    if not isinstance(models, dict):
        return ["业务模型包必须为对象"]
    expected = baseline["entries"]
    matched, errors = {}, []
    has_ui_baseline = any(value["section"] == "C.7" for value in expected.values())

    def visit(entries, kind):
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            identity = entry.get("id") or entry.get("roleId") or entry.get("screenId")
            refs = entry.get("requirementRefs")
            if kind == "UI" and not has_ui_baseline and refs in (None, []):
                continue
            if not isinstance(refs, list) or not refs or not all(isinstance(ref, str) for ref in refs):
                errors.append(f"{kind} {identity or '未命名条目'} 缺少有效的 requirementRefs")
                refs = []
            for ref in refs:
                if expected.get(ref, {}).get("section") != _SECTION_FOR_MODEL[kind]:
                    errors.append(f"{kind} {identity} 引用未知或归属不符的需求编号 {ref}")
                elif ref in matched:
                    errors.append(f"需求编号 {ref} 映射到多个模型条目")
                else:
                    matched[ref] = (identity, entry)
            if kind == "OBJECT" and isinstance(entry.get("entities"), list):
                visit(entry["entities"], kind)

    for filename, (kind, key) in model_files.items():
        document = models.get(filename, {})
        if not isinstance(document, dict):
            continue
        entries = document.get(key, document.get("entities", []) if kind == "OBJECT" else [])
        if isinstance(entries, list):
            visit(entries, kind)
    errors.extend(f"附录 C {ref} 未落实到对应模型" for ref in sorted(set(expected) - set(matched)))
    permissions = models.get("m5-actor-model.yaml")
    permission_list = permissions.get("permissions", []) if isinstance(permissions, dict) else []
    grants = {item.get("permissionId") or item.get("id"): item
              for item in permission_list or [] if isinstance(item, dict)}
    modeled_behaviors = {identity: element for ref, (identity, element) in matched.items()
                         if expected[ref]["section"] == "C.2"}
    for ref, (identity, element) in matched.items():
        entry = expected[ref]
        source_refs = [known for known in entry["references"] if known in expected]
        behavior_ids = {matched[known][0] for known in source_refs
                        if known in matched and expected[known]["section"] == "C.2"}
        object_ids = {matched[known][0] for known in source_refs
                      if known in matched and expected[known]["section"] == "C.1"}
        if entry["section"] == "C.2":
            if len(object_ids) == 1 and element.get("ownerEntity") not in object_ids:
                errors.append(f"行为 {ref} 的归属对象与附录 C 不一致")
            if ref.startswith("Q-") and element.get("behaviorType") != "QUERY":
                errors.append(f"行为 {ref} 必须落实为 QUERY")
        elif entry["section"] == "C.3" and behavior_ids:
            for behavior_id in behavior_ids:
                behavior = modeled_behaviors[behavior_id]
                rules = {value.get("ruleRef") if isinstance(value, dict) else value
                         for value in behavior.get("appliedRules", [])}
                rules.update(trigger.get("ruleRef") for trigger in behavior.get("syncTriggers", [])
                             if isinstance(trigger, dict))
                if identity not in rules:
                    errors.append(f"规则 {ref} 缺少由附录 C 确认的行为 {behavior_id} 调用")
        elif entry["section"] == "C.4" and behavior_ids:
            allowed = [grants.get(permission, {}) for permission in element.get("permissions", [])]
            actual = {grant.get("targetRef") for grant in allowed}
            if actual != behavior_ids:
                errors.append(f"角色 {ref} 的行为授权与附录 C 不一致")
            scopes = set(re.findall(r"dataScope\s*=\s*(ALL|OWN|DEPT|CUSTOM)\b", " ".join(entry["details"])))
            if len(scopes) == 1 and {grant.get("dataScope", "ALL") for grant in allowed} != scopes:
                errors.append(f"角色 {ref} 的数据权限范围与附录 C 不一致")
        elif entry["section"] == "C.5":
            flow_type = "APPROVAL" if ref.startswith("PROC-APP-") else "COLLABORATION"
            if element.get("flowType") != flow_type:
                errors.append(f"流程 {ref} 的类型与附录 C 不一致")
        elif entry["section"] == "C.7" and behavior_ids:
            points = element.get("actionPoints", element.get("actions", []))
            actual = {point.get("behaviorRef") for point in points if isinstance(point, dict) and point.get("behaviorRef")}
            if behavior_ids != actual:
                errors.append(f"屏幕 {ref} 的业务操作与附录 C 不一致")
    for report_ref, query_ref in baseline["report_to_query"].items():
        report_id, report = matched.get(report_ref, (None, {}))
        query_id, query = matched.get(query_ref, (None, {}))
        if report_id and query_id and (report.get("behaviorRef") != query_id or query.get("queryReportRef") != report_id):
            errors.append(f"模型报表 {report_ref} 与查询 {query_ref} 的双向引用和附录 C 不一致")
    return errors
