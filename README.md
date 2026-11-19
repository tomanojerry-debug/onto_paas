# Onto PaaS

Onto PaaS 是一个在本机运行的本体驱动应用平台。你可以在同一个工作台把业务需求构建成可运行的应用、分析 SQLite 数据，或把文档整理成可追溯的知识库。

[快速开始](#快速开始) · [使用流程](#使用流程) · [模型与架构](#模型与架构) · [开发与验证](#开发与验证) · [参考与致谢](#参考与致谢)

![Onto PaaS 业务本体工作台，展示内置销售合同案例](docs/images/workbench.png)

## 能做什么

| 项目类型 | 输入 | 产出 |
| --- | --- | --- |
| **业务应用** | 业务需求、交互原型或已有的七模型包 | 版本化业务应用、候选验收记录、可独立启动的源码开发包 |
| **数据分析** | SQLite 快照或已构建应用的数据快照 | 经过确认的数据本体与字段映射、可追溯的只读查询和分析报告 |
| **文档知识** | Markdown 或纯文本资料 | 场景、概念和实体模型，以及带原文证据的问答 |

## 快速开始

需要 **Python 3.11** 和 **macOS 或 Linux**。工作台由 Flask 提供服务，前端无需单独安装 Node.js 或构建。

```sh
git clone https://github.com/tomanojerry-debug/onto_paas.git
cd onto_paas

python3.11 -m venv .venv
.venv/bin/python -m pip install -r requirements.lock.txt
.venv/bin/python -m paas.server --port 4320
```

访问 [本体应用工作台](http://127.0.0.1:4320/)。默认数据目录为 `.local/paas/`，可通过 `--data-dir /path/to/workspace` 或 `PAAS_DATA_DIR` 改用其他目录。每个工作区同一时间由一个服务进程使用。

工作台默认只监听 `127.0.0.1`，用于本地单人管理。完整工作台依赖 Python 服务和可写工作区，GitHub Pages 无法运行该服务。

### 启用 GLM

启动和浏览工作台无需 API 密钥。需求对话、模型生成、自然语言分析和知识问答需要联网访问智谱 GLM。

```sh
cp .env.example .env.paas
chmod 600 .env.paas
```

在 `.env.paas` 中填入 `PAAS_API_KEY`，然后刷新工作台。同名进程环境变量优先。

| 配置项 | 用途 |
| --- | --- |
| `PAAS_API_KEY` | 智谱 API 密钥 |
| `PAAS_MODEL` | 模型名，默认 `glm-5.3-flash` |
| `PAAS_BASE_URL` | 智谱官方 HTTPS 接口，默认 `https://open.bigmodel.cn/api/coding/paas/v4` |

也可以通过 `PAAS_PROVIDER_CONFIG` 指定本地 JSON 配置，以 `credential_source` 和 `key_name` 从单独文件取密钥。相对凭据路径以配置文件所在目录为准。密钥由服务端读取。使用 AI 功能时，相关任务内容会随请求发送到配置的 GLM 服务。

## 使用流程

### 业务应用

1. **确认需求**。创建业务应用项目，选择 V9 逐阶段确认或多轮澄清。可从对话开始，也可上传 Markdown、TXT 或带文本层的 PDF。
2. **审核规格与原型**。审核需求规格和对应的自检证据，预览、修改并确认交互原型。
3. **确认模型**。逐份审核七个模型，通过界面调用链核对页面操作对应的行为、规则、角色、流程和报表。
4. **构建与验收**。在候选应用中使用构建生成的角色账号，检查表单、权限、适用的审批路径与报表。在「应用发布」记录验收用例。候选版本发生变更后需要重新验收。
5. **发布与导出**。发布固定当前代码、模型和数据快照。开发包包含需求、原型、模型、DDL 和源码，可在独立环境中初始化运行。跨版本的业务数据迁移需要单独处理。

已有可移植模型时，可在空白项目的「开发配置」选择「导入七模型构建」，上传七份 YAML 和 `manifest.json`，再确认导入包中的 MU 界面基线与模型。也可以使用固定的销售合同案例，体验合同、付款阶段、分级审批、开票和收款联动。

### 数据分析

1. 上传 SQLite 快照，或选择已构建应用的业务数据快照。
2. 确认分析目标，生成并审核数据本体和物理字段映射。也可导入现有的五份电商分析模型和 `data_mapping.yaml`。
3. 提出分析问题，查看结果、统计、执行 SQL、绑定参数和来源。固定电商案例在通过来源校验后，可运行 GMV、经营异常与客户流失分析。

通用查询使用只读 SQLite，每次执行一条平面 `SELECT`，支持最多四个有完整外键依据的 JOIN、最多 500 行结果和三秒查询预算。

### 文档知识

1. 上传 UTF-8 Markdown 或 TXT，并确认第一轮提取和语义去重后的候选。
2. 审核第二轮生成的场景、概念和实体模型。概念的输入、步骤和输出关联原文证据。
3. 用确认后的模型提问。工作台展示检索链路和原文短引。资料或模型变更后需重新确认。

## 模型与架构

业务应用与数据分析共享 `onto-paas-seven-v1` 文件格式，由七份 YAML 和 `manifest.json` 组成。

| 模型 | 负责内容 |
| --- | --- |
| M1 | 对象、聚合与状态 |
| M2 | 业务或分析行为 |
| M3 | 业务规则、计算口径与质量规则 |
| M5 | 角色与权限 |
| M6 | 审批和业务流程，或分析场景与步骤 |
| M7 | 报表与指标 |
| MU | 页面、控件与操作映射 |

业务编译器将支持的模型语义转换为可执行应用。数据分析将物理字段映射单独保存，M5 和 MU 保持为空。文档知识使用独立的场景、概念和实体模型。

```text
onto_paas/
├── paas/
│   ├── server.py          # Flask 入口与 API
│   ├── web/               # 工作台 HTML、CSS 与 JavaScript
│   ├── business.py       # 业务模型与工作流
│   ├── builder.py        # 应用构建与开发包导出
│   ├── analysis.py       # 数据绑定与只读分析
│   ├── knowledge.py      # 知识提取与问答
│   ├── provider.py       # 服务端 GLM 适配
│   ├── upstream/         # 固定版本的参考资料、源码与许可
│   └── tests/            # 回归测试与浏览器验证
├── docs/images/          # 工作台截图
└── requirements.lock.txt
```

## 开发与验证

运行离线回归测试。测试使用临时工作区和虚构资料，启动测试不需要模型凭据。

```sh
.venv/bin/python -m unittest discover -s paas/tests -v
```

浏览器验证需要安装 Google Chrome。夹具测试会自行启动服务，真实 HTTP 集成使用独立的临时工作区。

```sh
.venv/bin/python -m pip install -r requirements-browser.txt
.venv/bin/python -m paas.tests.ui_smoke --fixture
.venv/bin/python -m paas.tests.ui_http_integration
```

<details>
<summary>真实 GLM 和公开案例验证</summary>

以下命令使用已配置的 GLM，会产生 API 用量。先完成业务试验，再用同一试验目录验证完整链路。

```sh
.venv/bin/python -m paas.verify_live
.venv/bin/python -m paas.verify_final_chain
.venv/bin/python -m paas.verify_knowledge
```

业务试验和知识试验可以用 `--resume` 续跑。可用 `PAAS_VERIFY_DIR` 或 `PAAS_KNOWLEDGE_VERIFY_DIR` 为新一轮试验指定目录。

验证固定版本的合同和电商案例时，先根据 [来源清单](paas/upstream/sources.json) 检出对应的上游提交，再指定本地路径。附加 `--live-model` 会增加一次真实 GLM 电商用例。

```sh
.venv/bin/python -m paas.tests.verify_upstream_cases \
  --commerce-repo /path/to/Onto-DataAnalyse \
  --contract-repo /path/to/ontology-driven-dev \
  --output .local/verification/upstream-case-rerun
```

使用真实 GLM 验证合同助手和电商分析流程时，可运行 `.venv/bin/python -m paas.tests.verify_author_workflows --commerce-repo /path/to/Onto-DataAnalyse`。

</details>

## 参考与致谢

Onto PaaS 参考人月聊 IT（sharptoolbox）的开源项目进行二次开发，将业务应用构建、数据分析和文档知识问答整合成一个新的本地 PaaS。

| 参考内容 | 上游项目 |
| --- | --- |
| V9 需求探索、交互原型、应用技术底座与合同案例 | [ontology-driven-dev](https://github.com/sharptoolbox/ontology-driven-dev) |
| 本体建模框架与七模型规范 | [ontology_modeling_framework](https://github.com/sharptoolbox/ontology_modeling_framework) |
| 数据建模、字段映射和电商分析案例 | [Onto-DataAnalyse](https://github.com/sharptoolbox/Onto-DataAnalyse) |
| 文档知识萃取与有据问答 | [LLM-knowledgeBase](https://github.com/sharptoolbox/LLM-knowledgeBase) |

上游清单记录引用的版本及原文件摘要。项目在 `ontology-driven-dev/SKILL.md` 中增加了工作台的原型生成指引。原始来源见 [上游来源清单](paas/upstream/sources.json) 和 [合同案例来源清单](paas/upstream/contract_reference/source-sha256.json)。

### 第三方许可

- 需求、技术底座和合同案例保留 MIT 许可。 [技术底座](paas/upstream/ontology-driven-dev/LICENSE) · [合同案例](paas/upstream/contract_reference/LICENSE) · [合同前端第三方许可](paas/upstream/contract_reference/web/THIRD-PARTY-LICENSES.txt)
- 七模型规范保留 Apache-2.0 许可与声明。 [许可证](paas/upstream/ontology_modeling_framework/LICENSE) · [NOTICE](paas/upstream/ontology_modeling_framework/NOTICE)
- 分析场景和 `paas/_vendor/onto_data_analyse_stats.py` 中的统计引擎，以及文档知识参考资料，分别保留各自的 MIT 许可。 [数据分析](paas/upstream/Onto-DataAnalyse/LICENSE) · [文档知识](paas/upstream/LLM-knowledgeBase/LICENSE)
- 工作台内置的前端组件保留各自的许可声明。 [d3-force](paas/web/d3-force.LICENSE.txt) · [elastic-textarea](paas/web/elastic-textarea.LICENSE.txt)
