"""Cross-platform regression of the Windows wait API; not a Windows OS test."""
from __future__ import annotations

import ctypes
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from testbox import updater


class UpdaterWaitTests(unittest.TestCase):
    @staticmethod
    def kernel(*, handle=42, status=0):
        return SimpleNamespace(
            OpenProcess=Mock(return_value=handle),
            WaitForSingleObject=Mock(return_value=status),
            CloseHandle=Mock(return_value=True),
        )

    def invoke(self, kernel, *, error=0):
        with patch.object(ctypes, 'WinDLL', return_value=kernel, create=True), \
             patch.object(ctypes, 'get_last_error', return_value=error, create=True), \
             patch.object(ctypes, 'WinError', side_effect=lambda code: OSError(code, 'Windows API failure'), create=True):
            updater._wait_for_windows_pid(123, 0.25)

    def test_wait_requests_only_synchronize_and_closes_handle(self):
        kernel = self.kernel()
        self.invoke(kernel)
        kernel.OpenProcess.assert_called_once_with(0x00100000, False, 123)
        kernel.WaitForSingleObject.assert_called_once_with(42, 250)
        kernel.CloseHandle.assert_called_once_with(42)

    def test_missing_process_returns_without_waiting(self):
        kernel = self.kernel(handle=0)
        self.invoke(kernel, error=87)
        kernel.WaitForSingleObject.assert_not_called()
        kernel.CloseHandle.assert_not_called()

    def test_access_denied_is_not_interpreted_as_process_exit(self):
        kernel = self.kernel(handle=0)
        with self.assertRaises(OSError) as caught:
            self.invoke(kernel, error=5)
        self.assertEqual(caught.exception.errno, 5)
        kernel.WaitForSingleObject.assert_not_called()

    def test_timeout_closes_handle_and_raises(self):
        kernel = self.kernel(status=0x102)
        with self.assertRaises(TimeoutError):
            self.invoke(kernel)
        kernel.CloseHandle.assert_called_once_with(42)

    def test_wait_failure_closes_handle_and_raises(self):
        kernel = self.kernel(status=0xFFFFFFFF)
        with self.assertRaises(OSError):
            self.invoke(kernel, error=6)
        kernel.CloseHandle.assert_called_once_with(42)

    def test_windows_branch_does_not_call_os_kill(self):
        with patch.object(updater.os, 'name', 'nt'), \
             patch.object(updater, '_wait_for_windows_pid') as wait, \
             patch.object(updater.os, 'kill') as kill:
            updater._wait_for_pid(123, 0.25)
        wait.assert_called_once_with(123, 0.25)
        kill.assert_not_called()

    def test_invalid_pid_and_negative_timeout_are_rejected_before_platform_api(self):
        with patch.object(updater, '_wait_for_windows_pid') as wait, \
             patch.object(updater.os, 'kill') as kill:
            for pid, timeout in ((0, 1), (-1, 1), (1, -0.1)):
                with self.subTest(pid=pid, timeout=timeout), self.assertRaises(ValueError):
                    updater._wait_for_pid(pid, timeout)
        wait.assert_not_called()
        kill.assert_not_called()


if __name__ == '__main__':
    unittest.main()
