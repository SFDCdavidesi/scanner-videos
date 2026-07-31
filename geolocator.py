import time
from typing import Dict, Any, List
from database import load_json, save_json, DB_FILE, GEO_CACHE_FILE

try:
    from geopy.geocoders import Nominatim
    GEOPY_AVAILABLE = True
except ImportError:
    GEOPY_AVAILABLE = False

def run_geolocator_worker():
    print("[GEOLOCALIZADOR] Proceso de baja prioridad iniciado...", flush=True)
    if not GEOPY_AVAILABLE:
        print("[GEOLOCALIZADOR] Librería 'geopy' no disponible. Saliendo.", flush=True)
        return

    geolocator = Nominatim(user_agent="nas_video_explorer_low_priority_app")

    while True:
        try:
            videos: List[Dict[str, Any]] = load_json(DB_FILE, [])
            geo_cache: Dict[str, str] = load_json(GEO_CACHE_FILE, {})
            video_updated = False

            for v in videos:
                # Buscar vídeos que tengan GPS pero aún no se haya resuelto la ciudad
                if v.get("place_name") in ("Geolocalizando...", "Pendiente de ubicar") and v.get("lat") is not None and v.get("lon") is not None:
                    lat, lon = v["lat"], v["lon"]
                    key = f"{round(lat, 4)},{round(lon, 4)}"

                    if key in geo_cache:
                        v["place_name"] = geo_cache[key]
                        video_updated = True
                    else:
                        try:
                            # Respetar API pública de Nominatim
                            time.sleep(1.2)
                            location = geolocator.reverse((lat, lon), language="es", timeout=5)
                            if location and location.raw.get("address"):
                                address = location.raw["address"]
                                city = address.get("city") or address.get("town") or address.get("village") or address.get("municipality") or address.get("county") or ""
                                country = address.get("country") or ""
                                place_parts = [p for p in [city, country] if p]
                                place_name = ", ".join(place_parts) if place_parts else location.address
                                
                                geo_cache[key] = place_name
                                save_json(GEO_CACHE_FILE, geo_cache)
                                v["place_name"] = place_name
                                video_updated = True
                            else:
                                v["place_name"] = "Desconocida"
                                video_updated = True
                        except Exception:
                            # Si falla por red o timeout, lo dejamos para el próximo ciclo
                            pass
                    
                    # Solo procesamos uno por ciclo para no saturar nunca el disco del NAS
                    break

            if video_updated:
                # Guardamos el JSON actualizado
                save_json(DB_FILE, videos)
            else:
                # Si no hay vídeos pendientes, dormimos 10 segundos antes de volver a revisar
                time.sleep(10)

        except Exception:
            time.sleep(5)
        
        # Pausa mínima de cortesía entre comprobaciones
        time.sleep(1)

if __name__ == "__main__":
    run_geolocator_worker()
