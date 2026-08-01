#!/bin/bash

DB_FILE="videos_db.json"
EXCLUDED_PATH="/media/volumeUSB3/usbshare3-2"

if [ ! -f "$DB_FILE" ]; then
    echo "❌ Error: No se encuentra el fichero $DB_FILE"
    exit 1
fi

echo "📦 Creando copia de seguridad de seguridad en ${DB_FILE}.bak..."
cp "$DB_FILE" "${DB_FILE}.bak"

echo "🧹 Filtrando registros de la ruta: $EXCLUDED_PATH ..."

python3 -c '
import json

db_file = "videos_db.json"
excluded = "/media/volumeUSB3/usbshare3-2"

with open(db_file, "r", encoding="utf-8") as f:
    videos = json.load(f)

original_count = len(videos)
filtered_videos = [v for v in videos if not v.get("path", "").startswith(excluded)]
removed_count = original_count - len(filtered_videos)

with open(db_file, "w", encoding="utf-8") as f:
    json.dump(filtered_videos, f, indent=4, ensure_ascii=False)

print(f"✅ ¡Limpieza completada!")
print(f"   - Registros iniciales: {original_count}")
print(f"   - Registros eliminados: {removed_count}")
print(f"   - Registros restantes: {len(filtered_videos)}")
'
