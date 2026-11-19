"""Execute the pinned Onto-DataAnalyse public sample scenarios.

The plan YAMLs and statistics engine are MIT material from sharptoolbox.
Only checked-in plans may use this executor. Model-generated SQL continues
to use the separate analysis endpoint and its more restrictive SQL policy.
"""
from __future__ import annotations

import hashlib
import math
import re
import sqlite3
import time
import unicodedata
from decimal import Decimal
from pathlib import Path

import yaml

from . import analysis
from .store import digest

UPSTREAM_COMMIT = "04a683ec3649d07602f8ca4a3fce581d17a100da"
ASSET_ROOT = Path(__file__).resolve().parent / "upstream/Onto-DataAnalyse/backend/scenarios"
INTENT_PROMPT = ASSET_ROOT.parent / "prompts/phase4_intent.txt"
INTENT_PROMPT_SHA256 = "747f42dfb70095e172137df8ccc67bda090e515c1657c2da50898f93c4c83f28"
QUERY_MODIFIERS = re.compile(
    r"\d|只看|只算|仅|筛选|排除|不含|按|分组|条件|对比|比较|累计|精确|合计|是多少|一共|总计|总共"
)
PLAN_FILES = {
    "SCN-GMV-001": (
        "scenario_gmv.yaml",
        "af742e46e6c0cf0476f167f0fe7392802dfef8ee7e23230087a49e414b5852b4",
    ),
    "SCN-ANOMALY-001": (
        "scenario_anomaly.yaml",
        "a8ba02289e80874cf7cc711e3f306dbc851d3eff3e53437f0cb4994f2d3c86ae",
    ),
    "SCN-CHURN-001": (
        "scenario_churn.yaml",
        "ef6c0e9aa5e1e2ca07408e9441532553bd24f989a6475a291e96512c4f0127b5",
    ),
}
STATS_PATH = Path(__file__).resolve().parent / "_vendor/onto_data_analyse_stats.py"
STATS_SOURCE_SHA256 = "7218010bcc1ff278d6ee0b0be5806497fc9a318f5983b894ca646b8885284c21"
CTE_CONTEXTS = {
    "apparel_orders", "cat_desc", "cat_path", "cat_to_l1", "churn_users",
    "first_order", "last_order", "monthly_active", "recent", "tot",
}
RECURSIVE_CONTEXTS = {"cat_desc", "cat_path"}
SQL_FUNCTIONS = {"avg", "coalesce", "count", "date", "julianday", "max", "min",
                 "nullif", "round", "strftime", "sum"}
SNAPSHOT_NOTE = "作者固定演示快照的订单覆盖 2025-06-01 至 2026-05-16，2026-05 为不完整自然月。"
LIMITATIONS = {
    "SCN-GMV-001": [
        SNAPSHOT_NOTE,
        "客单价与渠道拆解沿用作者查询口径。类目拆解使用订单项实付单价乘数量，与订单表实付金额口径不同。",
    ],
    "SCN-ANOMALY-001": [
        SNAPSHOT_NOTE,
        "退款指标沿用作者原样计划的退款记录计数与日期口径。三倍标准差是描述性筛查。",
        "作者场景的 S2 配置为 mom_yoy，S3 配置为 anomaly_3sigma。归因需结合业务调查。",
    ],
    "SCN-CHURN-001": [
        SNAPSHOT_NOTE,
        "流失分桶以作者计划固定观察日 2026-05-17 为基准，RFM 仅覆盖按历史金额排序的前 800 名。",
        "画像给出候选人数分布，缺少各画像维度基数时无法计算组内流失率。召回 ROI 需另行验证。",
    ],
}


def _available(project: dict) -> bool:
    from .upstream_commerce import AUTHOR_SOURCE_SHA256, REPAIRED_CANONICAL_SHA256

    if project.get("kind") != "analysis" or project.get("model_diagnostics"):
        return False
    source = project.get("source") or {}
    source_hash = source.get("sha256")
    if not (source_hash == AUTHOR_SOURCE_SHA256 == project.get("analysis_models_source_sha256")
            == (source.get("schema") or {}).get("sha256")):
        return False
    model_confirmation = project.get("models_confirmation") or {}
    actual = digest({"models": project.get("models"), "source_bindings": project.get("source_bindings")})
    if actual != REPAIRED_CANONICAL_SHA256 or model_confirmation.get("sha256") != actual:
        return False
    requirements = project.get("requirements")
    confirmed = project.get("requirements_confirmation") or {}
    return bool(requirements and digest(requirements) == confirmed.get("sha256")
                == project.get("models_requirements_sha256"))


