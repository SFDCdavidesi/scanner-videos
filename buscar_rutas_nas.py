import os

# Extensiones de vídeo que vamos a detectar
EXTENSIONES_VIDEO = (".mp4", ".mov", ".m4v", ".avi", ".mkv", ".3gp", ".wmv")
# Volúmenes habituales en Synology NAS
VOLUMENES = ["/volume1", "/volume2", "/volume3", "/volume4"]


def buscar_todas_las_rutas():
    print("=== BUSCANDO CARPETAS CON VÍDEOS EN TODO EL NAS ===\n")
    rutas_encontradas = set()

    for volumen in VOLUMENES:
        if not os.path.exists(volumen):
            continue

        print(
            f"🔍 Escaneando {volumen} (puede tardar unos segundos según el disco)..."
        )

        for root, dirs, files in os.walk(volumen):
            partes_ruta = root.split(os.sep)

            # 1. Omitir carpetas ocultas o del sistema de Synology (@eaDir, #recycle, etc.)
            if any(p.startswith("@") or p == "#recycle" for p in partes_ruta):
                continue

            # 2. Omitir el alias '/home' para no duplicar lo que está en '/homes'
            if "/home/" in root or root.endswith("/home"):
                continue

            # 3. Si hay al menos un archivo de vídeo, guardamos la carpeta y pasamos a la siguiente
            for fichero in files:
                if fichero.lower().endswith(EXTENSIONES_VIDEO):
                    rutas_encontradas.add(root)
                    break

    print("\n=== RUTAS REALES DEL NAS CON VÍDEOS ENCONTRADAS ===")
    if not rutas_encontradas:
        print("No se han encontrado vídeos en los volúmenes analizados.")
    else:
        for ruta in sorted(rutas_encontradas):
            print(f" └── {ruta}")
    print("===================================================\n")
    print(
        "Copia estas rutas para usarlas en tu configuración o en tu escáner."
    )


if __name__ == "__main__":
    buscar_todas_las_rutas()