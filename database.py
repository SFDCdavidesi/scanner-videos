import os
import json
from datetime import datetime

# Apuntamos al fichero real con los 13MB de datos
DB_FILE = "videos_db.json"
STATE_FILE = "scanner_state.json"
FOLDER_STATS_FILE = "folder_stats.json"
ERROR_LOG_FILE = "errors.json"


def load_json(filepath: str, default):
    if not os.path.exists(filepath):
        return default
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def save_json(filepath: str, data) -> None:
    """Escritura atómica mediante fichero temporal + os.replace.

    Garantiza que si el proceso muere a mitad de la escritura el fichero
    original permanece intacto y no queda corrupto.
    Usa indent=2 en lugar de indent=4 para reducir el tamaño en disco ~30%.
    """
    tmp = filepath + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        os.replace(tmp, filepath)   # atómico en Linux/Synology
    except Exception as e:
        log_error("database", str(e))
        try:
            os.remove(tmp)
        except OSError:
            pass


def log_error(context: str, message: str) -> None:
    errors = load_json(ERROR_LOG_FILE, [])
    errors.append({
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "context": context,
        "message": message,
    })
    if len(errors) > 200:
        errors = errors[-200:]
    try:
        tmp = ERROR_LOG_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(errors, f, indent=2, ensure_ascii=False)
        os.replace(tmp, ERROR_LOG_FILE)
    except Exception:
        pass
