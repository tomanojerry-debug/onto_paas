"""Fictional equipment-loan V9 requirements with independently inspectable evidence."""
from paas.requirements_v9 import audit_items

_EVIDENCE = {
    '1.1': '员工借用设备目前依靠手工登记，目标是让借用状态可追踪。',
    '1.2': '范围内为员工借用登记、主管审批与查询，范围外为采购。',
    '1.3': '设备借用业务域由 OBJ-01 借用单承载。',
    '1.4.1': 'PROC-01 经过登记、审批、查询三个活动，审批由 PROC-APP-01 处理。',
    '1.5': '核心对象 OBJ-01 借用单、REP-01 借用查询和 PROC-01 借用流程。',
    '1.6': 'ROLE-01 为借用申请人，ROLE-02 为设备主管。',
    '2.1.1': 'FUNC-01 保存草稿，FUNC-02 提交审批，B-APP-01 批准，Q-01 查询。',
    '2.1.2': '用例图覆盖申请人和设备主管可执行的操作。',
    '2.1.3': '用例图与岗位角色与功能权限矩阵的授权边一致。',
    '2.2': '申请人保存 OBJ-01 的设备名称和数量。前置为已登录，失败保留输入，后置为草稿，无同步联动；设备主管可批准待审批记录。',
    '2.3.1': '用户确认不涉及独立属性级规则，当前数量校验作为复用规则。',
    '2.3.2': '用户确认不涉及单据内部不变量。',
    '2.3.3': 'R-01 数量校验在保存和提交中复用，输入数量，判断大于零，输出允许或拒绝。',
    '2.3.4': '数量只有 R-01 一个定义，保存和提交引用该规则。',
    '3.1': 'OBJ-01 借用单为聚合根，设备名称和整数数量必填；初始状态为草稿，由提交进入待审批，再经批准进入已批准终止状态。',
    '3.2': '仅有借用单一个对象。设备名称为字符串，数量为整数，状态为枚举，草稿可作废，已批准保留历史。',
    '3.3': '整体 ER 图覆盖借用单，当前没有对象间外键。',
    '3.4': '状态图覆盖草稿、待审批和已批准，禁止由草稿直接跳至已批准。',
    '4.1': '用户确认不存在跨对象联动，借用登记只改变同一借用单。',
    '4.2': 'PROC-01 从登记进入主管审批子流程，结果可由借用查询查看。',
    '4.3.1': 'PROC-APP-01 从提交开始，由 ROLE-02 批准，已批准后结束。',
    '4.4': '流程结果与借用单状态一致，批准前必须提交。',
    '5.1': 'REP-01 从借用单读取设备名称、数量和状态，按状态过滤，无聚合，按设备名称排序，唯一查询为 Q-01。',
    '6.1': 'ROLE-01 授予 FUNC-01、FUNC-02、Q-01，ROLE-02 授予 B-APP-01、Q-01。',
    '附录B': '无遗留问题，已完成业务范围确认。',
    'C.1': '借用单包含设备名称、数量和状态。',
    'C.7': '设备借用为一级菜单，下设借用申请、登记申请及借用查询，录入页提供保存草稿与提交操作。',
    'C.8': '用户确认范围不涉及跨对象联动，因此联动矩阵不适用。',
    'C.9': '覆盖协同流程、用例、ER、状态及审批五张图。',
}


