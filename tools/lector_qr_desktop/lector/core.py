from __future__ import annotations

import csv
import hashlib
import json
import os
import random
import sqlite3
import threading
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

MAX_QR_BYTES = 4096
TERMINAL = {"synced", "review", "rejected"}


def data_directory() -> Path:
    root = os.environ.get("LOCALAPPDATA")
    return Path(root) / "EdukadoLector" if root else Path.home() / ".local" / "share" / "EdukadoLector"


def normalize_qr(value: str) -> str:
    """Only remove scanner framing; the signed payload is otherwise untouched."""
    value = value.rstrip("\r\n")
    if not value or not value.strip():
        raise ValueError("El QR está vacío")
    if len(value.encode("utf-8")) > MAX_QR_BYTES:
        raise ValueError("El QR excede 4096 bytes")
    if any(ord(c) < 32 for c in value):
        raise ValueError("El QR contiene caracteres de control")
    return value


@dataclass(frozen=True)
class Event:
    event_id: str
    device_id: str
    sequence: int
    qr: str
    captured_at: str
    period_version: str | None
    status: str
    attempts: int
    last_error: str | None
    last_code: str | None
    server_receipt_id: str | None
    created_at: str
    updated_at: str


class QueueStore:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        try:
            os.chmod(path.parent, 0o700)
        except OSError:
            pass
        self.path = path
        self._lock = threading.RLock()
        self.db = sqlite3.connect(path, timeout=10, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.executescript("""
        CREATE TABLE IF NOT EXISTS events(
          event_id TEXT PRIMARY KEY, device_id TEXT NOT NULL, sequence INTEGER NOT NULL,
          qr TEXT NOT NULL CHECK(length(qr) BETWEEN 1 AND 4096), captured_at TEXT NOT NULL,
          period_version TEXT, status TEXT NOT NULL CHECK(status IN ('pending','synced','review','rejected')),
          attempts INTEGER NOT NULL DEFAULT 0, last_error TEXT, last_code TEXT,
          server_receipt_id TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
          UNIQUE(device_id, sequence));
        CREATE INDEX IF NOT EXISTS events_fifo ON events(device_id,status,sequence);
        CREATE TABLE IF NOT EXISTS sequences(device_id TEXT PRIMARY KEY, value INTEGER NOT NULL);
        """)
        self.db.commit()

    def close(self):
        with self._lock:
            self.db.close()

    def enqueue(self, device_id: str, qr: str, period_version: str | None = None,
                captured_at: str | None = None) -> Event:
        qr = normalize_qr(qr)
        if not device_id or len(device_id) > 128:
            raise ValueError("device_id inválido")
        now = datetime.now(timezone.utc).astimezone().isoformat(timespec="milliseconds")
        captured_at = captured_at or now
        datetime.fromisoformat(captured_at)
        with self._lock, self.db:
            row = self.db.execute("SELECT value FROM sequences WHERE device_id=?", (device_id,)).fetchone()
            seq = (row[0] if row else 0) + 1
            self.db.execute("INSERT INTO sequences(device_id,value) VALUES(?,?) ON CONFLICT(device_id) DO UPDATE SET value=excluded.value", (device_id, seq))
            values = (str(uuid.uuid4()), device_id, seq, qr, captured_at, period_version,
                      "pending", 0, None, None, None, now, now)
            self.db.execute("INSERT INTO events VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)", values)
        return Event(*values)

    def next_pending(self, device_id: str) -> Event | None:
        with self._lock:
            row = self.db.execute("SELECT * FROM events WHERE device_id=? AND status='pending' ORDER BY sequence LIMIT 1", (device_id,)).fetchone()
            return Event(**dict(row)) if row else None

    def update(self, event_id: str, status: str, receipt: str | None, code: str | None, error: str | None):
        if status not in {"pending", *TERMINAL}:
            raise ValueError("estado inválido")
        now = datetime.now(timezone.utc).astimezone().isoformat(timespec="milliseconds")
        safe_error = error[:300] if error else None
        with self._lock, self.db:
            self.db.execute("UPDATE events SET status=?,server_receipt_id=COALESCE(?,server_receipt_id),last_code=?,last_error=?,attempts=attempts+1,updated_at=? WHERE event_id=?", (status, receipt, code, safe_error, now, event_id))

    def count_pending(self) -> int:
        with self._lock:
            return self.db.execute("SELECT count(*) FROM events WHERE status='pending'").fetchone()[0]

    def get(self, event_id: str) -> Event:
        with self._lock:
            return Event(**dict(self.db.execute("SELECT * FROM events WHERE event_id=?", (event_id,)).fetchone()))

    def export_diagnostic(self, target: Path):
        with self._lock, target.open("w", newline="", encoding="utf-8") as fh:
            writer = csv.writer(fh)
            writer.writerow(("event_id", "device_id", "sequence", "captured_at", "status", "attempts", "last_code", "receipt", "qr_sha256_12"))
            for row in self.db.execute("SELECT * FROM events ORDER BY device_id,sequence"):
                writer.writerow((row["event_id"], row["device_id"], row["sequence"], row["captured_at"], row["status"], row["attempts"], row["last_code"], row["server_receipt_id"], hashlib.sha256(row["qr"].encode()).hexdigest()[:12]))


class ApiClient:
    def __init__(self, endpoint: str, token_provider: Callable[[], str], timeout: float = 8):
        self.endpoint, self.token_provider, self.timeout = endpoint.rstrip("/"), token_provider, timeout

    def send(self, event: Event) -> tuple[int, dict]:
        payload = {"protocol_version": 1, "event_id": event.event_id, "sequence": event.sequence,
                   "qr": event.qr, "captured_at": event.captured_at, "period_version": event.period_version}
        request = urllib.request.Request(self.endpoint, json.dumps(payload).encode(), {"Content-Type": "application/json", "Authorization": "Bearer " + self.token_provider()}, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                return response.status, json.load(response)
        except urllib.error.HTTPError as exc:
            try:
                return exc.code, json.load(exc)
            except Exception:
                return exc.code, {"message": "Respuesta HTTP no interpretable"}


class Synchronizer:
    def __init__(self, store: QueueStore, client: ApiClient, device_id: str):
        self.store, self.client, self.device_id = store, client, device_id

    def once(self) -> str:
        event = self.store.next_pending(self.device_id)
        if not event:
            return "empty"
        try:
            http, body = self.client.send(event)
        except (OSError, TimeoutError, urllib.error.URLError) as exc:
            self.store.update(event.event_id, "pending", None, "network", type(exc).__name__)
            return "pending"
        state = body.get("status")
        if http == 429 or http >= 500:
            target = "pending"
        elif state in ("accepted", "duplicate") and 200 <= http < 300:
            target = "synced"
        elif state == "review":
            target = "review"
        else:
            target = "rejected"
        self.store.update(event.event_id, target, body.get("receipt_id"), str(http), body.get("message"))
        return target

    @staticmethod
    def backoff(attempt: int) -> float:
        return min(300, 2 ** min(attempt, 8)) * random.uniform(.8, 1.2)
