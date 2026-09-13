import io
import json
from pathlib import Path
import queue
import tempfile
import unittest
from unittest.mock import patch
from datetime import datetime, timezone

from account_usage import (load_account_usage, normalize_account_usage,
                           read_official_account, save_account_snapshot)

NOW = datetime(2026, 9, 12, 10, tzinfo=timezone.utc)


def snapshot(data, fetched_at='2026-09-12T09:59:00Z'):
    return {'source': 'codex-desktop', 'fetched_at': fetched_at, 'data': data}


def limits(used=25, minutes=10080, reset=1789822800):
    return {'planType': 'pro', 'primary': {'usedPercent': used,
            'windowDurationMins': minutes, 'resetsAt': reset}, 'secondary': None}


class AccountSnapshotTests(unittest.TestCase):
    def normalize(self, data, fetched_at='2026-09-12T09:59:00Z'):
        return normalize_account_usage(snapshot(data, fetched_at), now=NOW)

    def test_weekly_primary_uses_duration_and_prefers_multiple_buckets(self):
        result = self.normalize({'rateLimits': limits(99, 300),
                                 'rateLimitsByLimitId': {'codex': limits()}})
        self.assertEqual(result['status'], 'available')
        self.assertEqual(result['plan_type'], 'pro')
        self.assertEqual(len(result['windows']), 1)
        window = result['windows'][0]
        self.assertEqual(window['label'], '7 天额度')
        self.assertEqual(window['remaining_percent'], 75)
        self.assertEqual(window['resets_at'], 1789822800)
        self.assertIsNone(result['reset_probability'])

    def test_legacy_fallback_and_zero_are_not_unknown(self):
        result = self.normalize({'rateLimits': limits(0, 300, 0),
                                 'rateLimitsByLimitId': None,
                                 'rateLimitResetCredits': {'availableCount': 0}})
        self.assertEqual(result['windows'][0]['label'], '5 小时额度')
        self.assertEqual(result['windows'][0]['remaining_percent'], 100)
        self.assertEqual(result['windows'][0]['resets_at'], 0)
        self.assertEqual(result['reset_credits_available'], 0)

    def test_quota_names_use_official_label_then_model_then_friendly_fallback(self):
        cases = [('codex', {}, 'Codex'), ('codex_bengalfox', {}, 'Codex Spark'),
                 ('base_model_inference', {}, '基础模型额度'),
                 ('other', {'limitName': 'gpt-reserve'}, '基础模型额度'),
                 ('other', {'normalModelSlug': 'gpt-5.6-sol'}, 'gpt-5.6-sol'),
                 ('other', {'limitName': '模型额度', 'normalModelSlug': 'slug'}, '模型额度'),
                 ('other', {'limitName': 'x' * 200}, 'x' * 100)]
        for bucket, detail, expected in cases:
            with self.subTest(bucket=bucket, detail=detail):
                result = self.normalize({'rateLimitsByLimitId': {bucket: limits() | detail}})
                self.assertEqual(result['windows'][0]['bucket_name'], expected)
        with tempfile.TemporaryDirectory() as directory:
            raw = {'rateLimitsByLimitId': {'model': limits() | {'normalModelSlug': 'gpt-5.6-sol'}}}
            save_account_snapshot(directory, raw, '2026-09-12T09:59:00Z')
            result = load_account_usage(directory, now=NOW)
            self.assertEqual(result['windows'][0]['bucket_name'], 'gpt-5.6-sol')

    def test_nulls_stay_unknown_and_credit_count_is_authoritative(self):
        result = self.normalize({'rateLimits': limits(None, 300, None),
                                 'rateLimitResetCredits': {'availableCount': 3, 'credits': []}})
        self.assertIsNone(result['windows'][0]['remaining_percent'])
        self.assertIsNone(result['windows'][0]['resets_at'])
        self.assertEqual(result['reset_credits_available'], 3)
        absent = self.normalize({'rateLimits': limits()})
        self.assertIsNone(absent['reset_credits_available'])
        self.assertEqual(self.normalize({'rateLimits': None})['status'], 'unavailable')

    def test_malformed_and_nonfinite_values_cannot_escape_as_json_numbers(self):
        for value in (float('nan'), float('inf'), -1, True, '50', 10 ** 1000):
            with self.subTest(value=str(value)[:30]):
                result = self.normalize({'rateLimits': limits(value, value, value),
                                         'rateLimitResetCredits': {'availableCount': value}})
                self.assertIsNone(result['windows'][0]['used_percent'])
                self.assertIsNone(result['windows'][0]['window_minutes'])
                self.assertIsNone(result['windows'][0]['resets_at'])
                self.assertIsNone(result['reset_credits_available'])
                json.dumps(result, allow_nan=False)
        for value in (None, [], {'data': []}, snapshot({}) | {'fetched_at': 'invalid'}):
            self.assertEqual(normalize_account_usage(value, now=NOW)['status'], 'unavailable')

    def test_exceeded_limit_clamps_remaining_only(self):
        result = self.normalize({'rateLimits': limits(110)})
        self.assertEqual(result['windows'][0]['remaining_percent'], 0)
        self.assertEqual(result['windows'][0]['used_percent'], 110)

    def test_staleness_uses_collection_time_and_handles_future_clock(self):
        for fetched in ('2026-09-12T09:44:59Z', '2026-09-12T10:02:00Z'):
            self.assertEqual(self.normalize({'rateLimits': limits()}, fetched)['status'], 'stale')
        self.assertEqual(self.normalize({'rateLimits': limits()}, '2026-09-12T09:45:00Z')['status'],
                         'available')
        self.assertEqual(self.normalize({'rateLimits': limits()}, '2026-09-12T09:59:00')['status'],
                         'unavailable')

    def test_save_and_load_drop_secrets_and_keep_only_quota_fields(self):
        raw = {'account': {'planType': 'pro', 'email': 'secret@example.com', 'id': 'ACCOUNT_SECRET'},
               'accessToken': 'TOKEN_SECRET', 'rateLimits': limits(),
               'rateLimitResetCredits': {'availableCount': 2,
                                        'credits': [{'id': 'CREDIT_SECRET'}]}}
        raw['rateLimits']['credits'] = {'balance': 'WORKSPACE_SECRET'}
        raw['rateLimits']['primary']['auth'] = 'AUTH_SECRET'
        with tempfile.TemporaryDirectory() as directory:
            save_account_snapshot(directory, raw, '2026-09-12T09:59:00Z')
            serialized = (Path(directory) / 'account-usage.json').read_text(encoding='utf-8')
            for secret in ('secret@example.com', 'ACCOUNT_SECRET', 'TOKEN_SECRET',
                           'CREDIT_SECRET', 'WORKSPACE_SECRET', 'AUTH_SECRET'):
                self.assertNotIn(secret, serialized)
            result = load_account_usage(directory, now=NOW)
            self.assertEqual(result['status'], 'available')
            self.assertEqual(result['reset_credits_available'], 2)
            self.assertEqual(len(list(Path(directory).iterdir())), 1)

    def test_missing_and_corrupted_snapshot_are_unavailable(self):
        with tempfile.TemporaryDirectory() as directory:
            self.assertEqual(load_account_usage(directory)['status'], 'unavailable')
            (Path(directory) / 'account-usage.json').write_text('malformed')
            self.assertEqual(load_account_usage(directory)['status'], 'unavailable')

    def test_snapshot_binding_matches_resolved_home_and_rejects_other_home(self):
        with tempfile.TemporaryDirectory() as directory:
            first = Path(directory) / 'codex-one'
            second = Path(directory) / 'codex-two'
            save_account_snapshot(directory, {'rateLimits': limits()},
                                  '2026-09-12T09:59:00Z', codex_home=first)
            same = load_account_usage(directory, now=NOW, codex_home=first / '..' / first.name)
            self.assertEqual(same['status'], 'available')
            self.assertNotIn('source_home', same)
            other = load_account_usage(directory, now=NOW, codex_home=second)
            self.assertEqual(other['status'], 'unavailable')
            self.assertIn('其他或未确认', other['error'])
            self.assertEqual(other['windows'], [])

    def test_unbound_snapshot_requires_resynchronization(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'account-usage.json'
            path.write_text(json.dumps(snapshot({'rateLimits': limits()})), encoding='utf-8')
            result = load_account_usage(directory, now=NOW, codex_home=Path(directory) / 'codex')
            self.assertEqual(result['status'], 'unavailable')
            self.assertIn('重新同步', result['error'])

    def test_default_binding_uses_codex_home_and_rejects_network_paths(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / 'codex'
            with patch.dict('account_usage.os.environ', {'CODEX_HOME': str(source)}):
                save_account_snapshot(directory, {'rateLimits': limits()}, '2026-09-12T09:59:00Z')
            self.assertEqual(load_account_usage(directory, now=NOW, codex_home=source)['status'],
                             'available')
            with self.assertRaises(ValueError):
                save_account_snapshot(directory, {}, codex_home='\\\\server\\share')


class FakeProcess:
    def __init__(self, account, quota=None):
        self.stdin = io.StringIO()
        messages = [{'id': 1, 'result': {}}, {'id': 2, 'result': {'account': account}}]
        if quota is not None:
            messages.append({'id': 3, 'result': quota})
        self.stdout = io.StringIO(''.join(json.dumps(item) + '\n' for item in messages))
        self.sent = None

    def poll(self):
        self.sent = self.stdin.getvalue()
        return 0


class OfficialClientTests(unittest.TestCase):
    def test_missing_executable_is_actionable(self):
        with patch('account_usage.shutil.which', return_value=None):
            self.assertEqual(read_official_account('/test')['status'], 'unavailable')

    def test_auth_missing_does_not_call_quota_or_reveal_server_details(self):
        process = FakeProcess(None)
        with patch('account_usage.shutil.which', return_value='/codex'), \
                patch('account_usage.subprocess.Popen', return_value=process):
            result = read_official_account('/test')
        self.assertIn('尚未登录', result['error'])
        self.assertNotIn('rateLimits/read', process.sent)

    def test_timeout_and_protocol_error_return_only_safe_errors(self):
        for output in ('', '{"id": 1, "error": {"message": "TOKEN_SECRET"}}\n'):
            process = FakeProcess(None)
            process.stdout = io.StringIO(output)
            with patch('account_usage.shutil.which', return_value='/codex'), \
                    patch('account_usage.subprocess.Popen', return_value=process):
                result = read_official_account('/test', timeout=0)
            self.assertEqual(result['status'], 'unavailable')
            self.assertNotIn('TOKEN_SECRET', json.dumps(result))
        process = FakeProcess(None)
        with patch('account_usage.shutil.which', return_value='/codex'), \
                patch('account_usage.subprocess.Popen', return_value=process), \
                patch('account_usage.queue.Queue') as messages:
            messages.return_value.get.side_effect = queue.Empty
            result = read_official_account('/test', timeout=0)
        self.assertIn('超时', result['error'])

    def test_only_documented_read_requests_and_explicit_home_are_used(self):
        process = FakeProcess({'type': 'chatgpt', 'email': 'SECRET', 'planType': 'pro'},
                              {'rateLimits': limits()})
        with patch('account_usage.shutil.which', return_value='/codex'), \
                patch('account_usage.subprocess.Popen', return_value=process) as popen:
            result = read_official_account('/test/codex')
        self.assertEqual(result['status'], 'available')
        self.assertNotIn('SECRET', json.dumps(result))
        sent = [json.loads(line) for line in process.sent.splitlines()]
        self.assertEqual([item['method'] for item in sent],
                         ['initialize', 'initialized', 'account/read', 'account/rateLimits/read'])
        self.assertFalse(sent[2]['params']['refreshToken'])
        self.assertEqual(popen.call_args.kwargs['env']['CODEX_HOME'], '/test/codex')
        self.assertNotIn('shell', popen.call_args.kwargs)


if __name__ == '__main__':
    unittest.main()