def evidence_prd(extra='', *, prototype_not_applicable=False):
    sections = [
        ('# 设备借用 V9 规范', extra),
        ('# 第一部分 项目概述与范围', ''),
        ('## 1.1 项目背景与目标', _EVIDENCE['1.1']),
        ('## 1.2 业务范围', _EVIDENCE['1.2']),
        ('## 1.3 业务域划分', _EVIDENCE['1.3']),
        ('## 1.4 端到端业务流程全景', ''),
        ('### 1.4.1 PROC-01 借用登记与审核', _EVIDENCE['1.4.1'] + '''
| 步骤 | 业务活动 |
|---|---|
| 1 | FUNC-01 登记 |
| 2 | PROC-APP-01 审批 |
| 3 | Q-01 查询 |

```mermaid
flowchart LR
  Start[开始] --> Draft[FUNC-01 登记]
  Draft --> Approve[PROC-APP-01 审批]
  Approve --> Query[Q-01 查询]
  Query --> End[结束]
```'''),
        ('## 1.5 核心清单总览', _EVIDENCE['1.5']),
        ('## 1.6 角色总览', _EVIDENCE['1.6']),
        ('# 第二部分 业务功能与规则', ''),
        ('## 2.1 业务功能清单', ''),
        ('### 2.1.1 功能清单表', _EVIDENCE['2.1.1']),
        ('### 2.1.2 角色-功能业务用例', _EVIDENCE['2.1.2'] + '''
```mermaid
flowchart LR
  Applicant[ROLE-01 申请人] --> Save[FUNC-01 保存草稿]
  Applicant --> Submit[FUNC-02 提交审批]
  Applicant --> Query[Q-01 查询]
  Manager[ROLE-02 主管] --> Approve[B-APP-01 批准]
  Manager --> Query
```'''),
        ('### 2.1.3 用例图覆盖说明', _EVIDENCE['2.1.3']),
        ('## 2.2 功能详述', _EVIDENCE['2.2']),
        ('## 2.3 业务规则', ''),
        ('### 2.3.1 属性级规则', _EVIDENCE['2.3.1']),
        ('### 2.3.2 对象级不变量', _EVIDENCE['2.3.2']),
        ('### 2.3.3 跨对象或复用规则', _EVIDENCE['2.3.3']),
        ('### 2.3.4 规则归属', _EVIDENCE['2.3.4']),
        ('# 第三部分 业务对象', ''),
        ('## 3.1 借用单', _EVIDENCE['3.1']),
        ('## 3.2 对象属性与关联说明', _EVIDENCE['3.2']),
        ('## 3.3 对象关系总览', _EVIDENCE['3.3'] + '''
```mermaid
erDiagram
  Loan {
      string deviceName
      int quantity
      string status
  }
```'''),
        ('## 3.4 核心对象状态迁移图', _EVIDENCE['3.4'] + '''
```mermaid
stateDiagram-v2
  [*] --> Draft: FUNC-01
  Draft --> Pending: FUNC-02
  Pending --> Approved: B-APP-01
  Approved --> [*]
```'''),
        ('# 第四部分 流程', ''),
        ('## 4.1 跨对象联动', _EVIDENCE['4.1']),
        ('## 4.2 端到端协同流', _EVIDENCE['4.2']),
        ('## 4.3 人工审批流', ''),
        ('### 4.3.1 PROC-APP-01 主管审批', _EVIDENCE['4.3.1'] + '''
```mermaid
flowchart TD
  Start[开始] --> Submit[FUNC-02 提交]
  Submit --> Approve[B-APP-01 主管批准]
  Approve --> End[结束]
```'''),
        ('## 4.4 一致性核对', _EVIDENCE['4.4']),
        ('# 第五部分 查询统计与固定报表', ''),
        ('## 5.1 借用查询', _EVIDENCE['5.1']),
        ('# 第六部分 岗位角色与功能权限', ''),
        ('## 6.1 角色清单', _EVIDENCE['6.1']),
        ('## 6.2 功能权限矩阵', '''| 角色编号 | 授权行为编号 |
|---|---|
| ROLE-01 申请人 | FUNC-01、FUNC-02、Q-01 |
| ROLE-02 主管 | B-APP-01、Q-01 |'''),
        ('# 附录 A 术语表', '借用单为一条设备借用申请。'),
        ('# 附录 B 待澄清问题清单', _EVIDENCE['附录B']),
        ('# 附录 C 七模型建模输入基线', ''),
        ('## C.1 M1 对象及约束', _EVIDENCE['C.1'] + '''
| 编号 | 对象 | 属性与约束 |
|---|---|---|
| OBJ-01 借用单 | 聚合根 | 设备名称、数量和状态 |
'''),
        ('## C.2 M2 行为', '''| 编号 | 类型 | 对象与动作 |
|---|---|---|
| FUNC-01 保存草稿 | COMMAND | OBJ-01 保存填写的设备名称和数量 |
| FUNC-02 提交审批 | COMMAND | OBJ-01 进入 PROC-APP-01 并设为待审批 |
| B-APP-01 批准 | APPROVAL | OBJ-01 由 ROLE-02 批准 |
| Q-01 借用查询 | QUERY | 使用 REP-01 |
'''),
        ('## C.3 M3 规则', '''| 编号 | 规则 | 调用 |
|---|---|---|
| R-01 数量校验 | 必须大于零 | FUNC-01、FUNC-02 |
'''),
        ('## C.4 M5 角色及权限', '''| 编号 | 角色 | 授权行为 |
|---|---|---|
| ROLE-01 申请人 | 员工 | FUNC-01、FUNC-02、Q-01 |
| ROLE-02 设备主管 | 主管 | B-APP-01、Q-01 |
'''),
        ('## C.5 M6 协同和审批流', '''| 编号 | M6 类型 | 活动与结束 |
|---|---|---|
| PROC-01 借用登记与审核 | COLLABORATION | 登记后进入 PROC-APP-01，结果可经 Q-01 查询 |
| PROC-APP-01 主管审批 | APPROVAL | FUNC-02 提交、ROLE-02 执行 B-APP-01，设置已批准后结束 |
'''),
        ('## C.6 M7 查询报表', '''| 编号 | 数据与条件 | 查询行为 |
|---|---|---|
| REP-01 借用查询 | 按 OBJ-01 状态过滤，返回设备、数量和状态 | Q-01 |
'''),
        ('## C.7 MU 界面', _EVIDENCE['C.7'] + '''
| 编号 | 屏幕 | 行为 |
|---|---|---|
| UI-01 借用申请 | 列表维护 | FUNC-02、B-APP-01 |
| UI-02 登记申请 | 单表录入 | FUNC-01 |
| UI-03 借用查询 | 查询列表 | Q-01 |
'''),
        ('## C.8 跨对象联动矩阵', _EVIDENCE['C.8']),
        ('## C.9 可视化覆盖索引', _EVIDENCE['C.9'] + '''
| 图表 | 章节 | 覆盖内容 |
|---|---|---|
| 协同流 | 1.4.1 | 登记、审批、查询 |
| 用例图 | 2.1.2 | 角色与功能 |
| ER 图 | 3.3 | 借用单 |
| 状态图 | 3.4 | 草稿、待审批、已批准 |
| 审批流 | 4.3.1 | 主管批准 |
'''),
    ]
    # Stable screen IDs are also declared in the requirement-side UI baseline.
    sections.insert(-10, ('# 界面原型', 'UI-01 借用申请、UI-02 登记申请、UI-03 借用查询。'))
    scopes = (
        '1.1 1.2 2.2 1.3 1.4.1 1.4.1 1.4.1 1.5 2.1.1 2.1.2 2.1.3 2.2 4.1 2.2 2.2 2.3.1 '
        '2.3.2 2.3.3 3.1 3.1 3.2 3.1 3.3 3.2 4.1 4.1 4.1 3.4 3.4 4.2 4.3.1 4.3.1 4.4 5.1 5.1 '
        '5.1 6.1 附录B C.1 C.8 C.9 C.9 C.7 C.7 C.7 C.7 C.7'
    ).split()
    not_applicable = {13, 16, 17, 25, 26, 27, 40}
    evidence = _EVIDENCE.copy()
    if prototype_not_applicable:
        evidence['C.7'] = '用户确认界面原型不适用，应用保留基本借用处理界面。'
        not_applicable.update(range(43, 48))
        sections = [(heading, evidence['C.7'] if heading.startswith('## C.7 ') else body)
                    for heading, body in sections if heading != '# 界面原型']
    checklist = ['| 序号 | 自检项 | 状态 | 依据与理由 |', '|---|---|---|---|']
    for number, (name, _) in audit_items().items():
        scope = scopes[number - 1]
        status = '不适用' if number in not_applicable else '通过'
        checklist.append(f'| {number} | {name} | {status} | §{scope}「{evidence[scope]}」 |')
    sections.extend([('# 需求完整性自检报告（47项）', '\n'.join(checklist)), ('# 修订记录', '首版设备借用验证。')])
    return '\n\n'.join(heading + '\n\n' + body.strip() for heading, body in sections)