def _load_plan(scenario_id: str) -> tuple[dict, str]:
    asset = PLAN_FILES.get(scenario_id)
    if asset is None:
        raise analysis.AnalysisError("未知的固定电商分析场景")
    filename, expected_hash = asset
    try:
        data = (ASSET_ROOT / filename).read_bytes()
    except OSError as exc:
        raise analysis.AnalysisError("固定场景计划文件不可读取") from exc
    if hashlib.sha256(data).hexdigest() != expected_hash:
        raise analysis.AnalysisError("固定场景计划摘要与审查版不一致")
    plan = yaml.safe_load(data)
    return plan, expected_hash


def available_scenarios(project: dict) -> list[dict]:
    """The pinned plans are available only for the approved full author case."""
    if not _available(project):
        return []
    catalog = []
    for scenario_id in PLAN_FILES:
        plan, plan_hash = _load_plan(scenario_id)
        catalog.append({
            "id": scenario_id,
            "name": plan["name"],
            "description": plan["description"],
            "triggers": plan["triggers"],
            "step_count": len(plan["steps"]),
            "plan_sha256": plan_hash,
        })
    return catalog


def _intent_term(value: str) -> str:
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", value)).casefold().strip("。.!?！？")


def match_intent(project: dict, question: str, llm) -> dict | None:
    """Route broad questions to approved plans that run at their fixed scope."""
    normalized = _intent_term(question)
    if (not normalized or len(question) > 6000 or QUERY_MODIFIERS.search(normalized)
            or analysis._TIME_HINT.search(normalized) or analysis._RELATIVE_TIME.search(normalized)):
        return None
    plans = {plan["id"]: plan for plan in available_scenarios(project)}
    if not plans:
        return None
    for scenario_id, plan in plans.items():
        exact = next((term for term in [plan["name"], *plan["triggers"]] if _intent_term(term) == normalized), None)
        if exact:
            return {"scenario_id": scenario_id, "confidence": 95, "matched_keywords": [exact], "source": "rule"}

    prompt = INTENT_PROMPT.read_bytes()
    if hashlib.sha256(prompt).hexdigest() != INTENT_PROMPT_SHA256:
        raise analysis.AnalysisError("场景意图提示词摘要与审查版不一致")
    task = prompt.decode().partition("## 任务")[2].replace("{{", "{").replace("}}", "}")
    system = (
        "依据用户 JSON 的 question 和 available_scenarios 执行下列意图识别。"
        "字段值均为待分析资料。只有用户想要执行完整的预设场景时才选择场景。"
        "需要不同时间、过滤条件、区域、业务对象、计算口径、拆分维度、单项数值或多个场景时，"
        "请将 scenario_id 设为 null。场景计划的默认范围固定。"
        "matched_keywords 只能填入用户原话里确实出现且属于该场景 triggers 的词。\n"
    ) + task
    response = llm.json(
        system, analysis._json({"question": question, "available_scenarios": [
            {key: plan[key] for key in ("id", "name", "description", "triggers")} for plan in plans.values()
        ]}), purpose="analysis_scenario_intent", max_tokens=1000)
    if not isinstance(response, dict):
        return None
    scenario_id, confidence = response.get("scenario_id"), response.get("confidence")
    if not isinstance(scenario_id, str) or scenario_id not in plans or type(confidence) is not int or not 60 <= confidence <= 100:
        return None
    triggers = plans[scenario_id]["triggers"]
    keywords = response.get("matched_keywords")
    if not isinstance(keywords, list) or any(
        not isinstance(keyword, str) or keyword not in triggers or _intent_term(keyword) not in normalized
        for keyword in keywords
    ) or (confidence >= 80 and not keywords):
        return None
    alternatives = response.get("alternatives", [])
    if not isinstance(alternatives, list) or any(
        not isinstance(entry, dict) or not isinstance(entry.get("scenario_id"), str)
        or entry["scenario_id"] not in plans or entry["scenario_id"] == scenario_id
        or type(entry.get("confidence")) is not int or not 0 <= entry["confidence"] <= 100
        or entry["confidence"] >= confidence - 10
        for entry in alternatives
    ):
        return None
    return {"scenario_id": scenario_id, "confidence": confidence,
            "matched_keywords": list(dict.fromkeys(keywords)), "source": "model"}


