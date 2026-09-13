"""Account reads must stay responsive and preserve dated official fallback data."""
from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from desktop import runtime


class AccountRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.state = runtime.DesktopState(self.base / 'data', self.base / 'codex')

    def snapshot(self, age=0):
        return runtime.account_usage.save_account_snapshot(
            self.state.data_dir,
            {'rateLimits': {'planType': 'pro', 'primary': {
                'usedPercent': 22, 'windowDurationMins': 10080, 'resetsAt': 2000000000}},
             'rateLimitResetCredits': {'availableCount': 0}},
            fetched_at=(datetime.now(timezone.utc) - timedelta(seconds=age)).isoformat(),
            codex_home=self.state.home)

    def wait_account(self):
        deadline = time.monotonic() + 2
        while self.state.account_running and time.monotonic() < deadline:
            time.sleep(.005)
        self.assertFalse(self.state.account_running)

    def test_fresh_snapshot_does_not_launch_cli_and_zero_credit_stays_known(self):
        self.snapshot()
        with patch.object(runtime.account_usage, 'read_official_account') as read:
            result = self.state.account_response()
        read.assert_not_called()
        self.assertEqual(result['reset_credits_available'], 0)
        self.assertEqual(result['windows'][0]['remaining_percent'], 78)

    def test_refresh_is_nonblocking_and_repeated_polls_are_single_flight(self):
        self.snapshot()
        entered, release = threading.Event(), threading.Event()
        def read(_):
            entered.set()
            release.wait(2)
            return {'status': 'unavailable', 'error': 'offline'}
        with patch.object(runtime.account_usage, 'read_official_account', side_effect=read) as mock:
            try:
                start = time.monotonic()
                self.assertTrue(self.state.account_response(refresh=True)['refreshing'])
                self.assertLess(time.monotonic() - start, .5)
                self.assertTrue(entered.wait(1))
                for _ in range(3):
                    self.state.account_response(refresh=True)
                self.assertEqual(mock.call_count, 1)
            finally:
                release.set()
                self.wait_account()
        result = self.state.account_response()
        self.assertEqual(result['status'], 'available')
        self.assertEqual(result['refresh_error'], 'offline')
        self.assertEqual(result['windows'][0]['remaining_percent'], 78)

    def test_cached_cli_success_expires_and_does_not_keep_restarting_on_failure(self):
        old = self.snapshot(age=1000)
        self.state.account_result = {**old, 'status': 'available', 'source': 'codex-app-server'}
        self.state.account_checked_at = time.monotonic()
        result = self.state.account_response()
        self.assertEqual(result['status'], 'stale')
        self.assertFalse(result['refreshing'])
        self.state.account_result = {'status': 'unavailable', 'error': 'offline'}
        with patch.object(runtime.account_usage, 'read_official_account') as read:
            self.assertEqual(self.state.account_response()['status'], 'stale')
        read.assert_not_called()

    def test_failed_refresh_retains_last_successful_cli_result(self):
        good = self.snapshot()
        self.state.account_result = {**good, 'source': 'codex-app-server'}
        (self.state.data_dir / 'account-usage.json').unlink()
        with patch.object(runtime.account_usage, 'read_official_account', return_value={
                'status': 'unavailable', 'error': 'offline'}):
            self.state._read_account(self.state.home)
        result = self.state.account_response()
        self.assertEqual(result['status'], 'available')
        self.assertEqual(result['refresh_error'], 'offline')
        self.assertEqual(result['fetched_at'], good['fetched_at'])

    def test_elapsed_codex_reset_triggers_new_read_before_snapshot_expires(self):
        snapshot = self.snapshot()
        snapshot['windows'][0]['resets_at'] = time.time() - 1
        self.state.account_checked_at = time.monotonic() - 121
        with patch.object(runtime.account_usage, 'load_account_usage', return_value=snapshot), \
                patch.object(runtime.account_usage, 'read_official_account', return_value={
                    'status': 'unavailable', 'error': 'offline'}) as read:
            self.state.account_response()
            self.wait_account()
        read.assert_called_once_with(self.state.home)


if __name__ == '__main__':
    unittest.main()
