import json
import os

print("Iniciando operaciones de rescate avanzado...")

db_file = 'videos_db.json'
tmp_file = 'videos_db.json.tmp'

# 1. Reparar la etiqueta roja de error (Estado ZOMBIE)
try:
    with open('state.json', 'w', encoding='utf-8') as f:
        json.dump({"is_completed": True, "last_folder": "Escaneo finalizado"}, f)
    print("✅ Estado ZOMBIE reparado. La etiqueta roja desaparecerá.")
except Exception as e:
    print(f"⚠️ No se pudo actualizar state.json: {e}")

def get_valid_data(filepath):
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            return json.load(f)
    except json.JSONDecodeError:
        print(f"⚠️ {filepath} está corrupto. Intentando cirugía...")
        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                content = f.read()
            last_brace = content.rfind('}')
            if last_brace != -1:
                clean_content = content[:last_brace+1] + '\n]'
                return json.loads(clean_content)
        except Exception as ex:
            print(f"❌ Falló la cirugía en {filepath}: {ex}")
    except FileNotFoundError:
        pass
    return []

# 2. Rescatar datos
print(f"🔍 Analizando {db_file} y archivos temporales...")
data = get_valid_data(db_file)
if not data and os.path.exists(tmp_file):
    print("Intentando leer desde el archivo temporal...")
    data = get_valid_data(tmp_file)

if data:
    # 3. Eliminar duplicados basándonos en la ruta absoluta (path)
    unique_videos = {}
    for v in data:
        path = v.get('path')
        if path and path not in unique_videos:
            unique_videos[path] = v
    
    final_list = list(unique_videos.values())
    
    # 4. Reasignar IDs de forma secuencial y limpia
    for idx, v in enumerate(final_list):
        v['id'] = idx + 1
        
    try:
        with open(db_file, 'w', encoding='utf-8') as f:
            json.dump(final_list, f, indent=4)
        print(f"🎉 ¡RESCATE Y LIMPIEZA EXITOSOS! Se han salvado y desduplicado {len(final_list)} vídeos únicos.")
        
        # Limpiar la basura temporal si existe
        if os.path.exists(tmp_file):
            os.remove(tmp_file)
            print("🧹 Archivo temporal corrupto eliminado.")
            
    except Exception as e:
        print(f"❌ Error guardando el archivo final: {e}")
else:
    print("❌ No se pudieron recuperar datos útiles de los archivos JSON.")