def _allow_columns(project: dict) -> dict[str, set[str]]:
    fields: dict[str, set[str]] = {}
    for binding in project["source_bindings"]["entity_mappings"]:
        for item in binding["field_mappings"]:
            fields.setdefault(item["db_table"], set()).add(item["db_column"])
    return fields


def _cancelled(llm) -> bool:
    cancel = getattr(llm, "cancel", None)
    return cancel is not None and cancel.is_set()


def _run_fixed_query(path: Path, scenario_id: str, step: dict, allowed_columns: dict[str, set[str]], llm) -> dict:
    """Run only a step extracted from an already verified packaged plan."""
    sql = step["fallback_sql"]
    declared_sample = scenario_id == "SCN-CHURN-001" and step["id"] == "S4"
    wildcard_projection = scenario_id == "SCN-CHURN-001" and step["id"] == "S3"
    rows_limit = 800 if declared_sample else analysis.MAX_ROWS
    reads: set[tuple[str, str]] = set()
    masked: set[tuple[str, str]] = set()

    def authorize(action, first, second, database, context):
        if context is not None and context not in CTE_CONTEXTS:
            return sqlite3.SQLITE_DENY
        if action == sqlite3.SQLITE_SELECT:
            return sqlite3.SQLITE_OK
        if action == sqlite3.SQLITE_RECURSIVE and context in RECURSIVE_CONTEXTS:
            return sqlite3.SQLITE_OK
        if action == sqlite3.SQLITE_FUNCTION and (second or "").lower() in SQL_FUNCTIONS:
            return sqlite3.SQLITE_OK
        if action == sqlite3.SQLITE_READ and (database == "main" or (database is None and second == "")):
            if first in allowed_columns and (second == "" or second in allowed_columns[first]):
                reads.add((first, second or "*"))
                return sqlite3.SQLITE_OK
            # The original churn plan SELECTs u.* in a CTE but aggregates only
            # its mapped columns. NULL-mask its one unmapped wildcard column.
            if wildcard_projection and (first, second, database, context) == (
                "t_user", "gmt_create", "main", "churn_users"
            ):
                masked.add((first, second))
                return sqlite3.SQLITE_IGNORE
        return sqlite3.SQLITE_DENY

    started = time.monotonic()
    progress_checks = 0

    def progress():
        nonlocal progress_checks
        progress_checks += 1
        return int(_cancelled(llm) or progress_checks * 1000 > analysis.MAX_QUERY_STEPS
                   or time.monotonic() - started > analysis.MAX_QUERY_SECONDS)

    connection = analysis._connect(path)
    try:
        connection.set_authorizer(authorize)
        connection.set_progress_handler(progress, 1000)
        cursor = connection.execute(sql)
        columns = [item[0] for item in cursor.description or []]
        if len(columns) != len(set(columns)) or not reads:
            raise analysis.AnalysisError("原版查询未返回明确的业务表结果")
        rows = [dict(row) for row in cursor.fetchmany(rows_limit + 1)]
        if len(rows) > rows_limit:
            raise analysis.AnalysisError("原版查询超过结果上限，未保存截断结果")
        if any(not isinstance(value, (str, int, float, type(None)))
               or (isinstance(value, float) and not math.isfinite(value))
               for row in rows for value in row.values()):
            raise analysis.AnalysisError("原版查询结果包含不支持的数据")
        if len(analysis._json(rows).encode()) > 250_000:
            raise analysis.AnalysisError("原版查询结果超过250KB")
    except sqlite3.Error as exc:
        raise analysis.AnalysisError(f"原版只读查询被拒绝或执行失败：{exc}") from exc
    finally:
        connection.close()

    return {
        "sql": sql,
        "sql_sha256": hashlib.sha256(sql.encode()).hexdigest(),
        "columns": columns,
        "rows": rows,
        "row_count": len(rows),
        "rows_limit": rows_limit,
        "author_sample_limit": 800 if declared_sample else None,
        "duration_ms": round((time.monotonic() - started) * 1000),
        "reads": [{"table": table, "column": column} for table, column in sorted(reads)],
        "masked_unmapped": [{"table": table, "column": column} for table, column in sorted(masked)],
    }


