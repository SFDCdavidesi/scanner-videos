import os
import json
import subprocess
import argparse
import re
import time
from datetime import datetime
from typing import List, Dict, Any, Tuple
from database import load_json, save_json, log_error, DB_FILE, STATE_FILE, GEO_CACHE_FILE, FOLDER_STATS_FILE

try:
    from geopy.geocoders import Nominatim
    GEOPY_AVAILABLE = True
except ImportError:
    GEOPY_AVAILABLE = False

CARPETAS_COMPARTIDAS = [
    "/media/volume1",
    "/media/volume2",
    "/media/volumeUSB1",
    "/media/volumeUSB2",
    "/media/volumeUSB3"
]

MIN_DURATION_SECONDS = 3.0
VIDEO_EXTENSIONS = {".mp4", ".mkv", ".avi", ".mov", ".wmv", ".flv", ".webm", ".m4v", ".3gp"}
EXCLUDE_DIRS = {"@appstore", "@database", "@docker", "@eaDir", "#recycle", "$RECYCLE.BIN", "EFI", "PlexMediaServer", "homes"}

def get_video_metadata(filepath: str, geo_cache: Dict[str, str]) -> Tuple[float, str, str, str]:
    cmd = ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", "-show_streams", filepath]
    try:
        result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=10)
        if result.returncode != 0:
            return 0.0, "No disponible", "", ""
        data = json.loads(result.stdout)
        duration = float(data.get("format", {}).get("duration", 0.0))
        tags = data.get("format", {}).get("tags", {})
        capture_date = tags.get("creation_time") or tags.get("date")
        if capture_date:
            try:
                dt = datetime.fromisoformat(capture_date.replace("Z", "+00:00"))
                capture_date = dt.strftime("%Y-%m-%d %H:%M:%S")
            except Exception:
                pass
        return duration, capture_date or "No disponible", "", ""
    except Exception:
        return 0.0, "No disponible", "", ""

def scan_nas(resume: bool = False, clean_first: bool = False) -> None:
    print("[ESCÁNER] Arrancando escaneo optimizado...", flush=True)
    videos: List[Dict[str, Any]] = load_json(DB_FILE, [])
    geo_cache: Dict[str, str] = load_json(GEO_CACHE_FILE, {})
    folder_stats: Dict[str, Dict[str, Any]] = load_json(FOLDER_STATS_FILE, {})
    existing_paths = {v["path"] for v in videos}
    
    start_timestamp = time.time()
    files_scanned = 0

    state = load_json(STATE_FILE, {
        "last_share": None, "last_folder": None, "is_completed": False,
        "files_scanned": 0, "start_timestamp": start_timestamp
    })

    for share in CARPETAS_COMPARTIDAS:
        if not os.path.exists(share):
            print(f"[ESCÁNER] Omitiendo {share} (No existe).", flush=True)
            continue

        print(f"[ESCÁNER] Analizando volumen: {share}", flush=True)
        state["last_share"] = share
        save_json(STATE_FILE, state)

        try:
            for root, dirs, files in os.walk(share):
                # PODA DE DIRECTORIOS: Evita que os.walk entre en carpetas del sistema o pesadas
                dirs[:] = [d for d in dirs if not d.startswith("@") and d not in EXCLUDE_DIRS]

                if "/home/" in root or root.endswith("/home"):
                    continue

                state["last_folder"] = root
                state["files_scanned"] = files_scanned
                save_json(STATE_FILE, state)

                folder_file_count = len(files)
                folder_video_count = 0

                for file in files:
                    files_scanned += 1
                    state["files_scanned"] = files_scanned

                    ext = os.path.splitext(file)[1].lower()
                    if ext in VIDEO_EXTENSIONS:
                        full_path = os.path.join(root, file)
                        if full_path in existing_paths:
                            continue
                        try:
                            stat = os.stat(full_path)
                            duration, capture_date, _, _ = get_video_metadata(full_path, geo_cache)
                            if duration >= MIN_DURATION_SECONDS:
                                folder_video_count += 1
                                file_date = datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M:%S")
                                size_mb = round(stat.st_size / (1024 * 1024), 2)
                                videos.append({
                                    "id": len(videos) + 1, "name": file, "path": full_path,
                                    "duration": round(duration, 1), "capture_date": capture_date,
                                    "file_date": file_date, "size_mb": size_mb,
                                    "location": "", "place_name": ""
                                })
                                existing_paths.add(full_path)
                                save_json(DB_FILE, videos)
                                print(f"[VÍDEO] Encontrado: {file}", flush=True)
                        except Exception:
                            pass

                if folder_file_count > 0 or folder_video_count > 0:
                    folder_stats[root] = {
                        "path": root, "total_files": folder_file_count,
                        "videos_found": folder_video_count,
                        "scanned_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    }
                    save_json(FOLDER_STATS_FILE, folder_stats)
                save_json(STATE_FILE, state)

        except Exception as e:
            err_msg = f"Error en {share}: {e}"
            print(f"[ERROR] {err_msg}", flush=True)
            log_error("Recorrido", err_msg)

    state["is_completed"] = True
    state["last_run"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    save_json(STATE_FILE, state)
    print("=== ESCANEO FINALIZADO ===", flush=True)

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--clean-deleted", action="store_true")
    args = parser.parse_args()
    scan_nas(resume=args.resume, clean_first=args.clean_deleted)
