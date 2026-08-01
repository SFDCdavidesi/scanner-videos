import os
import sys
import argparse
import json
from datetime import datetime
from database import load_json, save_json, log_error, DB_FILE, STATE_FILE, FOLDER_STATS_FILE

def get_video_metadata(filepath):
    size_bytes = os.path.getsize(filepath)
    size_mb = round(size_bytes / (1024 * 1024), 2)
    file_mtime = os.path.getmtime(filepath)
    file_date = datetime.fromtimestamp(file_mtime).strftime('%Y-%m-%d %H:%M:%S')
    
    # Intento básico de extracción con ffprobe si está disponible
    duration = 0.0
    try:
        import subprocess
        cmd = ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", filepath]
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=3)
        data = json.loads(res.stdout)
        duration = float(data.get("format", {}).get("duration", 0))
    except:
        pass

    return {
        "size_mb": size_mb,
        "file_date": file_date,
        "capture_date": file_date,
        "duration": duration,
        "place_name": ""
    }

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--clean-deleted", action="store_true")
    parser.add_argument("--path", type=str, default=None)
    args = parser.parse_args()

    state = load_json(STATE_FILE, {"is_completed": False, "files_scanned": 0, "start_timestamp": time.time()})
    state["is_completed"] = False
    state["start_timestamp"] = time.time()
    save_json(STATE_FILE, state)

    videos = load_json(DB_FILE, [])
    folder_stats = load_json(FOLDER_STATS_FILE, {})

    # Definir raíces de búsqueda por defecto o usar el path específico indicado
    scan_roots = [args.path] if args.path else ["/volume1", "/volume2", "/media"]
    valid_extensions = {".mp4", ".mov", ".avi", ".mkv", ".webm", ".m4v"}

    existing_paths = {v["path"] for v in videos}
    max_id = max([v.get("id", 0) for v in videos], default=0)

    files_count = 0
    for root_dir in scan_roots:
        if not os.path.exists(root_dir):
            continue
        for dirpath, _, filenames in os.walk(root_dir):
            state["last_folder"] = dirpath
            save_json(STATE_FILE, state)
            
            vids_in_folder = 0
            total_in_folder = len(filenames)
            
            for f in filenames:
                ext = os.path.splitext(f)[1].lower()
                if ext in valid_extensions:
                    files_count += 1
                    full_path = os.path.join(dirpath, f)
                    if full_path not in existing_paths:
                        max_id += 1
                        meta = get_video_metadata(full_path)
                        new_entry = {
                            "id": max_id,
                            "name": f,
                            "path": full_path,
                            "size_mb": meta["size_mb"],
                            "duration": meta["duration"],
                            "capture_date": meta["capture_date"],
                            "file_date": meta["file_date"],
                            "place_name": meta["place_name"]
                        }
                        videos.append(new_entry)
                        existing_paths.add(full_path)
                    vids_in_folder += 1

            folder_stats[dirpath] = {
                "path": dirpath,
                "total_files": total_in_folder,
                "videos_found": vids_in_folder,
                "scanned_at": datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            }
            save_json(DB_FILE, videos)
            save_json(FOLDER_STATS_FILE, folder_stats)
            state["files_scanned"] = files_count
            save_json(STATE_FILE, state)

    state["is_completed"] = True
    state["last_run"] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    save_json(STATE_FILE, state)

if __name__ == "__main__":
    import time
    main()