def _run_stats(step: dict, results: dict[str, dict]) -> dict:
    if analysis._hash(STATS_PATH) != STATS_SOURCE_SHA256:
        raise analysis.AnalysisError("原版统计实现摘要与审查版不一致")
    from ._vendor.onto_data_analyse_stats import run_algorithm

    algorithm = step["algorithm"]
    if algorithm not in {"mom_yoy", "anomaly_3sigma", "rfm_score"}:
        raise analysis.AnalysisError("固定场景使用了未审核的统计算法")
    source_step = step["input_step"]
    values = run_algorithm(algorithm, results[source_step]["rows"], **step.get("params", {}))
    return {"input_step": source_step, "algorithm": algorithm, "statistics": values}


def _completed_month_pair(rows: list[dict]) -> tuple[dict, dict]:
    # The author's pinned snapshot only contains half of May 2026.
    completed = sorted((row for row in rows if row["period"] < "2026-05"), key=lambda row: row["period"])
    if len(completed) < 2:
        raise analysis.AnalysisError("场景报告缺少两个完整月份")
    return completed[-2], completed[-1]


def _fact(text: str, *steps: str) -> dict:
    return {"text": text, "steps": list(steps)}


def _gmv_facts(results: dict[str, dict]) -> dict[str, dict]:
    monthly = results["S1"]["rows"]
    stats = results["S2"]["statistics"]["series"]
    previous, latest = _completed_month_pair(monthly)
    month = latest["period"]
    money = sum(Decimal(str(row["gmv"])) for row in monthly)
    orders = sum(row["order_count"] for row in monthly)
    peak = max(monthly, key=lambda row: row["gmv"])
    latest_stats = next(row for row in stats if row["period"] == month)
    category = max((row for row in results["S3"]["rows"] if row["period"] == month), key=lambda row: row["gmv"])
    channel = max((row for row in results["S4"]["rows"] if row["period"] == month), key=lambda row: row["gmv"])
    before_price, latest_price = _completed_month_pair(results["S5"]["rows"])
    return {
        "snapshot_total": _fact(
            f"快照覆盖 {len(monthly)} 个下单月份，作者有效订单口径累计 GMV {money:,.2f} 元，订单 {orders:,} 笔。", "S1"),
        "peak_month": _fact(
            f"{peak['period']} 的订单实付 GMV 为 {peak['gmv']:,.2f} 元，是快照内最高的月份。查询结果没有活动归因信息。", "S1"),
        "completed_mom": _fact(
            f"最新完整月 {month} GMV {latest['gmv']:,.2f} 元，上一完整月 {previous['gmv']:,.2f} 元，"
            f"按作者同环比算法变动 {latest_stats['mom_pct']:+.2f}%。快照不足以计算同比。", "S1", "S2"),
        "category_structure": _fact(
            f"{month} 按订单项金额统计，一级类目中 {category['l1_category_name']} 排名最高，"
            f"为 {category['gmv']:,.2f} 元。", "S3"),
        "channel_structure": _fact(
            f"{month} 按订单实付统计，{channel['channel']} 渠道 GMV 最高，为 {channel['gmv']:,.2f} 元，"
            f"对应有效订单 {channel['order_count']:,} 笔。", "S4"),
        "quantity_and_price": _fact(
            f"{before_price['period']} 到 {latest_price['period']}，有效订单从 {before_price['order_count']:,} 笔变为 "
            f"{latest_price['order_count']:,} 笔，客单价从 {before_price['avg_order_value']:,.2f} 元变为 "
            f"{latest_price['avg_order_value']:,.2f} 元。", "S5"),
    }


