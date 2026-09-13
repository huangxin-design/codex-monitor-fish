"""Sanitized account quota snapshots from the official Codex account interface."""
import json
import math
import os
from pathlib import Path
import queue
import shutil
import subprocess
import tempfile
import threading
import time
from datetime import datetime, timezone

OFFICIAL_DOCS = 'https://learn.chatgpt.com/docs/app-server#auth-endpoints'
PROBABILITY_NOTE = '官方接口未提供明天获赠重置机会的概率；仅能查询已获得的次数和额度重置时间。'


def _empty(error):
    return {'status': 'unavailable', 'source': None, 'fetched_at': None,
            'plan_type': None, 'windows': [], 'reset_credits_available': None,
            'reset_probability': None, 'reset_probability_note': PROBABILITY_NOTE,
            'error': error}


def _number(value, minimum=0):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        return value if math.isfinite(value) and value >= minimum else None
    except OverflowError:
        return None


def _time(value):
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
        return parsed.astimezone(timezone.utc) if parsed.tzinfo else None
    except (ValueError, OverflowError):
        return None


def _duration_label(minutes):
    if minutes is None:
        return '额度窗口'
    if minutes % 1440 == 0:
        return f'{minutes / 1440:g} 天额度'
    if minutes % 60 == 0:
        return f'{minutes / 60:g} 小时额度'
    return f'{minutes:g} 分钟额度'


def _bucket_name(bucket, detail):
    labels = {'codex': 'Codex', 'codex_bengalfox': 'Codex Spark',
              'base_model_inference': '基础模型额度', 'gpt-reserve': '基础模型额度'}
    for value in (detail.get('limitName'), detail.get('normalModelSlug'), bucket):
        if isinstance(value, str) and value.strip():
            value = value.strip()
            return labels.get(value, value)[:100]
    return '账户额度'


def normalize_account_usage(snapshot, now=None, max_age_seconds=900):
    """Keep quota fields only; absent values remain unknown, including reset credits."""
    if not isinstance(snapshot, dict) or not isinstance(snapshot.get('data'), dict):
        return _empty('账户快照格式无效。')
    fetched = _time(snapshot.get('fetched_at'))
    if fetched is None:
        return _empty('账户快照缺少有效的采集时间。')
    source = snapshot.get('source')
    if source not in ('codex-desktop', 'codex-app-server'):
        return _empty('账户快照来源无效。')
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError('now 必须包含时区。')
    age = (now - fetched).total_seconds()
    result = _empty(None)
    result.update(source=source, fetched_at=fetched.isoformat())
    data = snapshot['data']
    buckets = data.get('rateLimitsByLimitId')
    if not isinstance(buckets, dict) or not buckets:
        legacy = data.get('rateLimits')
        buckets = {'codex': legacy} if isinstance(legacy, dict) else {}
    for bucket, detail in buckets.items():
        if not isinstance(detail, dict):
            continue
        plan = detail.get('planType')
        if result['plan_type'] is None and isinstance(plan, str) and plan:
            result['plan_type'] = plan[:50]
        for key in ('primary', 'secondary'):
            window = detail.get(key)
            if not isinstance(window, dict):
                continue
            used = _number(window.get('usedPercent'))
            duration = _number(window.get('windowDurationMins'), minimum=1)
            reset = _number(window.get('resetsAt'))
            result['windows'].append({
                'bucket': str(bucket)[:100], 'bucket_name': _bucket_name(bucket, detail),
                'label': _duration_label(duration),
                'used_percent': used,
                'remaining_percent': max(0, 100 - used) if used is not None else None,
                'window_minutes': duration, 'resets_at': reset})
    account = data.get('account')
    if result['plan_type'] is None and isinstance(account, dict):
        plan = account.get('planType')
        if isinstance(plan, str) and plan:
            result['plan_type'] = plan[:50]
    credits = data.get('rateLimitResetCredits')
    if isinstance(credits, dict):
        count = _number(credits.get('availableCount'))
        if count is not None and count == int(count):
            result['reset_credits_available'] = int(count)
    if not result['windows'] and result['reset_credits_available'] is None:
        result['error'] = '官方接口尚未返回此账户的额度数据。'
    else:
        result['status'] = 'stale' if age > max_age_seconds or age < -60 else 'available'
    return result


def _source_home(value):
    if not isinstance(value, (str, os.PathLike)):
        return None
    try:
        value = os.fspath(value)
        if not isinstance(value, str) or not value.strip() or '\x00' in value:
            return None
        normalized = value.replace('\\', '/')
        if normalized.startswith('//') or normalized.lower().startswith(('/??/', '/device/')):
            return None
        return os.path.normcase(str(Path(value).expanduser().resolve()))
    except (OSError, ValueError, RuntimeError):
        return None


def load_account_usage(data_dir, now=None, max_age_seconds=900, codex_home=None):
    """Read a runtime snapshot; never open Codex credential files."""
    try:
        snapshot = json.loads((Path(data_dir) / 'account-usage.json').read_text(encoding='utf-8-sig'))
    except FileNotFoundError:
        return _empty('尚未同步账户额度。')
    except (OSError, ValueError, UnicodeError):
        return _empty('账户快照无法读取，请重新同步。')
    if codex_home is not None:
        expected = _source_home(codex_home)
        actual = _source_home(snapshot.get('source_home')) if isinstance(snapshot, dict) else None
        if expected is None or actual is None or expected != actual:
            return _empty('账户快照来自其他或未确认的数据目录，请重新同步。')
    return normalize_account_usage(snapshot, now, max_age_seconds)


