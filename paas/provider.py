"""Server-side provider adapter; no provider credentials are serialized or exported."""
from __future__ import annotations

import hashlib
import http.client
import json
import os
import re
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

# The same module is copied beside server.py in exported applications.
PROJECT_ROOT = Path(__file__).resolve().parent
if __package__ == 'paas':
    PROJECT_ROOT = PROJECT_ROOT.parent
TASK_TIMEOUT_SECONDS = 1800
_PUBLIC_REPLY_LIMIT = 1000
_PUBLIC_REPLY_SOURCE_LIMIT = 12 * _PUBLIC_REPLY_LIMIT + 512
_PUBLIC_REPLY_START = re.compile(r'^[ \t\r\n]*\{[ \t\r\n]*"reply"[ \t\r\n]*:[ \t\r\n]*"')
_PUBLIC_REPLY_STRING = re.compile(r'(?:[^"\\\x00-\x1f]|\\(?:["\\/bfnrt]|u[0-9a-fA-F]{4}))*')
_PUBLIC_REPLY_PARTIAL_ESCAPE = re.compile(r'\\(?:u[0-9a-fA-F]{0,3})?')
_PUBLIC_REPLY_STOP = re.compile(r'[<\ud800-\udfff]')


def public_reply_purpose(purpose):
    return purpose in (
        'business.prd.explore',
        'business.prd.explore.transport-retry',
        'business.prd.explore.contract-repair',
        'business.prd.explore.contract-repair.transport-retry',
    )


def sanitize_public_reply(value):
    if not isinstance(value, str):
        return ''
    return _PUBLIC_REPLY_STOP.split(value[:_PUBLIC_REPLY_LIMIT], maxsplit=1)[0]


def _public_reply_preview(source):
    head = _PUBLIC_REPLY_START.match(source)
    if not head:
        return ''
    value = _PUBLIC_REPLY_STRING.match(source, head.end())
    remainder = source[value.end():]
    if remainder and not remainder.startswith('"') and not _PUBLIC_REPLY_PARTIAL_ESCAPE.fullmatch(remainder):
        return ''
    # Decode only complete JSON escapes. A split Unicode surrogate pair is
    # clipped until both halves arrive, and decoded markup is never published.
    return sanitize_public_reply(json.loads('"' + value[0] + '"'))


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _read_env(path):
    values = {}
    path = Path(path)
    if path.is_file():
        for line in path.read_text().splitlines():
            if '=' in line and not line.lstrip().startswith('#'):
                key, value = line.split('=', 1)
                values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def configuration():
    # Explicit GLM profile; never fall back to another model/provider.
    config_file = Path(os.environ.get('PAAS_PROVIDER_CONFIG', PROJECT_ROOT / '.local/paas/provider.json'))
    profile = json.loads(config_file.read_text()) if config_file.is_file() else {}
    env = _read_env(PROJECT_ROOT / '.env.paas')
    env.update(os.environ)
    base_url = env.get('PAAS_BASE_URL') or profile.get('base_url') or 'https://open.bigmodel.cn/api/coding/paas/v4'
    if not base_url.startswith('https://open.bigmodel.cn/'):
        raise ValueError('GLM模型地址必须使用已配置的智谱官方HTTPS端点')
    key = env.get('PAAS_API_KEY', '')
    if not key and profile.get('credential_source'):
        source = config_file.parent / Path(profile['credential_source']).expanduser()
        key = _read_env(source).get(profile.get('key_name', 'PAAS_API_KEY'), '')
    return {'name': 'GLM', 'model': env.get('PAAS_MODEL') or profile.get('model', 'glm-5.3-flash'),
            'endpoint': base_url.rstrip('/') + '/chat/completions', 'key': key}


def status():
    config = configuration()
    return {key: value for key, value in config.items() if key != 'key'} | {'configured': bool(config['key'])}