def loan_requirement_models(models):
    """Bind fictional application elements to the evidence_prd() stable IDs."""
    from copy import deepcopy
    result = deepcopy(models)
    result['m1-object-model.yaml']['aggregates'][0]['requirementRefs'] = ['OBJ-01']
    for behavior, ref in zip(result['m2-behavior-model.yaml']['behaviors'], ('FUNC-01', 'FUNC-02', 'B-APP-01', 'Q-01'), strict=True):
        behavior['requirementRefs'] = [ref]
    result['m3-rule-model.yaml']['rules'][0]['requirementRefs'] = ['R-01']
    for role, ref in zip(result['m5-actor-model.yaml']['roles'], ('ROLE-01', 'ROLE-02'), strict=True):
        role['requirementRefs'] = [ref]
    flows = result['m6-flow-model.yaml']['flows']
    flows[0]['requirementRefs'] = ['PROC-APP-01']
    flows.append({
        'id': 'FLOW-LOAN-LIFECYCLE', 'name': '借用登记与审核', 'flowType': 'COLLABORATION',
        'requirementRefs': ['PROC-01'], 'businessObjectRefs': ['AGG-LOAN'],
        'startActivity': 'START', 'endActivities': ['END'],
        'activities': [
            {'activityId': 'START', 'activityType': 'START', 'nextActivities': ['REGISTER']},
            {'activityId': 'REGISTER', 'activityType': 'USER_TASK', 'behaviorRef': 'Loan_Create',
             'roleRef': 'ROLE-REQUESTER', 'nextActivities': ['END']},
            {'activityId': 'END', 'activityType': 'END', 'nextActivities': []},
        ],
    })
    result['m7-report-model.yaml']['query_reports'][0]['requirementRefs'] = ['REP-01']
    result['mu-ui-model.yaml']['screens'][0]['requirementRefs'] = ['UI-01', 'UI-02', 'UI-03']
    return result