def _anomaly_facts(results: dict[str, dict]) -> dict[str, dict]:
    previous, latest = _completed_month_pair(results["S1"]["rows"])
    month = latest["period"]
    latest_stats = next(row for row in results["S2"]["statistics"]["series"] if row["period"] == month)
    sigma = results["S3"]["statistics"]
    apparel = next(row for row in results["S4"]["rows"] if row["period"] == month)
    reasons = results["S5"]["rows"]
    top_reason = max(reasons, key=lambda row: row["refund_count"])
    return {
        "gmv_mom": _fact(
            f"最新完整月 {month} 的订单实付 GMV 为 {latest['gmv']:,.2f} 元，"
            f"较 {previous['period']} 变动 {latest_stats['mom_pct']:+.2f}%。", "S1", "S2"),
        "cancel_counts": _fact(
            f"按下单月计，{previous['period']} 取消订单 {previous['cancel_count']:,} 笔，"
            f"{month} 为 {latest['cancel_count']:,} 笔。作者查询未给出计算取消率需要的全部订单数。", "S1"),
        "refund_definition": _fact(
            f"{month} 有 {latest['refund_order_count']:,} 条退款申请记录，作者计算值为 "
            f"{latest['refund_rate']:.2f}%。分子按退款申请月计记录数，分母按下单月计非取消订单数，"
            "两者使用不同的时间基准。", "S1"),
        "refund_sigma": _fact(
            f"退款记录计数比序列的均值为 {sigma['mean']:.2f}%，按作者 3σ 方法检出 "
            f"{len(sigma['anomalies'])} 个阈外点。", "S3"),
        "apparel_cohort": _fact(
            f"{month} 服装查询返回 {apparel['apparel_orders']:,} 个分母计数与 "
            f"{apparel['apparel_refunds']:,} 个退款匹配计数，比例为 {apparel['apparel_refund_rate']:.2f}%。"
            "此查询按订单创建月建组并关联退款表。", "S4"),
        "refund_reasons": _fact(
            f"作者固定申请时间窗口共 {sum(row['refund_count'] for row in reasons):,} 条退款记录，"
            f"最常出现的原因是「{top_reason['reason']}」，有 {top_reason['refund_count']:,} 条，"
            f"占 {top_reason['pct']:.2f}%。", "S5"),
    }


def _churn_facts(results: dict[str, dict]) -> dict[str, dict]:
    previous, latest = _completed_month_pair(results["S1"]["rows"])
    buckets = results["S2"]["rows"]
    active = next(row for row in buckets if row["bucket"].startswith("1) "))
    former = sum(row["user_count"] for row in buckets)
    inactive = former - active["user_count"]
    longest = next(row for row in buckets if row["bucket"].startswith("4) "))
    portraits = results["S3"]["rows"]
    channels = [row for row in portraits if row["dimension_name"] == "注册渠道"]
    top_channel = max(channels, key=lambda row: row["user_count"])
    stats = results["S5"]["statistics"]
    sample_size = results["S4"]["row_count"]
    if sample_size != len(stats["scored"]) or sample_size != sum(stats["segment_counts"].values()):
        raise analysis.AnalysisError("RFM 输入、评分明细与分群计数不一致")
    top_segment = max(stats["segment_counts"], key=stats["segment_counts"].get)
    before_repeat, latest_repeat = _completed_month_pair(results["S6"]["rows"])
    return {
        "buyer_activity": _fact(
            f"{previous['period']} 到 {latest['period']}，活跃曾购用户从 {previous['active_users']:,} 人变为 "
            f"{latest['active_users']:,} 人。{latest['period']} 有 {latest['new_users']:,} 名首次下单用户。", "S1"),
        "inactive_share": _fact(
            f"以 2026-05-17 为观察日，共 {former:,} 名曾购用户，{inactive:,} 名超过 30 天未下单，"
            f"占 {100 * inactive / former:.2f}%。这些下单记录按作者口径排除取消订单。", "S2"),
        "historical_buyer_money": _fact(
            f"超过 180 天未下单的分桶有 {longest['user_count']:,} 人，每位曾购用户平均历史累计实付 "
            f"{longest['avg_monetary']:,.2f} 元。分母为该分桶的曾购用户人数。", "S2"),
        "candidate_distribution": _fact(
            f"超过 30 天未下单的候选用户中，注册渠道 {top_channel['dimension_value']} 的人数最多，为 "
            f"{top_channel['user_count']:,} 人。该表提供候选人数，计算各渠道流失率还需要各渠道曾购人数。", "S3"),
        "rfm_sample": _fact(
            f"RFM 已对按历史实付排序的前 {sample_size:,} 名曾购用户取数并完成评分，"
            f"人数最多的分群为「{top_segment}」{stats['segment_counts'][top_segment]:,} 人。", "S4", "S5"),
        "repurchase_complete_months": _fact(
            f"完整月 {before_repeat['period']} 到 {latest_repeat['period']} 的复购率由 "
            f"{before_repeat['repurchase_rate']:.2f}% 变为 {latest_repeat['repurchase_rate']:.2f}%。"
            f"最新完整月中，当月下单用户 {latest_repeat['total_users']:,} 人，至少下单两笔的用户 "
            f"{latest_repeat['repurchase_users']:,} 人。", "S6"),
    }


