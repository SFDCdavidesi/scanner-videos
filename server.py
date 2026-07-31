import os
import sys
import shutil
import subprocess
import json
from typing import Optional
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import HTMLResponse, FileResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

from database import load_json, save_json, log_error, DB_FILE, STATE_FILE, FOLDER_STATS_FILE, ERROR_LOG_FILE

ACTIVE_SCANNER_PROCESS = None
ACTIVE_GEOLOCATOR_PROCESS = None
templates = Jinja2Templates(directory="templates")

class RenameRequest(BaseModel):
    id: int
    new_name: str

class DeleteRequest(BaseModel):
    id: int

def launch_geolocator_low_priority():
    """Lanza geolocator.py como un proceso independiente con prioridad mínima en Linux (nice -n 19)."""
    global ACTIVE_GEOLOCATOR_PROCESS
    if ACTIVE_GEOLOCATOR_PROCESS and ACTIVE_GEOLOCATOR_PROCESS.poll() is None:
        return
    try:
        # 'nice -n 19' asigna la menor prioridad de CPU en el planificador del kernel Linux
        cmd = ["nice", "-n", "19", sys.executable, "geolocator.py"]
        ACTIVE_GEOLOCATOR_PROCESS = subprocess.Popen(cmd, shell=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception as e:
        log_error("Lanzar Geolocalizador", f"No se pudo iniciar geolocator.py: {e}")

def launch_scanner_process(resume: bool = False, clean_first: bool = False):
    global ACTIVE_SCANNER_PROCESS
    if ACTIVE_SCANNER_PROCESS and ACTIVE_SCANNER_PROCESS.poll() is None:
        return
    cmd = [sys.executable, "scanner.py"]
    if resume: cmd.append("--resume")
    if clean_first: cmd.append("--clean-deleted")
    try:
        ACTIVE_SCANNER_PROCESS = subprocess.Popen(cmd, shell=False)
    except Exception as e:
        log_error("Lanzar Escáner", f"No se pudo iniciar scanner.py: {e}")

@asynccontextmanager
async def lifespan(app: FastAPI):
    if not os.path.exists(DB_FILE):
        launch_scanner_process()
    
    # Arrancar el proceso independiente de geolocalización a prioridad mínima
    launch_geolocator_low_priority()
    
    yield
    global ACTIVE_SCANNER_PROCESS, ACTIVE_GEOLOCATOR_PROCESS
    if ACTIVE_SCANNER_PROCESS and ACTIVE_SCANNER_PROCESS.poll() is None:
        try: ACTIVE_SCANNER_PROCESS.terminate()
        except Exception: pass
    if ACTIVE_GEOLOCATOR_PROCESS and ACTIVE_GEOLOCATOR_PROCESS.poll() is None:
        try: ACTIVE_GEOLOCATOR_PROCESS.terminate()
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
    return {"success": True}

@app.post("/api/scan")
def trigger_scan(resume: bool = False, clean_first: bool = True):
    launch_scanner_process(resume=resume, clean_first=clean_first)
    # Asegurarnos de que el geolocalizador esté corriendo
    launch_geolocator_low_priority()
    return {"message": "Escaneo iniciado."}

@app.post("/api/videos/rename")
def rename_video(req: RenameRequest):
    videos = load_json(DB_FILE, [])
    updated = False
    for v in videos:
        if v.get("id") == req.id:
            v["name"] = req.new_name
            updated = True
            break
    if updated:
        save_json(DB_FILE, videos)
        return {"success": True}
    raise HTTPException(status_code=404, detail="Vídeo no encontrado.")

@app.post("/api/videos/delete")
def delete_video(req: DeleteRequest):
    videos = load_json(DB_FILE, [])
    new_videos = [v for v in videos if v.get("id") != req.id]
    if len(new_videos) < len(videos):
        save_json(DB_FILE, new_videos)
        return {"success": True}
    raise HTTPException(status_code=404, detail="Vídeo no encontrado.")


# --- FUNCIONES DE TRANSCODIFICACIÓN AL VUELO ---

def needs_transcoding(filepath: str) -> bool:
    cmd = ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_streams", filepath]
    try:
        result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=5)
        data = json.loads(result.stdout)
        
        web_safe_video = {"h264", "hevc", "vp8", "vp9", "av1"}
        web_safe_audio = {"aac", "mp3", "opus", "vorbis"}
        
        for stream in data.get("streams", []):
            codec = stream.get("codec_name", "").lower()
            codec_type = stream.get("codec_type")
            if codec_type == "video" and codec not in web_safe_video:
                return True
            if codec_type == "audio" and codec not in web_safe_audio:
                return True
        return False
    except Exception:
        return True

def ffmpeg_stream_generator(filepath: str):
    cmd = [
        "ffmpeg", "-i", filepath,
        "-c:v", "libx264", "-preset", "ultrafast", "-tune", "zerolatency",
        "-c:a", "aac", "-b:a", "128k",
        "-f", "mp4", "-movflags", "frag_keyframe+empty_moov",
        "pipe:1"
    ]
    process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, bufsize=10**7)
    try:
        while True:
            chunk = process.stdout.read(65536)
            if not chunk:
                break
            yield chunk
    finally:
        try:
            process.kill()
        except Exception:
            pass

@app.get("/api/stream")
def stream_video(path: str):
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="El vídeo no existe en el disco.")
    
    if needs_transcoding(path):
        return StreamingResponse(ffmpeg_stream_generator(path), media_type="video/mp4")
    else:
        return FileResponse(path)


@app.get("/", response_class=HTMLResponse)
def get_dashboard(request: Request):
    return templates.TemplateResponse(request, "index.html", {})

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
