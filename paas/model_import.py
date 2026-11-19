"""Bounded public-file imports for the legacy analysis exchange format."""
from __future__ import annotations

import json
import hashlib

import yaml

from .analysis import MODEL_FILES

ANALYSIS_IMPORT_FILES = frozenset((*MODEL_FILES, 'data_mapping.yaml'))
MAX_FILE_BYTES = 512 * 1024
MAX_YAML_DEPTH = 48
MAX_YAML_EVENTS = 150_000


class ModelLoader(yaml.SafeLoader):
    def construct_mapping(self, node, deep=False):
        result = {}
        for key_node, value_node in node.value:
            key = self.construct_object(key_node, deep=deep)
            if not isinstance(key, str) or key in result:
                raise ValueError('模型键必须是唯一的字符串')
            result[key] = self.construct_object(value_node, deep=deep)
        return result


def load_model_text(text):
    if not isinstance(text, str) or not text.strip():
        raise ValueError('模型文件必须是非空 UTF-8 文本')
    try:
        if len(text.encode()) > MAX_FILE_BYTES:
            raise ValueError('单个模型文件超过 512 KiB')
        depth = 0
        for index, event in enumerate(yaml.parse(text, Loader=ModelLoader)):
            if index >= MAX_YAML_EVENTS or isinstance(event, yaml.AliasEvent) or getattr(event, 'anchor', None):
                raise ValueError('模型文件过于复杂或使用了 YAML 锚点')
            if isinstance(event, yaml.CollectionStartEvent):
                depth += 1
                if depth > MAX_YAML_DEPTH:
                    raise ValueError('模型文件嵌套过深')
            elif isinstance(event, yaml.CollectionEndEvent):
                depth -= 1
        value = yaml.load(text, Loader=ModelLoader)
        if not isinstance(value, dict):
            raise ValueError('模型文件的根节点必须是对象')
        json.dumps(value, ensure_ascii=False, allow_nan=False)
        return value
    except (TypeError, UnicodeError, yaml.YAMLError) as error:
        raise ValueError('模型文件需要使用合法的 YAML 或 JSON 对象') from error


def load_analysis_files(files):
    if not isinstance(files, dict) or files.keys() != ANALYSIS_IMPORT_FILES:
        raise ValueError('请选择原始电商五模型及 data_mapping.yaml（共六份文件）')
    models = {}
    for name, text in files.items():
        try:
            models[name] = load_model_text(text)
        except ValueError as error:
            raise ValueError(f'{name}：{error}') from error
    return models


def load_business_files(files):
    from .business import MODEL_FILES as BUSINESS_FILES
    from .ui_contract import portable_design
    if not isinstance(files, dict) or set(files) != {*BUSINESS_FILES, 'manifest.json'}:
        raise ValueError('请选择七份业务模型 YAML 与 manifest.json')
    models = {name: load_model_text(text) for name, text in files.items()}
    manifest = models['manifest.json']
    names = manifest.get('model_files')
    if not isinstance(names, list) or len(names) != len(BUSINESS_FILES) or sorted(names) != sorted(BUSINESS_FILES):
        raise ValueError('manifest.json 的 model_files 必须与七份模型文件一致')
    design = portable_design(models)
    if design is None:
        raise ValueError('此模型包缺少可移植界面定义，请从带原型映射的业务项目导出七模型')
    provenance = {name: hashlib.sha256(text.encode()).hexdigest() for name, text in files.items()}
    return models, design, provenance


def business_prototype_input(project):
    """Use uploaded file identity as the rendering input of an imported design."""
    from .store import digest
    return dict(project, requirements='portable-v9:' + digest(project['business_model_import']['files_sha256']))
