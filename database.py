import os
import json
from datetime import datetime

# Apuntamos al fichero real con los 13MB de datos
DB_FILE = "videos_db.json"
STATE_FILE = "scanner_state.json"
FOLDER_STATS_FILE = "folder_stats.json"
ERROR_LOG_FILE = "errors.json"

def load_json(filepath, default):
    if not os.path.exists(filepath):
        return default
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default

def save_json(filepath, data):
    try:
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=4, ensure_ascii=False)
    except Exception as e:
        log_error("database", str(e))

def log_error(context, message):
    errors = load_json(ERROR_LOG_FILE, [])
    errors.append({
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "context": context,
        "message": message
    })
    if len(errors) > 200:
        errors = errors[-200:]
    try:
        with open(ERROR_LOG_FILE, "w", encoding="utf-8") as f:
            json.dump(errors, f, indent=4, ensure_ascii=False)
    except:
        pass
