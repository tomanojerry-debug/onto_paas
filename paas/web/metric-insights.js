const pending = '待业务确认';
const uploadSource = '上传的业务指标定义';
const mappingSource = 'data_mapping.yaml 字段映射';
const list = (value) => Array.isArray(value) ? value : [];
const identifier = /^[\p{L}\p{N}][\p{L}\p{N}_.:-]{0,127}$/u;

const markdownSections = {
  指标定义: 'definition',
  指标定位与口径概述: 'positioning',
  定义与计算口径: 'formula_description',
  '计算参考 SQL': 'sql_template',
  来源表与字段依赖: 'source_dependencies',
  过滤规则: 'filter_rules',
  时间口径: 'time_scope',
  空值与异常值处理: 'quality_handling',
  限制与口径边界: 'limitations',
};

function display(value) {
  if (typeof value === 'string') return value.trim();
  if (typeof value === 'number' || typeof value === 'boolean') return String(value);
  if (Array.isArray(value)) return value.map(display).filter(Boolean).join('、');
  if (value && typeof value === 'object') {
    return Object.entries(value).map(([key, item]) => {
      const content = display(item);
      return content ? `${key} · ${content}` : '';
    }).filter(Boolean).join('\n');
  }
  return '';
}

function markdownMetric(text) {
  const outline = text.replace(/^(`{3,}|~{3,})[^\n]*\n[\s\S]*?^\1[ \t]*$/gm,
    (block) => block.replace(/[^\n]/g, ' '));
  const headings = [...outline.matchAll(/^##[ \t]+(.+?)[ \t]*$/gm)];
  const header = text.slice(0, headings[0]?.index ?? text.length);
  const ids = [...outline.matchAll(/^(?:[-*][ \t]+)?指标[ \t]*ID[ \t]*[:：][ \t]*(.+?)[ \t]*$/gmi)];
  if (ids.length !== 1 || ids[0].index >= header.length) {
    throw new Error('每份 Markdown 指标文档开头需要且只能有一行「指标 ID: MTR-TXN-001」');
  }
  const metric = {
    id: ids[0][1].replace(/^`([^`]+)`$/, '$1'),
    name: header.match(/^#[ \t]+(.+?)[ \t]*$/m)?.[1],
  };
  for (const [label, key] of [['别名', 'alias'], ['业务域', 'domain'], ['单位', 'unit'], ['计算类型', 'computation_type'], ['优先级', 'priority']]) {
    const match = header.match(new RegExp(`^(?:[-*][ \\t]+)?${label}[ \\t]*[:：][ \\t]*(.+)$`, 'm'));
    if (match) metric[key] = match[1].trim();
  }
  headings.forEach((heading, index) => {
    const key = Object.hasOwn(markdownSections, heading[1]) ? markdownSections[heading[1]] : null;
    if (!key) return;
    if (Object.hasOwn(metric, key)) throw new Error(`指标文档中存在重复小节 · ${heading[1]}`);
    const body = text.slice(heading.index + heading[0].length, headings[index + 1]?.index).trim();
    metric[key] = key === 'sql_template' ? body.replace(/^```(?:sql)?\s*\n([\s\S]*?)\n```$/i, '$1') : body;
  });
  if (!metric.name && !headings.some((heading) => Object.hasOwn(markdownSections, heading[1]))) {
    throw new Error('指标文档需要「# 指标名称」或一个指标口径小节');
  }
  return metric;
}

export function parseMetricDefinitions(text) {
  const content = String(text ?? '').trim();
  if (!content) throw new Error('指标定义文档为空');

  const fencedJson = content.match(/^```json\s*\n([\s\S]*?)\n```$/i)?.[1];
  let metrics;
  if (fencedJson || /^[{\[]/.test(content)) {
    let document;
    try {
      document = JSON.parse(fencedJson ?? content);
    } catch {
      throw new Error('指标定义 JSON 格式有误');
    }
    metrics = Array.isArray(document) ? document : document?.metrics;
  } else {
    metrics = [markdownMetric(content.replaceAll('\r\n', '\n'))];
  }

  if (!Array.isArray(metrics) || !metrics.length) throw new Error('指标定义需要包含至少一条指标');
  const ids = new Set();
  return metrics.map((metric) => {
    const id = typeof metric?.id === 'string' ? metric.id.trim() : '';
    if (!identifier.test(id)) throw new Error('每条指标都需要有效的指标 ID');
    if (ids.has(id)) throw new Error(`指标 ID 重复 · ${id}`);
    ids.add(id);
    return { ...metric, id };
  });
}

function getModel(project, current, legacy, collection) {
  const models = project?.models;
  const path = Array.isArray(models?.[current]?.[collection]) ? current : legacy;
  return { path, domain: models?.[path]?.domain, records: list(models?.[path]?.[collection]) };
}

function metricModel(project) {
  return getModel(project, 'm7-report-model.yaml', 'm_metric_model.yaml', 'metrics');
}

function byId(items) {
  return new Map(list(items).filter((item) => typeof item?.id === 'string' && item.id.trim())
    .map((item) => [item.id, item]));
}

export function metricDefinitions(project, businessDefinitions = []) {
  const selected = byId(metricModel(project).records);
  for (const [id, metric] of byId(businessDefinitions)) {
    if (!selected.has(id)) selected.set(id, metric);
  }
  return [...selected.values()];
}

function row(label, value, source) {
  const content = display(value);
  return { label, value: content || pending, ...(content && source ? { source } : {}) };
}

function fieldRows(label, fields, modelMetric, businessMetric, path) {
  const valueFrom = (metric) => fields.map((field) => [field, display(metric?.[field])])
    .find(([, value]) => value);
  const modeled = valueFrom(modelMetric);
  const documented = valueFrom(businessMetric);
  if (!modeled && !documented) return [row(label)];
  const rows = modeled ? [row(label, modeled[1], `${path} · ${modeled[0]}`)] : [];
  if (documented) {
    rows.push(row(modeled ? `${label}（业务文档）` : label, documented[1], `${uploadSource} · ${documented[0]}`));
  }
  return rows;
}

function sqlExcerptRows(label, pattern, templates) {
  return templates.flatMap(([sql, source]) => {
    const lines = sql.replace(/\/\*[\s\S]*?\*\//g, ' ').split('\n').map((line) => line.trim())
      .filter((line) => !line.startsWith('--') && pattern.test(line));
    return lines.length ? [row(label, lines.join('\n'), source)] : [];
  });
}

function mappedDependencyRows(entityIds, entities, bindings, modelSql, documentSql) {
  const rows = [];
  for (const id of entityIds) {
    const entity = entities.get(id);
    const mapping = bindings.get(id);
    const tables = new Set(list(mapping?.tables).map((item) => item?.table).filter(Boolean));
    const fields = list(mapping?.field_mappings).filter((item) => item?.db_table && item?.db_column);
    fields.forEach((item) => tables.add(item.db_table));
    if (tables.size) rows.push(row(`${display(entity?.name || mapping?.entity_name) || id} · 映射表`, [...tables], mappingSource));
    if (fields.length) {
      const names = [...new Set(fields.map((field) => `${field.db_table}.${field.db_column}`))];
      rows.push(row(`${id} · 实体映射字段`, names, mappingSource));
    }
  }
  for (const [sql, source] of [modelSql, documentSql]) {
    if (!sql) continue;
    // Show only shared names as a reading aid. This does not resolve SQL scope or aliases.
    const tokens = new Set((sql.replace(/'(?:''|[^'])*'|--[^\n]*|\/\*[\s\S]*?\*\//g, ' ')
      .toLowerCase().match(/[\p{L}_][\p{L}\p{N}_]*/gu) || []));
    const references = new Set();
    for (const id of entityIds) {
      for (const field of list(bindings.get(id)?.field_mappings)) {
        if (typeof field?.db_table === 'string' && typeof field?.db_column === 'string' &&
            tokens.has(field.db_table.toLowerCase()) && tokens.has(field.db_column.toLowerCase())) {
          references.add(`${field.db_table}.${field.db_column}`);
        }
      }
    }
    if (references.size) rows.push(row('与 SQL 模板共同出现的映射字段名', [...references], `${source} · ${mappingSource}`));
  }
  return rows;
}

export function buildMetricInsights(project, businessDefinitions = []) {
  const metricDocument = metricModel(project);
  const modelMetrics = byId(metricDocument.records);
  const businessMetrics = byId(businessDefinitions);
  const entityModel = getModel(project, 'm1-object-model.yaml', 'm1_object_model.yaml', 'entities');
  const ruleModel = getModel(project, 'm3-rule-model.yaml', 'm3_rule_model.yaml', 'rules');
  const entities = byId(entityModel.records);
  const rules = byId(ruleModel.records);
  const mapping = project?.source_bindings ?? project?.models?.['data_mapping.yaml'];
  const bindings = new Map(list(mapping?.entity_mappings).map((item) => [item?.entity_id, item]));

  return metricDefinitions(project, businessDefinitions).map((metric) => {
    const modelMetric = modelMetrics.get(metric.id);
    const businessMetric = businessMetrics.get(metric.id);
    const modelSource = metricDocument.path;
    const field = (label, ...fields) => fieldRows(label, fields, modelMetric, businessMetric, modelSource);
    const name = display(modelMetric?.name || businessMetric?.name) || metric.id;
    const modelSql = [typeof modelMetric?.sql_template === 'string' ? modelMetric.sql_template.trim() : '', `${modelSource} · sql_template`];
    const documentSql = [typeof businessMetric?.sql_template === 'string' ? businessMetric.sql_template.trim() : '', `${uploadSource} · sql_template`];
    const [code, sqlSource] = modelSql[0] ? modelSql : documentSql;
    const templates = [modelSql, documentSql];
    const entityIds = [...new Set([...list(modelMetric?.depends_on_entities), ...list(businessMetric?.depends_on_entities)]
      .filter((id) => typeof id === 'string'))];
    const ruleIds = [...new Set([...list(modelMetric?.rule_refs), ...list(businessMetric?.rule_refs)]
      .filter((id) => typeof id === 'string'))];
    const ruleRows = ruleIds.map((id) => {
      const rule = rules.get(id);
      return row([id, rule?.name].filter(Boolean).join(' · '), [rule?.description, rule?.expression].filter(Boolean).join('\n'), ruleModel.path);
    });
    const domainRows = fieldRows('业务域', ['domain'], modelMetric && { domain: modelMetric.domain || metricDocument.domain }, businessMetric, modelSource);
    const details = mappedDependencyRows(entityIds, entities, bindings, modelSql, documentSql);

    return { id: metric.id, name, sections: [
      { title: '指标定义', rows: [
        row('指标 ID', metric.id, modelMetric ? modelSource : uploadSource),
        ...field('指标名称', 'name'), ...field('别名', 'alias', 'aliases'), ...domainRows,
        ...field('单位', 'unit'), ...field('计算类型', 'computation_type'), ...field('优先级', 'priority'),
        ...field('业务定义', 'definition', 'description'),
      ] },
      { title: '指标定位与口径概述', rows: [
        ...field('指标定位', 'positioning', 'purpose'), ...field('口径概述', 'overview', 'description', 'definition'),
      ] },
      { title: '定义与计算口径', rows: [
        ...field('计算口径', 'formula_description', 'formula'), ...field('统计方法', 'stat_algorithm'),
      ] },
      { title: '计算参考 SQL', ...(code ? { code, source: sqlSource, text: '原始 SQL 模板供核对，执行状态为未运行。' } : { text: pending }),
        ...(modelSql[0] && documentSql[0] ? { rows: [row('业务文档的参考 SQL', ...documentSql)] } : {}),
      },
      { title: '来源表与字段依赖', rows: [
        ...field('依赖实体', 'depends_on_entities'), ...field('指标级字段依赖', 'depends_on_fields', 'source_dependencies'), ...details,
      ] },
      { title: '过滤规则', rows: [
        ...field('过滤口径', 'filter_rules'), ...ruleRows,
        ...sqlExcerptRows('SQL 过滤参考片段', /\b(WHERE|HAVING)\b/i, templates),
      ], ...(ruleRows.length && code ? { text: '关联规则和参考 SQL 来自各自资料，规则是否已完整落实需由业务核对。' } : {}) },
      { title: '时间口径', rows: [
        ...field('时间范围与归属', 'time_scope', 'time_policy'), ...field('支持粒度', 'supported_grains'),
        ...sqlExcerptRows('SQL 时间参考片段', /\{date_(?:start|end|format)\}|\b(?:strftime|date|datetime)\s*\(/i, templates),
      ] },
      { title: '空值与异常值处理', rows: [
        ...(modelMetric?.quality_handling || businessMetric?.quality_handling ? field('处理说明', 'quality_handling') : []),
        ...field('空值处理', 'null_handling'), ...field('异常值处理', 'outlier_handling'),
        ...sqlExcerptRows('SQL 空值处理参考片段', /\b(?:COALESCE|IFNULL|NULLIF)\s*\(|\bIS\s+(?:NOT\s+)?NULL\b/i, templates),
      ] },
      { title: '限制与口径边界', rows: [
        ...field('支持维度', 'supported_dimensions', 'dimensions'), ...field('限制与边界', 'limitations', 'boundaries'),
      ] },
    ] };
  });
}
