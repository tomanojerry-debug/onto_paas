"""Explicit, reviewable model repairs; no model repair executes business effects."""
import copy
import json
from . import analysis, business


def business_repair(project, llm, feedback=''):
    models = project.get('models') or {}
    errors = business.validate_models(models)
    if not errors:
        errors.extend(business.validate_model_baseline(models, project.get('requirements')))
    from .builder import compile_spec
    try:
        compile_spec(project, require_ui_traceability=bool(project.get('prototype')))
    except ValueError as error:
        errors.append(str(error))
    if feedback:
        errors.append('用户校核意见：' + feedback)
    return business.repair_models(project, llm, errors)


def analysis_repair(project, llm, feedback=''):
    source = project.get('source') or {}
    schema = source.get('schema')
    if not schema:
        raise ValueError('请先连接当前数据源')
    # Re-inspect the exact snapshot before asking the model to alter a mapping.
    current = analysis.inspect_database(source['path'])
    if current['sha256'] != source.get('sha256'):
        raise ValueError('来源快照已变化，请重新连接并确认需求')
    models = copy.deepcopy(project.get('models') or {})
    errors = analysis.validate_models(models, schema)
    for attempt in range(2):
        result = llm.json('校核修复当前数据分析本体，严格保留已确认业务需求与真实物理Schema。'
            '只能修映射/引用/结构错误，不能编造表字段、改变统计口径或引入示例业务。'
            '返回JSON {"replacements":{"需修改的原文件名":完整模型对象},"explanation":"改动原因"}。'
            '只返回确有变更的文件；其他文件保持原样。',
            json.dumps({'requirements': project.get('analysis_requirements'), 'schema': schema,
                        'models': models, 'errors': errors, 'feedback': feedback}, ensure_ascii=False),
            purpose=f'analysis.repair.{attempt+1}',max_tokens=14000)
        replacements = result.get('replacements') if isinstance(result,dict) else None
        if not isinstance(replacements,dict) or not replacements:
            raise ValueError('分析模型修复没有返回明确文件差异')
        if set(replacements) - set(models) or any(not isinstance(v,dict) for v in replacements.values()):
            raise ValueError('修复包含未知文件或无效模型')
        models.update(replacements)
        errors = analysis.validate_models(models,schema)
        if not errors:
            return models
    raise ValueError('分析模型修复仍未通过校验：' + '; '.join(errors[:15]))
