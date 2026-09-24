from __future__ import annotations

import json
import queue
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, ttk

from .core import ApiClient, QueueStore, Synchronizer, data_directory
from .credentials import load_token


class App:
    def __init__(self, root: tk.Tk, config: dict, store=None, client=None):
        self.root, self.config = root, config
        self.store = store or QueueStore(data_directory() / "cola.sqlite3")
        self.client = client or ApiClient(config["endpoint"], lambda: load_token(data_directory() / "token.dpapi"))
        self.sync = Synchronizer(self.store, self.client, config["device_id"])
        self.results: queue.Queue[str] = queue.Queue()
        self.busy = False
        self.last_scan = ("", 0.0)
        self.failures = 0
        root.title("Edukado · Lector QR sin conexión")
        root.geometry("680x410")
        frame = ttk.Frame(root, padding=18); frame.pack(fill="both", expand=True)
        ttk.Label(frame, text="Lector de asistencia", font=("Segoe UI", 18, "bold")).pack(anchor="w")
        self.server = tk.StringVar(value="Servidor: verificando…")
        self.pending = tk.StringVar(); self.last = tk.StringVar(value="Listo para leer")
        ttk.Label(frame, textvariable=self.server).pack(anchor="w", pady=(8, 0))
        ttk.Label(frame, textvariable=self.pending).pack(anchor="w")
        ttk.Label(frame, text="Escanee el código y presione Enter:").pack(anchor="w", pady=(18, 3))
        self.entry = ttk.Entry(frame, font=("Consolas", 14), show="•")
        self.entry.pack(fill="x"); self.entry.bind("<Return>", self.capture)
        self.message = tk.Label(frame, textvariable=self.last, font=("Segoe UI", 13), pady=18)
        self.message.pack(fill="x")
        buttons = ttk.Frame(frame); buttons.pack(fill="x")
        ttk.Button(buttons, text="Sincronizar ahora", command=self.start_sync).pack(side="left")
        ttk.Button(buttons, text="Exportar diagnóstico", command=self.export).pack(side="left", padx=8)
        ttk.Label(frame, text="Pendiente ≠ asistencia confirmada. La cola no se elimina automáticamente.").pack(anchor="w", pady=20)
        self.refresh(); self.entry.focus_set(); root.after(150, self.poll); root.after(500, self.start_sync)

    def capture(self, _event=None):
        raw = self.entry.get(); self.entry.delete(0, "end")
        now = time.monotonic()
        if raw == self.last_scan[0] and now - self.last_scan[1] < 1.5:
            self.show("Enter repetido ignorado; la lectura anterior permanece guardada", "#9a6700")
            self.entry.focus_set()
            return
        try:
            saved = self.store.enqueue(self.config["device_id"], raw, self.config.get("period_version"))
            self.last_scan = (raw, now)
            self.show("Guardada localmente; pendiente de sincronizar", "#9a6700"); self.root.bell()
            self.start_sync()
        except Exception as exc:
            self.show("NO se guardó: " + str(exc), "#b42318"); self.root.bell()
        finally:
            self.entry.focus_set(); self.refresh()

    def start_sync(self):
        if self.busy: return
        self.busy = True
        def work():
            try: self.results.put(self.sync.once())
            except Exception as exc: self.results.put("error:" + type(exc).__name__)
        threading.Thread(target=work, daemon=True, name="edukado-sync").start()

    def poll(self):
        try:
            result = self.results.get_nowait(); self.busy = False
            labels = {"empty": ("Servidor disponible; cola al día", "#067647"), "synced": ("Aceptada por Edukado", "#067647"), "review": ("Requiere revisión", "#9a6700"), "rejected": ("Rechazada por Edukado", "#b42318"), "pending": ("Sin conexión; lectura guardada", "#9a6700")}
            text, color = labels.get(result, ("No se pudo contactar Edukado", "#b42318"))
            self.server.set("Servidor: " + ("conectado" if result in ("empty", "synced", "review", "rejected") else "no disponible")); self.show(text, color)
            self.refresh()
            if result in ("pending",) or result.startswith("error:"):
                self.failures += 1
            else:
                self.failures = 0
            if self.store.count_pending():
                self.root.after(int(self.sync.backoff(self.failures) * 1000), self.start_sync)
        except queue.Empty: pass
        self.entry.focus_set(); self.root.after(150, self.poll)

    def show(self, text, color): self.last.set(text); self.message.configure(fg=color)
    def refresh(self): self.pending.set(f"Lecturas pendientes: {self.store.count_pending()}")
    def export(self):
        filename = filedialog.asksaveasfilename(defaultextension=".csv")
        if filename: self.store.export_diagnostic(Path(filename)); self.show("Diagnóstico exportado sin QR ni token", "#067647")


def main():
    directory = data_directory(); directory.mkdir(parents=True, exist_ok=True)
    config = json.loads((directory / "config.json").read_text(encoding="utf-8"))
    root = tk.Tk(); App(root, config); root.mainloop()


if __name__ == "__main__": main()
