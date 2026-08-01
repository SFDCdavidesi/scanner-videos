import json
import hashlib
import os
from collections import defaultdict

DB_FILE = 'videos_db.json'
REPORT_FILE = 'duplicates_report.json'

def get_fast_hash(filepath, chunk_size=1024*1024):
    """Calcula un hash MD5 leyendo solo el primer y último MB para ser ultrarrápido."""
    hasher = hashlib.md5()
    try:
        file_size = os.path.getsize(filepath)
        with open(filepath, 'rb') as f:
            hasher.update(f.read(chunk_size))
            if file_size > chunk_size * 2:
                f.seek(-chunk_size, os.SEEK_END)
                hasher.update(f.read(chunk_size))
        return hasher.hexdigest()
    except Exception:
        return None

def find_and_mark_duplicates():
    print(f"📥 Cargando {DB_FILE}...")
    try:
        with open(DB_FILE, 'r', encoding='utf-8') as f:
            videos = json.load(f)
    except Exception as e:
        print(f"❌ Error al leer el archivo JSON: {e}")
        return

    # 1. Agrupar candidatos por Nombre + Tamaño + Duración (100% en memoria, sin tocar disco)
    candidates = defaultdict(list)
    for v in videos:
        # Reseteamos la marca por si acaso
        v['is_duplicate'] = False
        
        # LA CLAVE ESTRICTA: Solo agrupamos si coinciden estas tres cosas exactamente
        key = f"{v.get('name')}_{v.get('size_mb')}_{v.get('duration')}"
        candidates[key].append(v)

    duplicates_report = {}
    total_duplicates = 0
    espacio_ahorrable_mb = 0.0

    print("🔍 Analizando hashes SOLO de los sospechosos de estar repetidos...")

    # 2. Solo calculamos el hash si el grupo tiene MÁS DE 1 ARCHIVO
    for key, items in candidates.items():
        if len(items) > 1:
            hashes = defaultdict(list)
            for item in items:
                path = item.get('path')
                if path and os.path.exists(path):
                    # Aquí es el único momento donde leemos el disco
                    file_hash = get_fast_hash(path)
                    if file_hash:
                        hashes[file_hash].append(item)
                else:
                    print(f"⚠️ Archivo no accesible, saltando: {path}")

            # 3. Identificar los verdaderos idénticos según el hash
            for file_hash, identical_items in hashes.items():
                if len(identical_items) > 1:
                    # El primero de la lista se queda como original, el resto se marcan para ocultar
                    original = identical_items[0]
                    dupes = identical_items[1:]
                    
                    duplicates_report[original['path']] = [d['path'] for d in dupes]
                    
                    for d in dupes:
                        d['is_duplicate'] = True
                        total_duplicates += 1
                        espacio_ahorrable_mb += float(d.get('size_mb', 0))

    if total_duplicates > 0:
        print(f"⚠️ ¡Se han encontrado {total_duplicates} vídeos 100% idénticos repetidos!")
        print(f"💾 Espacio inútil ocupado: {round(espacio_ahorrable_mb / 1024, 2)} GB")
        
        with open(REPORT_FILE, 'w', encoding='utf-8') as f:
            json.dump(duplicates_report, f, indent=4)
        print(f"📝 Reporte detallado guardado en {REPORT_FILE}")

        with open(DB_FILE, 'w', encoding='utf-8') as f:
            json.dump(videos, f, indent=4)
        print(f"✅ Archivo {DB_FILE} actualizado. Ya no aparecerán en la web.")
    else:
        print("✅ No se encontraron archivos idénticos.")

if __name__ == '__main__':
    find_and_mark_duplicates()
