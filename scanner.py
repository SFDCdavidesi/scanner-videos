import os
import json
import subprocess
import argparse
import re
import time
from datetime import datetime
from typing import List, Dict, Any, Tuple
from concurrent.futures import ThreadPoolExecutor, as_completed
import threading
from database import load_json, save_json, log_error, DB_FILE, STATE_FILE, FOLDER_STATS_FILE

CARPETAS_COMPARTIDAS = [
    "/media/volume1",
    "/media/volume2",
    "/media/volumeUSB1",
    "/media/volumeUSB2",
    "/media/volumeUSB3"
]

MIN_DURATION_SECONDS = 3.0
SAVE_INTERVAL_SECONDS = 60.0  # Guardar en disco solo 1 vez cada 60 segundos
VIDEO_EXTENSIONS = {".mp4", ".mkv", ".avi", ".mov", ".wmv", ".flv", ".webm", ".m4v", ".3gp"}
EXCLUDE_DIRS = {"@appstore", "@database", "@docker", "@eaDir", "#recycle", "$RECYCLE.BIN", "EFI", "PlexMediaServer"}

db_lock = threading.Lock()

def parse_gps_from_tags(tags: dict) -> Tuple[float, float]:
    loc_str = tags.get("location") or tags.get("com.apple.quicktime.location.ISO6709") or tags.get("location-eng")
    if not loc_str and tags:
        for k, v in tags.items():
            if "location" in k.lower() and isinstance(v, str):
                loc_str = v
                break
    if not loc_str:
        return None, None
    
    match = re.search(r'([+-]\d+\.\d+)([+-]\d+\.\d+)', loc_str)
    if match:
        try:
            return float(match.group(1)), float(match.group(2))
        except ValueError:
            pass
    return None, None

def get_video_metadata(filepath: str) -> Tuple[float, str, float, float]:
    cmd = ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", filepath]
    try:
        result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=5)
        if result.returncode != 0:
            return 0.0, "No disponible", None, None
        data = json.loads(result.stdout)
        format_info = data.get("format", {})
        duration = float(format_info.get("duration", 0.0))
        tags = format_info.get("tags", {})
        
        capture_date = tags.get("creation_time") or tags.get("date")
        if capture_date:
            try:
                dt = datetime.fromisoformat(capture_date.replace("Z", "+00:00"))
                capture_date = dt.strftime("%Y-%m-%d %H:%M:%S")
            except Exception:
                pass
        
        lat, lon = parse_gps_from_tags(tags)
        return duration, capture_date or "No disponible", lat, lon
    except Exception:
        return 0.0, "No disponible", None, None

def process_single_video(full_path: str, file: str) -> Dict[str, Any]:
    try:
        stat = os.stat(full_path)
        duration, capture_date, lat, lon = get_video_metadata(full_path)
        if duration >= MIN_DURATION_SECONDS:
            file_date = datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M:%S")
            size_mb = round(stat.st_size / (1024 * 1024), 2)
            
            place_name = "Pendiente de ubicar"
            if lat is not None and lon is not None:
                place_name = "Geolocalizando..."

            return {
                "name": file, "path": full_path,
                "duration": round(duration, 1), "capture_date": capture_date,
                "file_date": file_date, "size_mb": size_mb,
                "lat": lat, "lon": lon, "place_name": place_name
            }
    except Exception:
        pass
    return None

def scan_nas(resume: bool = False, clean_first: bool = False) -> None:
    print("[ESCÁNER] Arrancando escaneo multihilo con guardado temporizado (1 vez/min)...", flush=True)
    videos: List[Dict[str, Any]] = load_json(DB_FILE, [])
    folder_stats: Dict[str, Dict[str, Any]] = load_json(FOLDER_STATS_FILE, {})
    existing_paths = {v["path"] for v in videos}
    
    start_timestamp = time.time()
    last_save_time = start_timestamp
    files_scanned = 0
    db_dirty = False
    stats_dirty = False

    state = load_json(STATE_FILE, {
        "last_share": None, "last_folder": None, "is_completed": False,
        "files_scanned": 0, "start_timestamp": start_timestamp
    })
    state["start_timestamp"] = start_timestamp
    save_json(STATE_FILE, state)

    # 8 hilos para procesar en paralelo y compensar lecturas USB/red
    max_workers = 8

    for share in CARPETAS_COMPARTIDAS:
        if not os.path.exists(share):
            continue

        state["last_share"] = share

        try:
            for root, dirs, files in os.walk(share):
                dirs[:] = [d for d in dirs if not d.startswith("@") and d not in EXCLUDE_DIRS]

                state["last_folder"] = root
                state["files_scanned"] = files_scanned

                folder_file_count = len(files)
                folder_video_count = 0
                pending_files = []

                for file in files:
                    files_scanned += 1
                    ext = os.path.splitext(file)[1].lower()
                    if ext in VIDEO_EXTENSIONS:
                        full_path = os.path.join(root, file)
                        if full_path not in existing_paths:
                            pending_files.append((full_path, file))

                # PROCESAMIENTO EN PARALELO EN RAM
                if pending_files:
                    folder_new_videos = []
                    with ThreadPoolExecutor(max_workers=max_workers) as executor:
                        futures = [executor.submit(process_single_video, fp, fn) for fp, fn in pending_files]
                        for future in as_completed(futures):
                            res = future.result()
                            if res:
                                folder_video_count += 1
                                folder_new_videos.append(res)
                    
                    if folder_new_videos:
                        with db_lock:
                            for res in folder_new_videos:
                                res["id"] = len(videos) + 1
                                videos.append(res)
                                existing_paths.add(res["path"])
                        db_dirty = True

                if folder_file_count > 0 or folder_video_count > 0:
                    folder_stats[root] = {
                        "path": root, "total_files": folder_file_count,
                        "videos_found": folder_video_count,
                        "scanned_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    }
                    stats_dirty = True

                # TEMPORIZADOR: Solo escribir en disco si ha pasado 1 minuto (60 segundos)
                current_time = time.time()
                if current_time - last_save_time >= SAVE_INTERVAL_SECONDS:
                    if db_dirty:
                        save_json(DB_FILE, videos)
                        db_dirty = False
                    if stats_dirty:
                        save_json(FOLDER_STATS_FILE, folder_stats)
                        stats_dirty = False
                    save_json(STATE_FILE, state)
                    last_save_time = current_time
                    print(f"[ESCÁNER] Volcado de progreso en disco -> {len(videos)} vídeos guardados.", flush=True)

        except Exception as e:
            log_error("Recorrido", f"Error en {share}: {e}")

    # VOLCADO FINAL OBLIGATORIO AL TERMINAR EL ESCANEO
    if db_dirty:
        save_json(DB_FILE, videos)
    if stats_dirty:
        save_json(FOLDER_STATS_FILE, folder_stats)
    
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
