"""
geolocator.py — Procesamiento en segundo plano de metadatos de vídeo.

Responsabilidades:
  1. Rellenar `duration` (segundos) en entradas con duration == 0 usando ffprobe.
  2. Purgar del índice entradas con fichero desaparecido, tamaño < 1 KB
     o duración irrecuperable (no queremos registros basura en la DB).
  3. Intentar extraer `place_name` desde metadatos GPS/ISO6709 si están presentes.

Se ejecuta con baja prioridad vía:  nice -n 19 python geolocator.py
"""

import os
import json
import time
import subprocess
from datetime import datetime
from database import load_json, save_json, log_error, DB_FILE

# ── Configuración ─────────────────────────────────────────────────────────────
BATCH_SIZE: int = 20         # Vídeos por lote antes de guardar a disco
SLEEP_BETWEEN_BATCHES: float = 2.0   # Segundos entre lotes (cede CPU)
SLEEP_IDLE: float = 300.0    # Segundos de espera cuando no hay trabajo pendiente
SLEEP_CYCLE: float = 60.0    # Segundos entre pasadas completas
FFPROBE_TIMEOUT: int = 10    # Timeout máximo por llamada a ffprobe
MIN_SIZE_BYTES: int = 100 * 1024  # Ficheros < 100 KB se descartan


def _get_duration(filepath: str) -> float:
    """Obtiene duración en segundos vía ffprobe. Devuelve 0.0 si falla."""
    cmd = [
        "ffprobe", "-v", "quiet",
        "-print_format", "json",
        "-show_format",
        filepath,
    ]
    try:
        result = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=FFPROBE_TIMEOUT,
        )
        data = json.loads(result.stdout)
        raw = data.get("format", {}).get("duration", 0) or 0
        return float(raw)
    except Exception:
        return 0.0


def _get_gps_place(filepath: str) -> str:
    """
    Intenta extraer la etiqueta de ubicación ISO 6709 / location de los
    metadatos del contenedor. Devuelve cadena vacía si no hay datos GPS.
    """
    cmd = [
        "ffprobe", "-v", "quiet",
        "-print_format", "json",
        "-show_format",
        filepath,
    ]
    try:
        result = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=FFPROBE_TIMEOUT,
        )
        data = json.loads(result.stdout)
        tags: dict = data.get("format", {}).get("tags", {})
        # iOS/Android suelen escribir en estas claves
        for key in ("location", "com.apple.quicktime.location.ISO6709",
                    "com.android.capture.fps"):
            val = tags.get(key, "")
            if val and val.strip():
                return val.strip()
    except Exception:
        pass
    return ""


def _process_batch(videos: list[dict], pending_indices: list[int]) -> tuple[list[dict], bool]:
    """
    Procesa un lote de índices y devuelve la lista de vídeos modificada
    junto con un flag indicando si hubo cambios.
    """
    changed = False

    for idx in pending_indices:
        v = videos[idx]
        path = v.get("path", "")

        # Verificar existencia y tamaño mínimo
        try:
            size_bytes = os.path.getsize(path)
        except OSError:
            # Fichero desaparecido: marcar como irrecuperable (no purgar para evitar
            # el bucle purga → el scanner lo reindexaría → el geolocator lo vuelve a ver)
            videos[idx]["geo_failed"] = True
            changed = True
            continue

        if size_bytes < MIN_SIZE_BYTES:
            videos[idx]["geo_failed"] = True
            changed = True
            continue

        # Obtener duración
        duration = _get_duration(path)
        if duration <= 0.0:
            # ffprobe no puede leer el fichero: marcar como irrecuperable
            # NO purgar para no generar el bucle infinito con el scanner
            videos[idx]["geo_failed"] = True
            log_error(
                "geolocator",
                f"Entrada irrecuperable (ffprobe falla): {path}",
            )
            changed = True
            continue

        videos[idx]["duration"] = round(duration, 2)
        changed = True

        # Intentar place_name solo si sigue vacío
        if not v.get("place_name"):
            place = _get_gps_place(path)
            if place:
                videos[idx]["place_name"] = place

    return videos, changed


def main() -> None:
    print(f"[geolocator] Iniciado: {datetime.now().isoformat()}", flush=True)

    while True:
        videos: list[dict] = load_json(DB_FILE, [])

        # Índices de entradas que necesitan duración y aún no han fallado definitivamente
        pending: list[int] = [
            i for i, v in enumerate(videos)
            if float(v.get("duration", 0) or 0) <= 0.0
            and not v.get("geo_failed", False)
            and os.path.exists(v.get("path", ""))
        ]

        if not pending:
            print(
                f"[geolocator] Sin trabajo pendiente. Próxima comprobación en "
                f"{int(SLEEP_IDLE)}s.",
                flush=True,
            )
            time.sleep(SLEEP_IDLE)
            continue

        print(
            f"[geolocator] {len(pending)} vídeos sin duración. "
            f"Procesando en lotes de {BATCH_SIZE}…",
            flush=True,
        )

        for batch_start in range(0, len(pending), BATCH_SIZE):
            batch_indices = pending[batch_start : batch_start + BATCH_SIZE]
            videos, changed = _process_batch(videos, batch_indices)

            if changed:
                save_json(DB_FILE, videos)
                # Recalcular pending excluyendo entradas ya marcadas como fallidas
                pending = [
                    i for i, v in enumerate(videos)
                    if float(v.get("duration", 0) or 0) <= 0.0
                    and not v.get("geo_failed", False)
                    and os.path.exists(v.get("path", ""))
                ]

            time.sleep(SLEEP_BETWEEN_BATCHES)

        print(
            f"[geolocator] Pasada completada: {datetime.now().isoformat()}. "
            f"Esperando {int(SLEEP_CYCLE)}s…",
            flush=True,
        )
        time.sleep(SLEEP_CYCLE)


if __name__ == "__main__":
    main()
