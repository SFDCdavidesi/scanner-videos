import os
import sys
import shutil
import subprocess
from typing import Optional
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, FileResponse, StreamingResponse
from pydantic import BaseModel

from database import load_json, save_json, log_error, DB_FILE, STATE_FILE, FOLDER_STATS_FILE, ERROR_LOG_FILE

ACTIVE_SCANNER_PROCESS = None

class RenameRequest(BaseModel):
    file_path: str
    new_name: str

class DeleteRequest(BaseModel):
    file_path: str

def launch_scanner_process(resume: bool = False, clean_first: bool = False):
    global ACTIVE_SCANNER_PROCESS
    if ACTIVE_SCANNER_PROCESS and ACTIVE_SCANNER_PROCESS.poll() is None:
        return
    
    # Usamos sys.executable para asegurar que usa el intérprete correcto del contenedor
    cmd = [sys.executable, "scanner.py"]
    if resume: cmd.append("--resume")
    if clean_first: cmd.append("--clean-deleted")
    
    try:
        ACTIVE_SCANNER_PROCESS = subprocess.Popen(cmd, shell=False)
        print(f"[SERVIDOR] Proceso escáner lanzado correctamente con PID: {ACTIVE_SCANNER_PROCESS.pid}", flush=True)
    except Exception as e:
        log_error("Lanzar Escáner", f"No se pudo iniciar el proceso subprocess: {e}")
        print(f"[ERROR] No se pudo iniciar el escáner: {e}", flush=True)

@asynccontextmanager
async def lifespan(app: FastAPI):
    if not os.path.exists(DB_FILE):
        launch_scanner_process()
    yield
    global ACTIVE_SCANNER_PROCESS
    if ACTIVE_SCANNER_PROCESS and ACTIVE_SCANNER_PROCESS.poll() is None:
        try: ACTIVE_SCANNER_PROCESS.terminate()
        except Exception: pass

app = FastAPI(title="Explorador Web Desacoplado NAS", lifespan=lifespan)

@app.get("/api/videos")
def get_videos():
    return load_json(DB_FILE, [])

@app.get("/api/status")
def get_status():
    state = load_json(STATE_FILE, {})
    videos = load_json(DB_FILE, [])
    state["total_count"] = len(videos)
    return state

@app.get("/api/folders")
def get_folders():
    stats = load_json(FOLDER_STATS_FILE, {})
    return list(stats.values())

@app.get("/api/errors")
def get_errors():
    return load_json(ERROR_LOG_FILE, [])

@app.post("/api/errors/clear")
def clear_errors():
    save_json(ERROR_LOG_FILE, [])
    return {"success": True, "message": "Log de errores vaciado correctamente."}

@app.post("/api/scan")
def trigger_scan(resume: bool = False, clean_first: bool = True):
    launch_scanner_process(resume=resume, clean_first=clean_first)
    return {"message": "Escaneo iniciado."}

@app.get("/api/stream")
def stream_video(path: str):
    if not os.path.exists(path):
        log_error("Streaming", f"Petición de reproducción fallida: archivo no encontrado en {path}")
        raise HTTPException(status_code=404, detail="El vídeo no existe.")
    return FileResponse(path)

@app.post("/api/rename")
def rename_video(data: RenameRequest):
    if not os.path.exists(data.file_path):
        raise HTTPException(status_code=404, detail="Archivo no encontrado.")
    parent_dir = os.path.dirname(data.file_path)
    old_ext = os.path.splitext(data.file_path)[1]
    new_name_clean = data.new_name.strip()
    if new_name_clean.lower().endswith(old_ext.lower()):
        new_name_clean = new_name_clean[:-len(old_ext)]
    new_filename = new_name_clean + old_ext
    new_path = os.path.join(parent_dir, new_filename)
    
    try:
        os.rename(data.file_path, new_path)
    except Exception as e:
        log_error("Renombrar Fichero", f"No se pudo renombrar {data.file_path} a {new_path}: {e}")
        raise HTTPException(status_code=500, detail=str(e))

    videos = load_json(DB_FILE, [])
    for v in videos:
        if v["path"] == data.file_path:
            v["name"] = new_filename
            v["path"] = new_path
            break
    save_json(DB_FILE, videos)
    return {"success": True, "new_path": new_path, "new_name": new_filename}

