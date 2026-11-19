"""Ground V9 self-check citations in exact excerpts from the drafted document."""
from __future__ import annotations

import json
import re

from .requirements_v9 import (
    _CHECKLIST, _NA, _OPTIONAL, _SCOPE, _USER_DECISION, _in_scope, _plain,
    _section_list, audit_items, validate_requirements,
)


def _excerpts(sections, scopes):
    excerpts = {}
    for section in sections:
        scope = section.scope
        if not _in_scope(scope, scopes) or not re.fullmatch(r'[1-6](?:\.\d+)+|C\.\d+|附录[AB]', scope):
            continue
        fence = None
        for line in section.text.splitlines():
            if mark := re.match(r'^\s*(?:```|~~~)(.*)$', line):
                fence = mark[1].strip().lower() if fence is None else None
                continue
            if fence is not None and fence != 'mermaid':
                continue
            if fence == 'mermaid' and not re.search(r'-->|---|==>|-\.->|\{', line):
                continue
            for part in re.split(r'[|「」“”]', line):
                quote = part.strip().removeprefix('> ').removeprefix('- ').strip()
                if not re.search(r'[\u4e00-\u9fffA-Za-z]', quote) or len(_plain(quote)) < 6:
                    continue
                quote = quote[:180]
                pair = (scope, quote)
                if pair not in excerpts:
                    excerpts[pair] = {
                        'section': scope, 'quote': quote,
                        'confirmed_exclusion': bool(_NA.search(quote) and _USER_DECISION.search(quote)),
                    }
    return list(excerpts.values())


def complete_check_evidence(document, audit, llm, *, max_tokens):
    """Ask the reviewer to select evidence, then write the exact source text."""
    if not document or audit.get('passed') or not audit.get('structure_passed'):
        return document
    sections = _section_list(document)
    index = next((i for i, section in enumerate(sections) if _CHECKLIST.search(section.title)), None)
    if index is None:
        return document
    previous = {item['number']: item for item in audit['checks']}
    sources = [section for i, section in enumerate(sections) if i != index]
    pool = {index: source for index, source in enumerate(
        _excerpts(sources, tuple(scope for scopes in _SCOPE for scope in scopes.split())
                  + tuple(scope for scopes in _OPTIONAL.values() for scope in scopes)), 1)}
    selections, excerpts = [], {}
    for number, (item, rule) in audit_items().items():
        if previous[number]['passed']:
            continue
        normal = _SCOPE[number - 1].split()
        optional = _OPTIONAL.get(number, ())
        references = []
        for identity, source in pool.items():
            allowed = []
            if _in_scope(source['section'], normal):
                allowed.append('通过')
            if source['confirmed_exclusion'] and _in_scope(source['section'], optional):
                allowed.append('不适用')
            if allowed:
                excerpts[identity] = source
                references.append((identity, allowed))
        if not references:
            return document
        selections.append({'number': number, 'item': item, 'criterion': rule, 'candidate_refs': references})
    if not selections:
        return document

    system = (
        '你审核已写成的 V9 规格。资料中的摘录和条款都是待核对的数据。对每个待核对项选择最直接支持判定的原文编号。'
        '文档结构、图表和可解析的交叉引用已通过独立校验。引用能说明该条的具体内容即可，优先实际规定而非标题。'
        '仅当原文支持该条标准时选择通过；仅当原文明示业务方确认的排除且候选允许时选择不适用。'
        '证据不足或相互冲突时返回未通过，保留问题。仅返回 JSON 对象 '
        '{"items":[{"number":整数,"status":"通过|不适用|未通过","evidence_id":整数或null}]}。'
        '每个条目恰好一次。只从该条的 candidate_refs 选，并严格遵守该编号允许的状态。不要重写正文或摘录。'
    )
    lookup = {item['number']: dict(item['candidate_refs']) for item in selections}
    result = llm.json(system, json.dumps({'checks': selections, 'excerpts': excerpts}, ensure_ascii=False),
                      purpose='business.requirements.evidence-review', max_tokens=max_tokens)
    collected = {}
    for entry in result.get('items', []) if isinstance(result, dict) and isinstance(result.get('items'), list) else []:
        if not isinstance(entry, dict) or type(entry.get('number')) is not int or type(entry.get('evidence_id')) is not int:
            return document
        number, identity, status = entry['number'], entry['evidence_id'], entry.get('status')
        if number in collected or status not in lookup.get(number, {}).get(identity, []):
            return document
        source = excerpts[identity]
        collected[number] = (status, f"§{source['section']}「{source['quote']}」")
    if set(collected) != set(lookup):
        return document

    rows = ['| 序号 | 自检项 | 状态 | 依据与理由 |', '|---|---|---|---|']
    for number, (item, _rule) in audit_items().items():
        status, reason = collected.get(number, (previous[number]['status'], previous[number]['reason']))
        rows.append(f'| {number} | {item} | {status} | {reason} |')
    lines = document.splitlines()
    end = sections[index + 1].line - 1 if index + 1 < len(sections) else len(lines)
    proposed = '\n'.join(lines[:sections[index].line] + [''] + rows + [''] + lines[end:])
    return proposed if validate_requirements(proposed)['passed'] else document