def parse_json(text):
    text = re.sub(r'<think>.*?</think>', '', text, flags=re.S).strip()
    fenced = re.match(r'^```(?:json)?[ \t]*\r?\n(.*?)\r?\n```(?:\r?\n|$)', text, flags=re.S | re.I)
    if fenced and '```' not in text[fenced.end():]:
        try:
            return json.loads(fenced[1])
        except json.JSONDecodeError:
            pass
    text = re.sub(r'^```(?:json)?\s*|\s*```$', '', text).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        decoder = json.JSONDecoder()
        for match in re.finditer(r'[\[{]', text):
            try:
                value, end = decoder.raw_decode(text[match.start():])
                if not text[match.start() + end:].strip(' \n`'):
                    return value
            except json.JSONDecodeError:
                continue
        raise ValueError('模型输出无法解析为 JSON，未保存为成功成果')


class LLM:
    def __init__(self, trace_dir=None, cancel=None, max_calls=40):
        self.config = configuration()
        self.trace_dir = Path(trace_dir) if trace_dir else None
        self.cancel = cancel or threading.Event()
        self.max_calls = max_calls
        self.reasoning_effort = 'low'
        self.calls = []
        self.started = time.monotonic()

    def _check_active(self):
        if self.cancel.is_set():
            raise ValueError('任务已取消，未提交模型输出')
        remaining = TASK_TIMEOUT_SECONDS - (time.monotonic() - self.started)
        if remaining <= 0:
            raise ValueError('本次任务已达到时间预算')
        return remaining

    def _write_progress(self, purpose, started, characters=0, public_reply=''):
        if not self.trace_dir:
            return
        self.trace_dir.mkdir(parents=True, exist_ok=True)
        progress = {'purpose': purpose, 'characters': characters,
                    'elapsed_seconds': round(time.monotonic() - started), 'model': self.config['model']}
        if public_reply:
            progress['public_reply'] = public_reply
        temporary = self.trace_dir / 'progress.tmp'
        temporary.write_text(json.dumps(progress))
        temporary.chmod(0o600)
        temporary.replace(self.trace_dir / 'progress.json')

    def text(self, system, user, *, purpose='generation', max_tokens=8192, _transport_retry=False):
        self._check_active()
        if not self.config['key']:
            raise ValueError('未配置GLM模型服务；请在服务端配置 PAAS_API_KEY 或已有凭据来源')
        if len(self.calls) >= self.max_calls:
            raise ValueError('本次任务已达到模型调用预算')
        payload = {'model': self.config['model'], 'messages': [
            {'role': 'system', 'content': system}, {'role': 'user', 'content': user}],
            'max_tokens': max_tokens, 'temperature': 0.15,
            'thinking': {'type': 'enabled'}, 'reasoning_effort': self.reasoning_effort, 'stream': True}
        record = {'purpose': purpose, 'model': self.config['model'],
                  'reasoning_effort': self.reasoning_effort,
                  'input_sha256': hashlib.sha256(json.dumps(payload, ensure_ascii=False).encode()).hexdigest(),
                  'started_at': time.time(), 'status': 'running'}
        self.calls.append(record)
        started = time.monotonic()
        try:
            self._write_progress(purpose, started)
            request = urllib.request.Request(self.config['endpoint'],
                data=json.dumps(payload).encode(), headers={'Content-Type': 'application/json',
                'Authorization': 'Bearer ' + self.config['key']}, method='POST')
            request_timeout = min(240, self._check_active())
            with urllib.request.build_opener(_NoRedirect).open(request, timeout=request_timeout) as response:
                if 'text/event-stream' in response.headers.get('Content-Type', ''):
                    content_parts, usage, finish, completed = [], {}, None, False
                    last_progress = 0
                    characters, preview_source = 0, ''
                    previews_reply = self.trace_dir is not None and public_reply_purpose(purpose)
                    for line in response:
                        self._check_active()
                        if not line.startswith(b'data:'):
                            continue
                        data = line[5:].strip()
                        if data == b'[DONE]':
                            completed = True
                            break
                        if not data:
                            continue
                        chunk = json.loads(data)
                        if chunk.get('error'):
                            raise ValueError('GLM流式请求返回错误，未接受不完整产物')
                        if chunk.get('usage'):
                            usage = chunk['usage']
                        for choice in chunk.get('choices', []):
                            piece = choice.get('delta', {}).get('content')
                            if piece:
                                content_parts.append(piece)
                                characters += len(piece)
                                if previews_reply and len(preview_source) < _PUBLIC_REPLY_SOURCE_LIMIT:
                                    preview_source += piece[:_PUBLIC_REPLY_SOURCE_LIMIT - len(preview_source)]
                            if choice.get('finish_reason'):
                                finish = choice['finish_reason']
                        if not self.trace_dir:
                            continue
                        now = time.monotonic()
                        if now - last_progress > (0.25 if previews_reply else 1):
                            self._write_progress(purpose, started, characters, _public_reply_preview(preview_source))
                            last_progress = now
                    if not completed and not finish:
                        raise http.client.RemoteDisconnected('Incomplete model stream')
                    if previews_reply and finish != 'length':
                        self._write_progress(purpose, started, characters, _public_reply_preview(preview_source))
                    body = {'choices': [{'message': {'content': ''.join(content_parts)}, 'finish_reason': finish}], 'usage': usage}
                else:
                    body = json.load(response)
            if self.trace_dir:
                self.trace_dir.mkdir(parents=True, exist_ok=True)
                raw_path = self.trace_dir / ('response-' + str(len(self.calls)) + '.json')
                raw_path.write_text(json.dumps(body, ensure_ascii=False, indent=2))
                raw_path.chmod(0o600)
            # Both JSON and streaming responses can finish after the last
            # in-flight check; parsing and response cleanup consume the same
            # task budget as transport and must not publish a late result.
            self._check_active()
            choices = body.get('choices', [])
            if not choices:
                raise ValueError('模型没有返回正文')
            choice = choices[0]
            if choice.get('finish_reason') == 'length':
                raise ValueError('模型输出达到长度上限，未接受截断产物')
            content = choice.get('message', {}).get('content', '')
            if isinstance(content, list):
                content = '\n'.join(x.get('text', '') for x in content)
            content = re.sub(r'<think>.*?</think>', '', content, flags=re.S).strip()
            if not content:
                raise ValueError('模型正文为空')
            self._check_active()
            record.update(status='succeeded', usage=body.get('usage', {}),
                          output_sha256=hashlib.sha256(content.encode()).hexdigest())
            return content
        except urllib.error.HTTPError as error:
            record.update(status='failed', http_status=error.code)
            if error.code in (429, 502, 503, 504) and not _transport_retry and not self.cancel.is_set():
                record['duration_ms'] = round((time.monotonic() - started) * 1000)
                return self.text(system, user, purpose=purpose + '.transport-retry', max_tokens=max_tokens, _transport_retry=True)
            raise ValueError(f'模型服务 HTTP {error.code}；本次任务未完成') from None
        except (urllib.error.URLError, TimeoutError, ConnectionError, http.client.HTTPException) as error:
            record.update(status='failed', error=type(error).__name__)
            if not _transport_retry and not self.cancel.is_set():
                record['duration_ms'] = round((time.monotonic() - started) * 1000)
                return self.text(system, user, purpose=purpose + '.transport-retry', max_tokens=max_tokens, _transport_retry=True)
            raise ValueError('模型网络请求失败或超时；可查看任务记录后重试') from None
        except Exception as error:
            record.update(status='failed', error=str(error).replace(self.config['key'], '[REDACTED]'))
            raise
        finally:
            record.setdefault('duration_ms', round((time.monotonic() - started) * 1000))
            if self.trace_dir:
                self.trace_dir.mkdir(parents=True, exist_ok=True)
                path = self.trace_dir / 'provider-calls.json'
                path.write_text(json.dumps(self.calls, ensure_ascii=False, indent=2))
                path.chmod(0o600)

    def json(self, system, user, *, purpose='generation', max_tokens=8192):
        output = self.text(system + '\n仅返回完整JSON，不要Markdown代码块。', user,
                           purpose=purpose, max_tokens=max_tokens)
        try:
            result = parse_json(output)
        except ValueError:
            repaired = self.text('修复以下 JSON 格式。保留原语义和全部元素，只返回合法 JSON。',
                                 output, purpose=purpose + '.format-repair', max_tokens=max_tokens)
            result = parse_json(repaired)
        self._check_active()
        return result
