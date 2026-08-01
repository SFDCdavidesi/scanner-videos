import os
import sys
import subprocess
import json
import time
import secrets
from datetime import datetime
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, HTTPException, Depends, status
from fastapi.responses import HTMLResponse, FileResponse, StreamingResponse, Response
from fastapi.templating import Jinja2Templates
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from starlette.middleware.base import BaseHTTPMiddleware
from pydantic import BaseModel

from database import load_json, save_json, log_error, DB_FILE, STATE_FILE, FOLDER_STATS_FILE, ERROR_LOG_FILE

AUTH_FILE = "auth.json"
CATEGORIES_FILE = "video_categories.json"
LOGS_DIR = "logs"
os.makedirs(LOGS_DIR, exist_ok=True)

EXCLUDED_FOLDERS = ["/media/volumeUSB3/usbshare3-2"]

def load_auth():
    default_auth = {"admin": "cambiame", "familia": "david2026"}
    if os.path.exists(AUTH_FILE):
        try:
            with open(AUTH_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, dict) and len(data) > 0:
                    return data
        except Exception:
            pass
    try:
        with open(AUTH_FILE, "w", encoding="utf-8") as f:
            json.dump(default_auth, f, indent=4)
    except:
        pass
    return default_auth

def load_categories():
    if not os.path.exists(CATEGORIES_FILE):
        return {}
    try:
        with open(CATEGORIES_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}

security = HTTPBasic()

def verificar_credenciales(request: Request, credentials: HTTPBasicCredentials = Depends(security)):
    if request.url.path.startswith("/share/"):
        return "guest"
    
    users = load_auth()
    username = credentials.username
    password = credentials.password
    
    if username in users and secrets.compare_digest(str(users[username]), str(password)):
        return username
        
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Acceso Denegado",
        headers={"WWW-Authenticate": "Basic"},
    )

def verify_admin(username: str = Depends(verificar_credenciales)):
    if username != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Acceso exclusivo para administradores."
        )
    return username

class RegistroAccesosMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        ip_cliente = request.headers.get("x-real-ip") or request.headers.get("x-forwarded-for") or request.client.host
        metodo = request.method
        url = request.url.path
        
        response = await call_next(request)
        
        fecha_hoy = datetime.now().strftime("%Y-%m-%d")
        hora_exacta = datetime.now().strftime("%H:%M:%S")
        archivo_log = os.path.join(LOGS_DIR, f"accesos_{fecha_hoy}.log")
        
        linea_log = f"[{hora_exacta}] IP: {ip_cliente} | {metodo} {url} | Código: {response.status_code}\n"
        try:
            with open(archivo_log, "a", encoding="utf-8") as f:
                f.write(linea_log)
        except Exception:
            pass
        return response

ACTIVE_SCANNER_PROCESS = None
ACTIVE_GEOLOCATOR_PROCESS = None
templates = Jinja2Templates(directory="templates")

class RenameRequest(BaseModel):
    id: int
    new_name: str

class DeleteRequest(BaseModel):
    id: int

class CategoryRequest(BaseModel):
    id: int
    category: str

class ScanRequest(BaseModel):
    resume: bool = False
    clean_first: bool = True
    path: str | None = None

