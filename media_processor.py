"""
media_processor.py — Procesamiento en segundo plano de metadatos de vídeo.

Responsabilidades:
  1. Rellenar `duration` (segundos) en entradas con duration == 0 usando ffprobe.
  2. Extraer una miniatura JPEG (~400 px ancho) en el instante duration/2 para
     cada vídeo que aún no tenga `thumb`, guardándola en static/thumbs/.
  3. Intentar extraer `place_name` desde metadatos GPS/ISO6709 si están presentes.
  4. Marcar con `geo_failed: true` entradas irrecuperables para no entrar en bucle.

Se ejecuta con baja prioridad vía:  nice -n 19 python media_processor.py
"""

import os
import json
import time
import hashlib
import subprocess
from datetime import datetime
from database import load_json, save_json, log_error, DB_FILE

# ── Configuración ─────────────────────────────────────────────────────────────
BATCH_SIZE: int = 50                  # vídeos por lote (era 20)
SLEEP_BETWEEN_BATCHES: float = 0.5   # segundos entre lotes (era 2.0)
SLEEP_IDLE: float = 300.0
SLEEP_CYCLE: float = 60.0
FFPROBE_TIMEOUT: int = 10
FFMPEG_TIMEOUT: int = 15
MIN_SIZE_BYTES: int = 100 * 1024  # 100 KB

THUMBS_DIR: str = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static", "thumbs")
THUMB_WIDTH: int = 400


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
    Intenta extraer la etiqueta de ubicación ISO6709 / location de los
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
        for key in ("location", "com.apple.quicktime.location.ISO6709",
                    "com.android.capture.fps"):
            val = tags.get(key, "")
            if val and val.strip():
                return val.strip()
    except Exception:
        pass
    return ""


def _get_thumbnail(filepath: str, duration: float) -> str | None:
    """
    Extrae una miniatura JPEG de ~400 px en el instante duration/2.
    Devuelve la ruta relativa "thumbs/HASH.jpg" o None si falla.
    """
    os.makedirs(THUMBS_DIR, exist_ok=True)

    name_hash = hashlib.md5(filepath.encode()).hexdigest()[:16]
    thumb_filename = f"{name_hash}.jpg"
    thumb_path = os.path.join(THUMBS_DIR, thumb_filename)

    if os.path.exists(thumb_path):
        return f"thumbs/{thumb_filename}"

    # Posicionarse en la mitad del vídeo (mínimo 0.5 s para evitar frames negros)
    seek_time = max(0.5, duration / 2)

    cmd = [
        "ffmpeg", "-y",
        "-ss", str(seek_time),
        "-i", filepath,
        "-frames:v", "1",
        "-vf", f"scale={THUMB_WIDTH}:-2",   # -2: altura par, mantiene ratio
        "-q:v", "3",                          # calidad JPEG (~buena)
        thumb_path,
    ]
    try:
        result = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=FFMPEG_TIMEOUT,
        )
        if result.returncode == 0 and os.path.exists(thumb_path):
            return f"thumbs/{thumb_filename}"
    except Exception:
        pass
    return None


def _process_batch(videos: list[dict], pending_indices: list[int]) -> tuple[list[dict], bool]:
    """
    Procesa un lote de índices: rellena duration, extrae thumb y place_name.
    Marca geo_failed en entradas irrecuperables en lugar de eliminarlas.
    """
    changed = False

    for idx in pending_indices:
        v = videos[idx]
        path = v.get("path", "")

        # Verificar existencia y tamaño mínimo
        try:
            size_bytes = os.path.getsize(path)
        except OSError:
            videos[idx]["geo_failed"] = True
            changed = True
            continue

        if size_bytes < MIN_SIZE_BYTES:
            videos[idx]["geo_failed"] = True
            changed = True
            continue

        duration = float(v.get("duration", 0) or 0)

        # Rellenar duración si falta
        if duration <= 0.0:
            duration = _get_duration(path)
            if duration <= 0.0:
                videos[idx]["geo_failed"] = True
                log_error(
                    "media_processor",
                    f"Entrada irrecuperable (ffprobe falla): {path}",
                )
                changed = True
                continue
            videos[idx]["duration"] = round(duration, 2)
            changed = True

        # Extraer place_name si falta
        if not v.get("place_name"):
            place = _get_gps_place(path)
            if place:
                videos[idx]["place_name"] = place
                changed = True

        # Extraer miniatura si falta
        if not v.get("thumb"):
            thumb = _get_thumbnail(path, duration)
            if thumb:
                videos[idx]["thumb"] = thumb
                changed = True

    return videos, changed


def main() -> None:
    print(f"[media_processor] Iniciado: {datetime.now().isoformat()}", flush=True)

    while True:
        videos: list[dict] = load_json(DB_FILE, [])

        # Entradas que necesitan algún procesamiento y no han fallado definitivamente
        pending: list[int] = [
            i for i, v in enumerate(videos)
            if not v.get("geo_failed", False)
            and os.path.exists(v.get("path", ""))
            and (
                float(v.get("duration", 0) or 0) <= 0.0
                or not v.get("thumb")
            )
        ]

        if not pending:
            print(
                f"[media_processor] Sin trabajo pendiente. Próxima comprobación en "
                f"{int(SLEEP_IDLE)}s.",
                flush=True,
            )
            time.sleep(SLEEP_IDLE)
            continue

        print(
            f"[media_processor] {len(pending)} vídeos pendientes. "
            f"Procesando en lotes de {BATCH_SIZE}…",
            flush=True,
        )

        for batch_start in range(0, len(pending), BATCH_SIZE):
            batch_indices = pending[batch_start : batch_start + BATCH_SIZE]
            videos, changed = _process_batch(videos, batch_indices)

            if changed:
                save_json(DB_FILE, videos)
                pending = [
                    i for i, v in enumerate(videos)
                    if not v.get("geo_failed", False)
                    and os.path.exists(v.get("path", ""))
                    and (
                        float(v.get("duration", 0) or 0) <= 0.0
                        or not v.get("thumb")
                    )
                ]

            time.sleep(SLEEP_BETWEEN_BATCHES)

        print(
            f"[media_processor] Pasada completada: {datetime.now().isoformat()}. "
            f"Esperando {int(SLEEP_CYCLE)}s…",
            flush=True,
        )
        time.sleep(SLEEP_CYCLE)


if __name__ == "__main__":
    main()