def save_account_snapshot(data_dir, raw_response, fetched_at=None, codex_home=None):
    """Persist only displayable quota data, never account or credit identifiers."""
    if not isinstance(raw_response, dict):
        raise ValueError('账户响应必须是 JSON 对象。')

    def sanitize_bucket(bucket):
        if not isinstance(bucket, dict):
            return None
        clean = {}
        for key in ('limitId', 'limitName', 'normalModelSlug', 'planType'):
            if isinstance(bucket.get(key), str):
                clean[key] = bucket[key][:100]
        for key in ('primary', 'secondary'):
            window = bucket.get(key)
            clean[key] = ({'usedPercent': _number(window.get('usedPercent')),
                           'windowDurationMins': _number(window.get('windowDurationMins'), 1),
                           'resetsAt': _number(window.get('resetsAt'))}
                          if isinstance(window, dict) else None)
        return clean

    data = {'rateLimits': sanitize_bucket(raw_response.get('rateLimits'))}
    buckets = raw_response.get('rateLimitsByLimitId')
    if isinstance(buckets, dict):
        data['rateLimitsByLimitId'] = {str(key)[:100]: sanitize_bucket(value)
                                      for key, value in buckets.items()}
    credits = raw_response.get('rateLimitResetCredits')
    count = _number(credits.get('availableCount')) if isinstance(credits, dict) else None
    data['rateLimitResetCredits'] = ({'availableCount': int(count)}
                                     if count is not None and count == int(count) else None)
    account = raw_response.get('account')
    if isinstance(account, dict) and isinstance(account.get('planType'), str):
        data['account'] = {'planType': account['planType'][:50]}
    fetched_at = fetched_at or datetime.now(timezone.utc).isoformat()
    if _time(fetched_at) is None:
        raise ValueError('采集时间必须是含时区的 ISO 时间。')
    source_home = _source_home(codex_home if codex_home is not None else
                               os.environ.get('CODEX_HOME') or Path.home() / '.codex')
    if source_home is None:
        raise ValueError('Codex 数据目录必须是有效的本机路径。')
    snapshot = {'source': 'codex-desktop', 'source_home': source_home,
                'fetched_at': fetched_at, 'data': data}
    directory = Path(data_dir)
    directory.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile('w', encoding='utf-8', dir=directory,
                                         prefix='.account-usage-', delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(snapshot, stream, ensure_ascii=False, allow_nan=False)
        temporary.replace(directory / 'account-usage.json')
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()
    return normalize_account_usage(snapshot)


def read_official_account(codex_home, timeout=8):
    """Optional read-only stdio client; callers decide whether to invoke it.

    Codex handles its own existing login. This does not start a login, force an
    auth refresh, create a turn, redeem a credit, or write a monitor snapshot.
    """
    executable = shutil.which('codex')
    if not executable:
        return _empty('未找到 Codex CLI，无法同步账户额度。')
    process = None
    messages = queue.Queue()
    deadline = time.monotonic() + timeout
    try:
        environment = dict(os.environ, CODEX_HOME=str(codex_home))
        process = subprocess.Popen(
            [executable, 'app-server'], env=environment, stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, encoding='utf-8',
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)

        def receive():
            try:
                for line in process.stdout:
                    try:
                        message = json.loads(line)
                        if isinstance(message, dict):
                            messages.put(message)
                    except ValueError:
                        pass
            except (OSError, ValueError):
                pass
            finally:
                messages.put(None)

        threading.Thread(target=receive, daemon=True).start()

        def send(method, ident=None, params=None):
            message = {'method': method}
            if ident is not None:
                message['id'] = ident
            if params is not None:
                message['params'] = params
            process.stdin.write(json.dumps(message) + '\n')
            process.stdin.flush()
            if ident is None:
                return None
            while True:
                response = messages.get(timeout=max(0, deadline - time.monotonic()))
                if response is None:
                    raise OSError('closed')
                if response.get('id') == ident:
                    if 'error' in response or not isinstance(response.get('result'), dict):
                        raise ValueError('unavailable')
                    return response['result']

        send('initialize', 1, {'clientInfo': {'name': 'codex_monitor_fish', 'version': '1.0.0'}})
        send('initialized', params={})
        auth = send('account/read', 2, {'refreshToken': False})
        account = auth.get('account')
        if not isinstance(account, dict):
            return _empty('Codex CLI 尚未登录账户；请先在 CLI 中登录。')
        if account.get('type') == 'apiKey':
            return _empty('当前 CLI 使用 API 密钥，无法读取 ChatGPT 套餐额度。')
        if account.get('type') == 'amazonBedrock':
            return _empty('当前 CLI 使用 Bedrock，无法读取 ChatGPT 套餐额度。')
        data = send('account/rateLimits/read', 3)
        data['account'] = account
        return normalize_account_usage({'source': 'codex-app-server', 'data': data,
                                        'fetched_at': datetime.now(timezone.utc).isoformat()})
    except (OSError, ValueError, queue.Empty):
        return _empty('账户额度同步失败或超时，请检查 Codex CLI 登录状态。')
    finally:
        if process is not None:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=1)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=1)
            process.stdin.close()
            process.stdout.close()
