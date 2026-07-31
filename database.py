import os
import json
import time
from datetime import datetime
from typing import Any, List, Dict

DB_FILE = "videos_db.json"
STATE_FILE = "scan_state.json"
GEO_CACHE_FILE = "geo_cache.json"
FOLDER_STATS_FILE = "folder_stats.json"
ERROR_LOG_FILE = "error_log.json"

def load_json(filepath: str, default: Any) -> Any:
    if not os.path.exists(filepath):
        return default
    for _ in range(5):
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                return json.load(f)
        except (PermissionError, OSError):
            time.sleep(0.15)
        except Exception:
            break
    return default

def save_json(filepath: str, data: Any) -> None:
    temp_filepath = f"{filepath}.tmp"
    for _ in range(6):
        try:
            with open(temp_filepath, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            if os.path.exists(filepath):
                os.replace(temp_filepath, filepath)
            else:
                os.rename(temp_filepath, filepath)
            return
        except (PermissionError, OSError):
            time.sleep(0.2)
        except Exception as e:
            print(f"[ERROR DB] No se pudo guardar {filepath}: {e}")
            break

def log_error(context: str, message: str) -> None:
    """Registra un error con marca de tiempo en el log persistente para la web."""
    try:
        errors: List[Dict[str, str]] = load_json(ERROR_LOG_FILE, [])
        error_entry = {
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "context": context,
            "message": str(message)
        }
        errors.insert(0, error_entry)  # Los más recientes arriba
        if len(errors) > 300:  # Límite máximo de registros
            errors = errors[:300]
        save_json(ERROR_LOG_FILE, errors)
    except Exception as e:
        print(f"[ERROR CRÍTICO] No se pudo escribir en el log de errores: {e}")
