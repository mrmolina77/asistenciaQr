import getpass
import json
from pathlib import Path

from lector.core import data_directory
from lector.credentials import save_token

directory = data_directory(); directory.mkdir(parents=True, exist_ok=True, mode=0o700)
endpoint = input("Endpoint HTTPS: ").strip()
device = input("Identificador del dispositivo: ").strip()
if not endpoint.startswith("https://") or not device:
    raise SystemExit("Se requiere endpoint HTTPS e identificador")
(directory / "config.json").write_text(json.dumps({"endpoint": endpoint, "device_id": device}, indent=2), encoding="utf-8")
save_token(directory / "token.dpapi", getpass.getpass("Token (no se mostrará): "))
print("Configuración y credencial DPAPI guardadas en", directory)
