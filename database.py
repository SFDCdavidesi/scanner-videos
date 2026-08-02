import os
import json
import tempfile
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


def save_json(filepath: str, data) -> bool:
    """Escritura atómica mediante fichero temporal único + os.replace.

    Usa tempfile.mkstemp para generar un nombre de temporal único en el mismo
    directorio que el destino, evitando la condición de carrera cuando
    media_processor y server escriben concurrentemente sobre el mismo fichero.
    Devuelve True si el guardado fue exitoso, False en caso de error.
    """
    dir_name = os.path.dirname(os.path.abspath(filepath)) or '.'
    fd, tmp_path = tempfile.mkstemp(dir=dir_name, suffix='.tmp.json')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        os.replace(tmp_path, filepath)   # atómico en Linux/Synology
        return True
    except Exception as e:
        log_error("database", str(e))
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        return False


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
        dir_name = os.path.dirname(os.path.abspath(ERROR_LOG_FILE)) or '.'
        fd, tmp_path = tempfile.mkstemp(dir=dir_name, suffix='.tmp.json')
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            json.dump(errors, f, indent=2, ensure_ascii=False)
        os.replace(tmp_path, ERROR_LOG_FILE)
    except Exception:
        pass
