import csv
import tempfile
import unittest
import urllib.error
from pathlib import Path

from lector.core import MAX_QR_BYTES, QueueStore, Synchronizer, normalize_qr


class FakeClient:
    def __init__(self, outcomes): self.outcomes, self.sent = list(outcomes), []
    def send(self, event):
        self.sent.append(event.event_id)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception): raise outcome
        return outcome


class QueueTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.path = Path(self.tmp.name) / "q.db"
        self.store = QueueStore(self.path)
    def tearDown(self): self.store.close(); self.tmp.cleanup()

    def test_durable_reopen_and_monotonic_sequence(self):
        first = self.store.enqueue("d1", "signed|payload", "period-3", "2026-09-24T08:00:00-04:00")
        second = self.store.enqueue("d1", "signed|other")
        self.assertEqual((first.sequence, second.sequence), (1, 2)); self.store.close()
        self.store = QueueStore(self.path); restored = self.store.get(first.event_id)
        self.assertEqual(restored.qr, "signed|payload"); self.assertEqual(restored.period_version, "period-3")
        self.assertEqual(restored.captured_at, "2026-09-24T08:00:00-04:00")

    def test_validation(self):
        for bad in ("", "  ", "x\x00y", "x" * (MAX_QR_BYTES + 1)):
            with self.assertRaises(ValueError): normalize_qr(bad)
        self.assertEqual(normalize_qr("signed|x\r\n"), "signed|x")

    def test_network_failure_then_reconnect_and_fifo(self):
        one = self.store.enqueue("d", "one"); two = self.store.enqueue("d", "two")
        client = FakeClient([urllib.error.URLError("offline"), (200, {"status":"accepted", "receipt_id":"r1"}), (200, {"status":"accepted", "receipt_id":"r2"})])
        sync = Synchronizer(self.store, client, "d")
        self.assertEqual(sync.once(), "pending"); self.assertEqual(client.sent[-1], one.event_id)
        self.assertEqual(sync.once(), "synced"); self.assertEqual(client.sent[-1], one.event_id)
        self.assertEqual(sync.once(), "synced"); self.assertEqual(client.sent[-1], two.event_id)
        self.assertEqual(self.store.get(one.event_id).server_receipt_id, "r1")

    def test_timeout_after_acceptance_retries_same_id(self):
        event = self.store.enqueue("d", "one")
        client = FakeClient([TimeoutError(), (200, {"status":"duplicate", "receipt_id":"stable"})])
        sync = Synchronizer(self.store, client, "d")
        self.assertEqual(sync.once(), "pending"); self.assertEqual(sync.once(), "synced")
        self.assertEqual(client.sent, [event.event_id, event.event_id])

    def test_transient_and_terminal_results(self):
        event = self.store.enqueue("d", "one"); sync = Synchronizer(self.store, FakeClient([(503, {}), (429, {})]), "d")
        self.assertEqual(sync.once(), "pending"); self.assertEqual(sync.once(), "pending")
        sync.client = FakeClient([(422, {"status":"review", "message":"period changed"})])
        self.assertEqual(sync.once(), "review"); self.assertEqual(self.store.get(event.event_id).status, "review")

    def test_diagnostic_redacts_qr(self):
        self.store.enqueue("d", "VERY-SECRET-PERSONAL-QR")
        target = Path(self.tmp.name) / "diag.csv"; self.store.export_diagnostic(target)
        text = target.read_text(); self.assertNotIn("VERY-SECRET", text); self.assertIn("qr_sha256_12", text)


if __name__ == "__main__": unittest.main()