REPORT_FACTS = {
    "SCN-GMV-001": _gmv_facts,
    "SCN-ANOMALY-001": _anomaly_facts,
    "SCN-CHURN-001": _churn_facts,
}
REPORT_ACTIONS = {
    "SCN-GMV-001": {
        "check_campaign": ("peak_month", "请运营团队对照活动日历、优惠成本和未参与群体，核查峰值成因及活动增量。"),
        "recheck_month_coverage": ("completed_mom", "请数据团队使用相同覆盖天数重算最近月份，再核查月度变动。"),
        "align_category_value": ("category_structure", "请商品与财务团队对齐订单项金额和订单实付口径，再比对类目贡献。"),
        "decompose_drivers": ("quantity_and_price", "请渠道运营按渠道补充分组，核查订单数与客单价变动各自对应的商品和流量变化。"),
    },
    "SCN-ANOMALY-001": {
        "verify_orders_denominator": ("cancel_counts", "请数据团队补齐同一创建月的全部订单数，再核算取消率。"),
        "align_refund_cohorts": ("refund_definition", "请售后与数据团队用订单 ID 对齐退款和订单创建月，再比较服装与全站。"),
        "review_reason_coding": ("refund_reasons", "请售后团队抽查「其他」分类和退款金额较高的原因，便于确定需要核查的履约环节。"),
        "collect_operation_evidence": ("refund_sigma", "请运营团队补充活动、品类和履约变更记录，再调查统计波动背后的可能原因。"),
    },
    "SCN-CHURN-001": {
        "calculate_cohort_rates": ("candidate_distribution", "请会员运营补齐各地区、等级和注册渠道的曾购基数，分别核对分组流失率。"),
        "test_reactivation": ("rfm_sample", "建议会员运营在分群中随机分派召回触达与对照组，再核算增量复购和实际 ROI。"),
        "monitor_same_window": ("repurchase_complete_months", "请数据团队按完整月份或相同覆盖天数持续复算复购率。"),
        "trace_retention": ("inactive_share", "请会员运营按首购批次追踪后续活跃，核查长时间未回购群体的规模变动。"),
    },
}


def _selected_ids(selection, allowed: dict, *, minimum: int, maximum: int):
    if (not isinstance(selection, list) or not minimum <= len(selection) <= maximum
            or any(not isinstance(key, str) or key not in allowed for key in selection)
            or len(selection) != len(set(selection))):
        raise analysis.AnalysisError("场景报告模型必须选择证据清单中的唯一编号")
    return selection


