import os
import time
import argparse
from datetime import datetime
from database import load_json, save_json, log_error, DB_FILE, STATE_FILE, FOLDER_STATS_FILE

# ─── Nombres de directorio de sistema/papelera a excluir ─────────────────────
SYSTEM_DIR_NAMES: frozenset[str] = frozenset({
    "#recycle", "@eaDir", "@appdata", "#snapshot",
    "@docker", "@tmp", "@sharebin", "#ScsiCmdCache",
    "lost+found", ".DS_Store", ".Trash",
})

# ─── Prefijos de rutas completas excluidas ────────────────────────────────────
# Añade aquí carpetas de películas pesadas u otras rutas no deseadas.
EXCLUDED_PATH_PREFIXES: tuple[str, ...] = (
    "/volume1/Peliculas",
    "/volume2/Peliculas",
    "/media/volumeUSB3/usbshare3-2",
)

# ─── Alias de rutas Synology (homes/davidesi ↔ home) ─────────────────────────
# Formato: (prefijo_alias, prefijo_canonico)
# El scanner normaliza SIEMPRE hacia el lado canónico para evitar doble índice.
PATH_ALIASES: tuple[tuple[str, str], ...] = (
    ("/volume1/homes/davidesi", "/volume1/home"),
    ("/volume2/homes/davidesi", "/volume2/home"),
)

VALID_EXTENSIONS: frozenset[str] = frozenset({
    ".mp4", ".mov", ".avi", ".mkv", ".webm", ".m4v",
})

# Umbral mínimo: ignorar ficheros menores de 100 KB
MIN_SIZE_BYTES: int = 100 * 1024

# Número de carpetas procesadas entre cada flush a disco
FLUSH_EVERY_N_FOLDERS: int = 25


def _is_excluded_dir(dirpath: str) -> bool:
    """True si el directorio debe omitirse completamente."""
    # Comprobar nombre de cada componente del path
    for part in dirpath.replace("\\", "/").split("/"):
        if part in SYSTEM_DIR_NAMES:
            return True
    # Comprobar prefijos de ruta completa
    for prefix in EXCLUDED_PATH_PREFIXES:
        if dirpath.startswith(prefix):
            return True
    return False


def _canonical_path(path: str) -> str:
    """
    Normaliza el path aplicando primero los alias del NAS y después
    os.path.normpath (no sigue symlinks para no depender del SO remoto).
    """
    for alias, canonical in PATH_ALIASES:
        if path.startswith(alias + "/") or path == alias:
            path = canonical + path[len(alias):]
            break
    return os.path.normpath(path)


def _get_inode_key(path: str) -> tuple[int, int] | None:
    """Devuelve (st_dev, st_ino) para detección de hard-links/symlinks."""
    try:
        st = os.stat(path)
        return (st.st_dev, st.st_ino)
    except OSError:
        return None


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--resume", action="store_true",
                        help="Continúa sin borrar entradas existentes")
    parser.add_argument("--clean-deleted", action="store_true",
                        help="Elimina entradas cuyo fichero ya no existe en disco")
    parser.add_argument("--path", type=str, default=None,
                        help="Escanea solo esta ruta en lugar de las raíces por defecto")
    args = parser.parse_args()

    # ── Estado inicial ────────────────────────────────────────────────────────
    state: dict = load_json(STATE_FILE, {})
    state["is_completed"] = False
    state["start_timestamp"] = time.time()
    save_json(STATE_FILE, state)

    videos: list[dict] = load_json(DB_FILE, [])
    folder_stats: dict = load_json(FOLDER_STATS_FILE, {})

    # ── Limpieza de entradas eliminadas ───────────────────────────────────────
    if args.clean_deleted:
        before = len(videos)
        videos = [v for v in videos if os.path.exists(v.get("path", ""))]
        purged = before - len(videos)
        if purged:
            log_error("scanner", f"clean-deleted: {purged} entradas eliminadas del índice.")

    # ── Índices de deduplicación ──────────────────────────────────────────────
    existing_canonical: set[str] = {
        _canonical_path(v["path"]) for v in videos
    }
    existing_inodes: set[tuple[int, int]] = set()
    for v in videos:
        key = _get_inode_key(v["path"])
        if key:
            existing_inodes.add(key)

    max_id: int = max((v.get("id", 0) for v in videos), default=0)
    scan_roots = [args.path] if args.path else ["/volume1", "/volume2", "/media"]

    files_added = 0
    folders_since_flush = 0

    # ── Recorrido de directorios ──────────────────────────────────────────────
    for root_dir in scan_roots:
        if not os.path.exists(root_dir):
            continue

        for dirpath, dirnames, filenames in os.walk(root_dir, topdown=True):
            # Podar ramas de sistema IN-SITU para que os.walk no descienda
            dirnames[:] = [
                d for d in dirnames
                if d not in SYSTEM_DIR_NAMES
                and not _is_excluded_dir(os.path.join(dirpath, d))
            ]

            if _is_excluded_dir(dirpath):
                dirnames.clear()
                continue

            state["last_folder"] = dirpath
            vids_in_folder = 0

            for fname in filenames:
                if os.path.splitext(fname)[1].lower() not in VALID_EXTENSIONS:
                    continue

                full_path = os.path.join(dirpath, fname)

                # Filtro 1: tamaño mínimo
                try:
                    size_bytes = os.path.getsize(full_path)
                except OSError:
                    continue
                if size_bytes < MIN_SIZE_BYTES:
                    continue

                # Filtro 2: ruta canónica ya indexada (alias home/homes)
                canon = _canonical_path(full_path)
                if canon in existing_canonical:
                    vids_in_folder += 1
                    continue

                # Filtro 3: mismo inode (hard-link o symlink)
                inode_key = _get_inode_key(full_path)
                if inode_key and inode_key in existing_inodes:
                    vids_in_folder += 1
                    continue

                # ── Nuevo registro ────────────────────────────────────────────
                size_mb = round(size_bytes / (1024 * 1024), 2)
                try:
                    file_date = datetime.fromtimestamp(
                        os.path.getmtime(full_path)
                    ).strftime('%Y-%m-%d %H:%M:%S')
                except OSError:
                    file_date = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

                max_id += 1
                videos.append({
                    "id": max_id,
                    "name": fname,
                    "path": full_path,
                    "canonical_path": canon,
                    "size_mb": size_mb,
                    # duration y place_name los completa geolocator.py
                    "duration": 0.0,
                    "capture_date": file_date,
                    "file_date": file_date,
                    "place_name": "",
                })
                existing_canonical.add(canon)
                if inode_key:
                    existing_inodes.add(inode_key)

                files_added += 1
                vids_in_folder += 1

            folder_stats[dirpath] = {
                "path": dirpath,
                "videos_found": vids_in_folder,
                "scanned_at": datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            }
            folders_since_flush += 1

            # Flush periódico: reduce escrituras de disco en órdenes de magnitud
            if folders_since_flush >= FLUSH_EVERY_N_FOLDERS:
                save_json(DB_FILE, videos)
                save_json(FOLDER_STATS_FILE, folder_stats)
                state["files_scanned"] = files_added
                save_json(STATE_FILE, state)
                folders_since_flush = 0

    # ── Flush final ───────────────────────────────────────────────────────────
    save_json(DB_FILE, videos)
    save_json(FOLDER_STATS_FILE, folder_stats)
    state["files_scanned"] = files_added
    state["is_completed"] = True
    state["last_run"] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    save_json(STATE_FILE, state)


if __name__ == "__main__":
    main()
