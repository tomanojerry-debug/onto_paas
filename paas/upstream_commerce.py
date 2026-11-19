"""Opt-in metric completion for the pinned Onto-DataAnalyse demo bundle."""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from types import MappingProxyType

from .ontology import analysis_to_seven
from .store import digest

_MANIFEST_PATH = Path(__file__).with_name("upstream") / "Onto-DataAnalyse" / "metric_repairs.json"
_MANIFEST_SHA256 = "4d5de1d1b4f5e03681aac0f6513c0eb71e895955d45ca6409d798518444469c2"


def _load_manifest():
    raw = _MANIFEST_PATH.read_bytes()
    if hashlib.sha256(raw).hexdigest() != _MANIFEST_SHA256:
        raise ValueError("电商原版指标修复清单校验失败")
    return json.loads(raw)


_MANIFEST = _load_manifest()
METRIC_REPAIR_PROFILE = _MANIFEST["profile"]
AUTHOR_SOURCE_SHA256 = _MANIFEST["source_sha256"]
ORIGINAL_MODEL_HASHES = MappingProxyType(_MANIFEST["original_hashes"])
REPAIRED_LEGACY_SHA256 = _MANIFEST["repaired_legacy_sha256"]
REPAIRED_CANONICAL_SHA256 = _MANIFEST["repaired_bundle_sha256"]


def apply_metric_repairs(models, *, source_sha256, original_hashes):
    """Complete exactly two metrics for the unchanged author's SQLite and models."""
    if source_sha256 != AUTHOR_SOURCE_SHA256:
        raise ValueError("此指标修复仅适用于 Onto-DataAnalyse 作者原版 SQLite 快照")
    if original_hashes != ORIGINAL_MODEL_HASHES or digest(models) != _MANIFEST["original_legacy_sha256"]:
        raise ValueError("此指标修复要求六份未修改的 Onto-DataAnalyse 作者原版模型文件")

    repaired = copy.deepcopy(models)
    entries = repaired["m_metric_model.yaml"]["metrics"]
    by_id = {metric["id"]: metric for metric in entries}
    evidence = {key: copy.deepcopy(_MANIFEST[key]) for key in (
        "profile", "source_repository", "source_commit", "source_sha256", "original_hashes",
        "original_legacy_sha256", "repaired_legacy_sha256", "repaired_bundle_sha256", "time_window",
    )}
    evidence["manifest_sha256"] = _MANIFEST_SHA256
    evidence["repairs"] = []
    for repair in _MANIFEST["repairs"]:
        metric_id = repair["metric_id"]
        fields = copy.deepcopy(repair["fields"])
        if repair["operation"] == "append":
            if metric_id in by_id:
                raise ValueError(f"已有指标 {metric_id}，未应用原版补全")
            entries.append(fields)
        elif repair["operation"] == "complete" and by_id.get(metric_id) == repair["expected"]:
            by_id[metric_id].update(fields)
        else:
            raise ValueError(f"指标 {metric_id} 内容与原版补全基线不一致")
        entry = {key: copy.deepcopy(value) for key, value in repair.items() if key != "fields"}
        entry["changed_fields"] = list(fields)
        evidence["repairs"].append(entry)

    canonical, bindings = analysis_to_seven(repaired)
    if (digest(repaired) != REPAIRED_LEGACY_SHA256
            or digest({"models": canonical, "source_bindings": bindings}) != REPAIRED_CANONICAL_SHA256):
        raise ValueError("补全后的电商指标模型未通过完整性校验")
    return repaired, evidence


def metric_queries():
    """Return the two package-controlled SQL templates with bound date parameters."""
    return {
        repair["metric_id"]: repair["fields"]["sql_template"]
            .replace("'{date_start}'", ":date_start")
            .replace("'{date_end}'", ":date_end")
        for repair in _MANIFEST["repairs"]
    }