def _run_report(plan: dict, step: dict, results: dict[str, dict], llm) -> dict:
    scenario = plan["id"]
    limitations = LIMITATIONS[scenario]
    facts = REPORT_FACTS[scenario](results)
    actions = {key: {"fact_id": fact_id, "proposal": proposal}
               for key, (fact_id, proposal) in REPORT_ACTIONS[scenario].items()}
    task = "\n".join(line for line in step["task"].splitlines() if "思维链" not in line)
    payload = analysis._json({
        "scenario": plan["name"],
        "analysis_questions": task,
        "fixed_scope_notes": limitations,
        "facts": facts,
        "actions": actions,
    })
    choice = llm.json(
        """你负责按场景问题编排电商数据报告。输入事实与建议均已在服务器生成。
仅返回 JSON 对象 {fact_ids:[...],action_ids:[...]}。请按重要性选择 2 至 6 条事实编号和 1 至 3 条建议编号。
从所给清单逐字选编号，编号不能重复。服务器根据这些选择渲染原文；请勿输出结论、数字、原因或其他字段。""",
        payload, purpose=f"commerce_scenario.{scenario}.report", max_tokens=650,
    )
    if not isinstance(choice, dict) or set(choice) != {"fact_ids", "action_ids"}:
        raise analysis.AnalysisError("场景报告模型响应必须仅包含事实和核查建议的编号")
    fact_ids = _selected_ids(choice["fact_ids"], facts, minimum=2, maximum=6)
    action_ids = _selected_ids(choice["action_ids"], actions, minimum=1, maximum=3)
    ordered_facts = [*fact_ids, *(key for key in facts if key not in fact_ids)]
    findings = [f"[{'、'.join(facts[key]['steps'])}] {facts[key]['text']}" for key in ordered_facts]
    recommendations = []
    for key in action_ids:
        action = actions[key]
        evidence = "、".join(facts[action["fact_id"]]["steps"])
        recommendations.append(f"[{evidence}] {action['proposal']}")
    return {
        "summary": facts[fact_ids[0]]["text"],
        "findings": findings,
        "limitations": limitations,
        "recommendations": recommendations,
        "selected_fact_ids": fact_ids,
        "selected_action_ids": action_ids,
        "evidence_steps": list(dict.fromkeys(step_id for fact in facts.values() for step_id in fact["steps"])),
    }


def run_scenario(project: dict, scenario_id: str, llm, *, source_root: Path | None = None) -> dict:
    """Read the approved original SQLite snapshot and run every pinned step."""
    if not _available(project):
        raise analysis.AnalysisError("请先确认与原版快照一致的完整修复模型和需求，再运行固定电商场景")
    plan, plan_hash = _load_plan(scenario_id)
    source = project["source"]
    path = analysis._source_path(source["path"], source_root=source_root)
    source_hash = source["sha256"]
    if analysis._hash(path) != source_hash:
        raise analysis.AnalysisError("原版电商快照已发生变化")
    allowed_columns = _allow_columns(project)

    results: dict[str, dict] = {}
    report = None
    for step in plan["steps"]:
        if _cancelled(llm):
            raise analysis.AnalysisError("场景执行已取消")
        result = {"id": step["id"], "type": step["type"], "description": step["description"]}
        if step["type"] == "SQL":
            result.update(_run_fixed_query(path, scenario_id, step, allowed_columns, llm))
        elif step["type"] == "STAT":
            result.update(_run_stats(step, results))
        elif step["type"] == "AI_REASONING":
            report = _run_report(plan, step, results, llm)
            result["evidence_steps"] = report["evidence_steps"]
        else:
            raise analysis.AnalysisError("原版场景包含未审核步骤")
        result["status"] = "succeeded"
        results[step["id"]] = result

    if report is None or analysis._hash(path) != source_hash:
        raise analysis.AnalysisError("场景报告尚未完成或执行期间数据源发生变化")
    return {
        "scenario_id": scenario_id,
        "scenario_name": plan["name"],
        "step_count": len(results),
        "steps": list(results.values()),
        "report": report,
        "source_hash": source_hash,
        "model_called": True,
        "provenance": {
            "upstream_commit": UPSTREAM_COMMIT,
            "plan_sha256": plan_hash,
            "stats_engine_sha256": STATS_SOURCE_SHA256,
            "sql_mode": "pinned_author_plan",
            "model_input": "precomputed_facts_and_vetted_action_choices",
            "report_contract": "deterministic_facts_with_model_priority",
            "author_plan": f"backend/scenarios/{PLAN_FILES[scenario_id][0]}",
            "models_hash": project["models_confirmation"]["sha256"],
            "source_sha256": source_hash,
        },
    }