@app.get("/", response_class=HTMLResponse)
def get_dashboard():
    return HTMLResponse(content="""
    <!DOCTYPE html>
    <html lang="es">
    <head>
        <meta charset="UTF-8">
        <title>Explorador de Vídeos, Carpetas y Errores NAS</title>
        <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
        <link href="https://cdn.datatables.net/1.13.6/css/dataTables.bootstrap5.min.css" rel="stylesheet">
        <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/bootstrap-icons@1.11.0/font/bootstrap-icons.css">
        <style>
            body { background-color: #f4f6f9; padding: 20px; }
            .card-panel { background: white; padding: 20px; border-radius: 8px; box-shadow: 0 2px 5px rgba(0,0,0,0.05); margin-bottom: 20px; }
            .folder-tree-item { padding: 8px 12px; border-bottom: 1px solid #eee; display: flex; justify-content: space-between; align-items: center; }
            .folder-tree-item:hover { background-color: #f8f9fa; }
            .error-item { padding: 10px 15px; border-left: 4px solid #dc3545; background-color: #fff5f5; margin-bottom: 8px; border-radius: 4px; }
        </style>
    </head>
    <body>
        <div class="container-fluid">
            <div class="card-panel d-flex justify-content-between align-items-center">
                <div>
                    <h3 class="m-0"><i class="bi bi-collection-play-fill text-primary"></i> Explorador Web de Vídeos y Diagnóstico</h3>
                    <small id="statusText" class="text-muted">Estado del escáner: Comprobando...</small>
                </div>
                <div class="d-flex gap-2">
                    <button class="btn btn-outline-secondary btn-sm" onclick="triggerScan(true)"><i class="bi bi-play-circle"></i> Retomar</button>
                    <button class="btn btn-outline-primary btn-sm" onclick="triggerScan(false)"><i class="bi bi-arrow-clockwise"></i> Reiniciar Escaneo</button>
                </div>
            </div>

            <ul class="nav nav-tabs mb-3" id="mainTab" role="tablist">
                <li class="nav-item"><button class="nav-link active" data-bs-toggle="tab" data-bs-target="#videos-pane">🎬 Vídeos Encontrados</button></li>
                <li class="nav-item"><button class="nav-link" data-bs-toggle="tab" data-bs-target="#folders-pane" onclick="loadFoldersLog()">📁 Log de Carpetas</button></li>
                <li class="nav-item"><button class="nav-link text-danger fw-bold" data-bs-toggle="tab" data-bs-target="#errors-pane" onclick="loadErrorsLog()"><i class="bi bi-exclamation-triangle-fill"></i> Log de Errores (<span id="errorBadgeCount">0</span>)</button></li>
            </ul>

            <div class="tab-content">
                <div class="tab-pane fade show active card-panel" id="videos-pane">
                    <table id="videosTable" class="table table-striped table-hover w-100">
                        <thead>
                            <tr><th>Nombre</th><th>Duración (s)</th><th>Tamaño (MB)</th><th>Fecha Captura</th><th>Ruta Completa</th><th>Acciones</th></tr>
                        </thead>
                        <tbody></tbody>
                    </table>
                </div>

                <div class="tab-pane fade card-panel" id="folders-pane">
                    <h5 class="mb-3"><i class="bi bi-folder2-open text-warning"></i> Directorios analizados por el escáner</h5>
                    <div class="input-group mb-3">
                        <span class="input-group-text"><i class="bi bi-search"></i></span>
                        <input type="text" id="folderSearchInput" class="form-control" placeholder="Filtrar por ruta de carpeta..." onkeyup="filterFoldersList()">
                    </div>
                    <div id="foldersListContainer" style="max-height: 500px; overflow-y: auto; border: 1px solid #dee2e6; border-radius: 6px;">
                        <div class="p-3 text-center text-muted">Cargando log de carpetas...</div>
                    </div>
                </div>

                <div class="tab-pane fade card-panel" id="errors-pane">
                    <div class="d-flex justify-content-between align-items-center mb-3">
                        <h5 class="m-0 text-danger"><i class="bi bi-bug-fill"></i> Registro de Errores y Excepciones</h5>
                        <button class="btn btn-outline-danger btn-sm" onclick="clearErrorsLog()"><i class="bi bi-trash"></i> Vaciar Log de Errores</button>
                    </div>
                    <div id="errorsListContainer" style="max-height: 500px; overflow-y: auto; border: 1px solid #f5c6cb; border-radius: 6px; padding: 10px; background-color: #fff;">
                        <div class="p-3 text-center text-muted">No hay errores registrados. ¡Todo marcha bien!</div>
                    </div>
                </div>
            </div>
        </div>

        <script src="https://code.jquery.com/jquery-3.7.0.min.js"></script>
        <script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/js/bootstrap.bundle.min.js"></script>
        <script src="https://cdn.datatables.net/1.13.6/js/jquery.dataTables.min.js"></script>
        <script src="https://cdn.datatables.net/1.13.6/js/dataTables.bootstrap5.min.js"></script>
        <script>
            let table;
            let allFoldersData = [];

            $(document).ready(function() {
                table = $('#videosTable').DataTable({
                    ajax: { url: '/api/videos', dataSrc: '' },
                    language: { url: '//cdn.datatables.net/plug-ins/1.13.6/i18n/es-ES.json' },
                    columns: [
                        { data: 'name', className: 'fw-bold' },
                        { data: 'duration' },
                        { data: 'size_mb' },
                        { data: 'capture_date' },
                        { data: 'path', className: 'small text-muted' },
                        {
                            data: null,
                            render: function(data, type, row) {
                                return `<button class="btn btn-sm btn-primary" onclick="alert('${row.path}')"><i class="bi bi-play-fill"></i></button>`;
                            }
                        }
                    ]
                });
                setInterval(checkStatus, 3000);
                loadErrorsLog();
                loadFoldersLog();
            });

            function checkStatus() {
                fetch('/api/status').then(r => r.json()).then(data => {
                    const statusEl = document.getElementById('statusText');
                    if (statusEl) {
                        if (data.is_completed === false) {
                            statusEl.innerHTML = `<span class="badge bg-warning text-dark">Escaneando... Vídeos: ${data.total_count} | Carpeta actual: ${data.last_folder || 'Iniciando'}</span>`;
                            table.ajax.reload(null, false);
                        } else {
                            statusEl.innerHTML = `<span class="badge bg-success">Completado | Vídeos totales: ${data.total_count}</span>`;
                        }
                    }
                });
                loadErrorsLogCount();
            }

            function loadErrorsLogCount() {
                fetch('/api/errors').then(r => r.json()).then(data => {
                    document.getElementById('errorBadgeCount').innerText = data.length || 0;
                });
            }

            function loadErrorsLog() {
                fetch('/api/errors').then(r => r.json()).then(data => {
                    const container = document.getElementById('errorsListContainer');
                    document.getElementById('errorBadgeCount').innerText = data.length || 0;
                    if (!data || data.length === 0) {
                        container.innerHTML = '<div class="p-4 text-center text-muted">No hay errores registrados. ¡Todo marcha bien!</div>';
                        return;
                    }
                    let html = '';
                    data.forEach(err => {
                        html += `
                            <div class="error-item">
                                <div class="d-flex justify-content-between">
                                    <strong class="text-danger"><i class="bi bi-exclamation-circle-fill"></i> [${err.context}]</strong>
                                    <small class="text-muted">${err.timestamp}</small>
                                </div>
                                <div class="small mt-1 text-dark font-monospace">${err.message}</div>
                            </div>
                        `;
                    });
                    container.innerHTML = html;
                });
            }

            function clearErrorsLog() {
                if (confirm('¿Estás seguro de vaciar el registro de errores?')) {
                    fetch('/api/errors/clear', { method: 'POST' }).then(() => loadErrorsLog());
                }
            }

            function triggerScan(resume) {
                fetch(`/api/scan?resume=${resume}&clean_first=true`, { method: 'POST' }).then(() => checkStatus());
            }

            function loadFoldersLog() {
                fetch('/api/folders').then(r => r.json()).then(data => {
                    allFoldersData = data;
                    renderFoldersList(data);
                });
            }

            function renderFoldersList(folders) {
                const container = document.getElementById('foldersListContainer');
                if (!folders || folders.length === 0) {
                    container.innerHTML = '<div class="p-4 text-center text-muted">Todavía no hay carpetas registradas.</div>';
                    return;
                }
                let html = '';
                folders.sort((a, b) => b.scanned_at.localeCompare(a.scanned_at)).forEach(f => {
                    html += `
                        <div class="folder-tree-item">
                            <div>
                                <i class="bi bi-folder-fill text-primary me-2"></i><strong>${f.path}</strong><br>
                                <small class="text-muted ms-4">Escaneado el: ${f.scanned_at}</small>
                            </div>
                            <div class="text-end">
                                <span class="badge bg-secondary me-1">Ficheros: ${f.total_files}</span>
                                <span class="badge bg-success">Vídeos encontrados: ${f.videos_found}</span>
                            </div>
                        </div>
                    `;
                });
                container.innerHTML = html;
            }

            function filterFoldersList() {
                const query = document.getElementById('folderSearchInput').value.toLowerCase();
                const filtered = allFoldersData.filter(f => f.path.toLowerCase().includes(query));
                renderFoldersList(filtered);
            }
        </script>
    </body>
    </html>
    """)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
