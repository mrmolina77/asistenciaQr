# Lector QR offline de Edukado

Aplicación de escritorio (Python 3.11+, Tkinter y `sqlite3`) para lectores USB que
se comportan como teclado. **Una lectura guardada offline no es una asistencia
confirmada**: solo cambia a confirmada después de la respuesta idempotente del servidor.

> Estado del repositorio: el checkout recibido solo contenía `.gitignore`; no había
> aplicación Laravel, ZIP, `composer.json`, módulo `Modules/Asistencia` ni pruebas PHP.
> Por seguridad no se inventó una integración desacoplada de las reglas reales. Este
> documento deja el contrato que debe implementarse al recuperar Edukado2.

## Instalación y aprovisionamiento (Windows)

1. Instale Python 3.11 o posterior incluyendo Tcl/Tk.
2. Desde esta carpeta ejecute `py provision.py`. Introduzca una URL HTTPS y el token
   de **dispositivo**, entregado una sola vez por un administrador. El token se cifra
   con DPAPI para el usuario actual; no se escribe en JSON ni logs.
3. Ejecute `py run.py`. Configure el lector para terminar en Enter.

Los datos se guardan en `%LOCALAPPDATA%\EdukadoLector`: `cola.sqlite3`, sus archivos
WAL, configuración no secreta y credencial DPAPI. La aplicación intenta restringir el
directorio al usuario; el administrador debe verificar ACL NTFS en el despliegue.
No copie `token.dpapi` a otro usuario/equipo: DPAPI impedirá descifrarlo.

## Operación y estados

Cada Enter válido crea primero una transacción SQLite (`synchronous=FULL`, WAL,
secuencia monotónica por dispositivo) y recién después despierta el hilo de red.
`pending` se reintenta FIFO; `synced`, `review` y `rejected` son terminales y nunca se
borran automáticamente. Timeout, desconexión, 429 y 5xx conservan `pending`. El botón
**Exportar diagnóstico** produce un CSV con hash corto del QR, nunca QR o token.

El campo conserva foco mientras la sincronización ocurre fuera del hilo de Tkinter.
Para una prueba manual: abra la app sin red, escanee varios códigos, reinicie, confirme
el contador, restaure la red y pulse **Sincronizar ahora**. Verifique en Edukado los
recibos y el orden. Un evento en revisión requiere resolución desde Edukado.

## Contrato requerido en Edukado2

`POST /api/v1/attendance/offline-events`, JSON, `Authorization: Bearer <token>`, límite
sugerido 30/minuto/dispositivo y máximo 8 KiB. Un evento por petición:

```json
{"protocol_version":1,"event_id":"uuid","sequence":42,"qr":"payload firmado",
 "captured_at":"2026-09-24T08:03:12-04:00","period_version":"2026-2027:v3"}
```

Respuesta JSON: `status` (`accepted`, `duplicate`, `review`, `rejected`), `event_id`,
`receipt_id` estable, `processed_at` con offset y `message` seguro. `accepted` y
`duplicate` usan 200; revisión 202; validación definitiva 422; credencial inválida o
revocada 401/403; payload distinto para el mismo evento o secuencia 409; límite 429.
Solo 429/5xx/timeouts son reintentables.

La implementación Laravel debe vincular el token hash a un dispositivo habilitado,
ignorar cualquier `device_id` del cuerpo, y almacenar recibo, hash canónico del payload,
secuencia, captura convertida explícitamente a `America/Caracas` y `received_at`, con
índices únicos `(device_id,event_id)` y `(device_id,sequence)`. Dentro de **una sola
transacción**, debe bloquear/crear el recibo y llamar al servicio de asistencia existente
pasándole `captured_at`; un reintento idéntico devuelve el recibo sin repetir asistencia,
pase o notificación. Payload diferente produce 409 sin mutación.

Antes de aplicar reglas se compara período observado/vigente y orden. Capturas futuras,
demasiado antiguas, en feriado, con período cambiado, o llegadas después de la ausencia
automática deben persistirse como `review` sin crear asistencia ni revertir ausencias o
avisos silenciosamente. La política de desfase/antigüedad debe ser configurable. Las
reglas de elegibilidad, intervalo, entrada/salida, tardanza, pases y notificaciones deben
seguir en `AttendanceRegistrationService`, nunca en Python.

## Pruebas, respaldo y recuperación

```powershell
py -m unittest discover -s tests -v
```

Cierre la aplicación antes del respaldo y copie juntos `cola.sqlite3`, `-wal` y `-shm`,
o use la API de backup de SQLite. Restaure en el mismo perfil antes de iniciar. La pérdida
del perfil/disco pierde la cola; use respaldo cifrado y controles de acceso. Si hay
corrupción, preserve los archivos, no cree una cola encima y entregue el diagnóstico a
soporte. Para revocar un lector, deshabilite su credencial en Edukado y aprovisione otra;
las respuestas 401/403 quedan terminales para revisión operacional.

## Empaquetado

En Windows: `build_exe.bat`. Instala la versión fijada de PyInstaller y crea
`dist\EdukadoLector.exe` en modo consola para conservar errores visibles. Pruebe y firme
el ejecutable antes de distribución; PyInstaller no se requiere en la PC destino.