def launch_geolocator_low_priority():
    global ACTIVE_GEOLOCATOR_PROCESS
    if ACTIVE_GEOLOCATOR_PROCESS and ACTIVE_GEOLOCATOR_PROCESS.poll() is None:
        return
    try:
        cmd = ["nice", "-n", "19", sys.executable, "geolocator.py"]
        ACTIVE_GEOLOCATOR_PROCESS = subprocess.Popen(
            cmd, shell=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
    except Exception:
        pass

def launch_scanner_process(resume: bool = False, clean_first: bool = False, target_path: str | None = None):
    global ACTIVE_SCANNER_PROCESS
    if ACTIVE_SCANNER_PROCESS and ACTIVE_SCANNER_PROCESS.poll() is None:
        return
    cmd = [sys.executable, "scanner.py"]
    if resume:
        cmd.append("--resume")
    if clean_first:
        cmd.append("--clean-deleted")
    if target_path:
        cmd.extend(["--path", target_path])
    try:
        ACTIVE_SCANNER_PROCESS = subprocess.Popen(cmd, shell=False)
    except Exception:
        pass

@asynccontextmanager
async def lifespan(app: FastAPI):
    if not os.path.exists(DB_FILE):
        launch_scanner_process()
    launch_geolocator_low_priority()
    yield
    global ACTIVE_SCANNER_PROCESS, ACTIVE_GEOLOCATOR_PROCESS
    if ACTIVE_SCANNER_PROCESS and ACTIVE_SCANNER_PROCESS.poll() is None:
        try: ACTIVE_SCANNER_PROCESS.terminate()
        except: pass
    if ACTIVE_GEOLOCATOR_PROCESS and ACTIVE_GEOLOCATOR_PROCESS.poll() is None:
        try: ACTIVE_GEOLOCATOR_PROCESS.terminate()
        except: pass

app = FastAPI(title="Explorador Web NAS", lifespan=lifespan, dependencies=[Depends(verificar_credenciales)])
app.add_middleware(RegistroAccesosMiddleware)

@app.get("/favicon.ico", include_in_schema=False)
def favicon():
    svg_icon = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100"><text y=".9em" font-size="90">🎬</text></svg>'
    return Response(content=svg_icon, media_type="image/svg+xml")

@app.get("/api/user_info")
def get_user_info(username: str = Depends(verificar_credenciales)):
    return {"username": username, "is_admin": (username == "admin")}

@app.get("/api/videos")
def get_videos():
    videos = load_json(DB_FILE, [])
    categories = load_categories()
    valid_videos = []
    
    for idx, v in enumerate(videos):
        if v.get("is_duplicate", False):
            continue
            
        v_path = v.get("path", "")
        excluded = any(v_path.startswith(exc) for exc in EXCLUDED_FOLDERS)
        if excluded:
            continue

        v_copy = dict(v)
        vid_id = str(v_copy.get("id", idx))
        v_copy["category"] = categories.get(vid_id, "")
        valid_videos.append(v_copy)
        
    return valid_videos

@app.get("/api/categories")
def get_categories():
    categories = load_categories()
    unique_cats = sorted(list(set(c for c in categories.values() if c)))
    return unique_cats

@app.post("/api/videos/category", dependencies=[Depends(verify_admin)])
def set_video_category(req: CategoryRequest):
    categories = load_categories()
    if req.category.strip():
        categories[str(req.id)] = req.category.strip()
    else:
        if str(req.id) in categories:
            del categories[str(req.id)]
    save_json(CATEGORIES_FILE, categories)
    return {"success": True}

@app.get("/api/dashboard_stats")
def get_dashboard_stats():
    videos = load_json(DB_FILE, [])
    valid_videos = []
    for v in videos:
        if v.get("is_duplicate", False):
            continue
        v_path = v.get("path", "")
        if not any(v_path.startswith(exc) for exc in EXCLUDED_FOLDERS):
            valid_videos.append(v)
            
    total_vids = len(valid_videos)
    if total_vids == 0:
        return {"total_videos": 0, "total_size_gb": 0, "avg_size_mb": 0, "total_duration_hrs": 0, "avg_duration_sec": 0}
        
    total_size = 0.0
    total_duration = 0.0
    
    for v in valid_videos:
        try:
            s = v.get("size_mb")
            if s is not None: 
                total_size += float(s)
        except: pass
        
        try:
            d = v.get("duration")
            if d is not None: 
                total_duration += float(d)
        except: pass
    
    return {
        "total_videos": total_vids,
        "total_size_gb": round(total_size / 1024, 2),
        "avg_size_mb": round(total_size / total_vids, 2) if total_vids else 0,
        "total_duration_hrs": round(total_duration / 3600, 2),
        "avg_duration_sec": round(total_duration / total_vids, 2) if total_vids else 0
    }

@app.get("/api/status")
def get_status():
    state = load_json(STATE_FILE, {})
    videos = load_json(DB_FILE, [])
    state["total_count"] = len(videos)
    global ACTIVE_SCANNER_PROCESS
    if state.get("is_completed") is False:
        if ACTIVE_SCANNER_PROCESS is None or ACTIVE_SCANNER_PROCESS.poll() is not None:
            state["is_completed"] = "ZOMBIE"
    return state

@app.get("/api/folders")
def get_folders():
    stats = load_json(FOLDER_STATS_FILE, {})
    return list(stats.values())

@app.get("/api/errors")
def get_errors():
    return load_json(ERROR_LOG_FILE, [])

@app.post("/api/errors/clear", dependencies=[Depends(verify_admin)])
def clear_errors():
    save_json(ERROR_LOG_FILE, [])
    return {"success": True}

@app.post("/api/scan", dependencies=[Depends(verify_admin)])
def trigger_scan(req: ScanRequest = ScanRequest()):
    launch_scanner_process(resume=req.resume, clean_first=req.clean_first, target_path=req.path)
    launch_geolocator_low_priority()
    return {"message": "Escaneo iniciado."}

@app.post("/api/scan/stop", dependencies=[Depends(verify_admin)])
def stop_scan():
    global ACTIVE_SCANNER_PROCESS
    if ACTIVE_SCANNER_PROCESS and ACTIVE_SCANNER_PROCESS.poll() is None:
        try:
            ACTIVE_SCANNER_PROCESS.terminate()
            ACTIVE_SCANNER_PROCESS = None
        except:
            pass
    state = load_json(STATE_FILE, {})
    state["is_completed"] = True
    state["last_run"] = "Detenido por el usuario"
    save_json(STATE_FILE, state)
    return {"success": True, "message": "Escaneo cancelado."}

@app.post("/api/videos/rename", dependencies=[Depends(verify_admin)])
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

@app.post("/api/videos/delete", dependencies=[Depends(verify_admin)])
def delete_video(req: DeleteRequest):
    videos = load_json(DB_FILE, [])
    new_videos = [v for v in videos if v.get("id") != req.id]
    if len(new_videos) < len(videos):
        save_json(DB_FILE, new_videos)
        return {"success": True}
    raise HTTPException(status_code=404, detail="Vídeo no encontrado.")

def needs_transcoding(filepath: str) -> bool:
    ext = os.path.splitext(filepath)[1].lower()
    if ext not in {".mp4", ".webm"}:
        return True
    cmd = ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_streams", filepath]
    try:
        result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=5)
        data = json.loads(result.stdout)
        web_safe_video = {"h264", "hevc", "vp8", "vp9", "av1"}
        web_safe_audio = {"aac", "mp3", "opus", "vorbis"}
        for stream in data.get("streams", []):
            codec = stream.get("codec_name", "").lower()
            codec_type = stream.get("codec_type")
            if codec_type == "video" and codec not in web_safe_video: return True
            if codec_type == "audio" and codec not in web_safe_audio: return True
        return False
    except Exception:
        return True

def ffmpeg_stream_generator(filepath: str):
    cmd = [
        "ffmpeg", "-i", filepath,
        "-c:v", "libx264", "-preset", "ultrafast", "-tune", "zerolatency",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "128k",
        "-f", "mp4", "-movflags", "frag_keyframe+empty_moov",
        "pipe:1"
    ]
    try:
        process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=10**7)
    except Exception as e:
        yield b""
        return
    try:
        while True:
            chunk = process.stdout.read(65536)
            if not chunk: break
            yield chunk
    finally:
        try: process.kill()
        except: pass

@app.get("/api/stream")
def stream_video(video_id: int):
    videos = load_json(DB_FILE, [])
    path = None
    for v in videos:
        if v.get("id") == video_id:
            path = v.get("path")
            break
    if not path or not os.path.exists(path):
        raise HTTPException(status_code=404, detail="El vídeo no existe en el disco.")
    if needs_transcoding(path):
        return StreamingResponse(ffmpeg_stream_generator(path), media_type="video/mp4")
    else:
        return FileResponse(path)

@app.get("/share/{video_id}/video.mp4")
def share_video_whatsapp(video_id: int):
    videos = load_json(DB_FILE, [])
    path = None
    for v in videos:
        if v.get("id") == video_id:
            path = v.get("path")
            break
    if not path or not os.path.exists(path):
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
