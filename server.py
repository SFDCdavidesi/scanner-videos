import os
import sys
import subprocess
import json
import time
import secrets
import hmac
import hashlib
import base64
import ipaddress
from datetime import datetime
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, HTTPException, Depends, status, Form
from fastapi.responses import HTMLResponse, FileResponse, StreamingResponse, Response, RedirectResponse
from fastapi.templating import Jinja2Templates
from starlette.middleware.base import BaseHTTPMiddleware
from pydantic import BaseModel

from database import load_json, save_json, log_error, DB_FILE, STATE_FILE, FOLDER_STATS_FILE, ERROR_LOG_FILE

AUTH_FILE = "auth.json"
CATEGORIES_FILE = "video_categories.json"
SECRET_KEY_FILE = "secret.key"
LOGS_DIR = "logs"
os.makedirs(LOGS_DIR, exist_ok=True)

# Rutas excluidas del índice (sincronizar con EXCLUDED_PATH_PREFIXES en scanner.py)
EXCLUDED_FOLDERS: list[str] = ["/media/volumeUSB3/usbshare3-2"]

# ─── Rate limiting ────────────────────────────────────────────────────────────
LOGIN_ATTEMPTS: dict = {}
MAX_LOGIN_ATTEMPTS = 5
LOCKOUT_TIME_SECONDS = 300  # 5 minutos de bloqueo

# ─── IPs de proxies de confianza (Synology reverse proxy, localhost) ──────────
# Solo se aceptan cabeceras X-Forwarded-For / X-Real-IP cuando la conexión
# directa proviene de una de estas redes.  Ajusta si tu proxy está en otra IP.
_TRUSTED_PROXY_NETS: list[ipaddress.IPv4Network | ipaddress.IPv6Network] = [
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
]


def _is_trusted_proxy(host: str) -> bool:
    try:
        addr = ipaddress.ip_address(host)
        return any(addr in net for net in _TRUSTED_PROXY_NETS)
    except ValueError:
        return False


def get_client_ip(request: Request) -> str:
    """Devuelve la IP real del cliente.

    Solo confía en X-Forwarded-For / X-Real-IP cuando la conexión directa
    proviene de un proxy de confianza, evitando el bypass del rate-limiting.
    """
    direct_host = request.client.host if request.client else ""
    if _is_trusted_proxy(direct_host):
        # Tomamos el último IP añadido por nuestro proxy (el más a la derecha
        # que no sea el propio proxy) para evitar spoofing por el cliente.
        xff = request.headers.get("x-forwarded-for", "")
        if xff:
            candidates = [ip.strip() for ip in xff.split(",")]
            # El proxy de Synology añade al final; tomamos el primero no-privado
            for candidate in reversed(candidates):
                try:
                    addr = ipaddress.ip_address(candidate)
                    if not any(addr in net for net in _TRUSTED_PROXY_NETS):
                        return candidate
                except ValueError:
                    continue
            # Si todos son privados (red local), devolvemos el primero
            if candidates:
                return candidates[0]
        x_real = request.headers.get("x-real-ip", "").strip()
        if x_real:
            return x_real
    return direct_host or "unknown"

def is_ip_locked(ip: str) -> bool:
    if ip in LOGIN_ATTEMPTS:
        data = LOGIN_ATTEMPTS[ip]
        if data["locked"]:
            if time.time() < data["lock_until"]:
                return True
            else:
                LOGIN_ATTEMPTS[ip] = {"failures": 0, "locked": False, "lock_until": 0}
    return False

def register_login_failure(ip: str):
    now = time.time()
    if ip not in LOGIN_ATTEMPTS:
        LOGIN_ATTEMPTS[ip] = {"failures": 1, "locked": False, "lock_until": 0}
    else:
        LOGIN_ATTEMPTS[ip]["failures"] += 1
        if LOGIN_ATTEMPTS[ip]["failures"] >= MAX_LOGIN_ATTEMPTS:
            LOGIN_ATTEMPTS[ip]["locked"] = True
            LOGIN_ATTEMPTS[ip]["lock_until"] = now + LOCKOUT_TIME_SECONDS

def reset_login_failures(ip: str):
    if ip in LOGIN_ATTEMPTS:
        LOGIN_ATTEMPTS[ip] = {"failures": 0, "locked": False, "lock_until": 0}

def get_or_create_secret_key():
    if os.path.exists(SECRET_KEY_FILE):
        try:
            with open(SECRET_KEY_FILE, "r", encoding="utf-8") as f:
                key = f.read().strip()
                if key:
                    return key
        except Exception:
            pass
    new_key = secrets.token_hex(32)
    try:
        with open(SECRET_KEY_FILE, "w", encoding="utf-8") as f:
            f.write(new_key)
    except Exception:
        pass
    return new_key

SECRET_KEY = get_or_create_secret_key()

# Duración máxima de sesión en segundos (24 horas)
SESSION_MAX_AGE = 86400


def sign_cookie(username: str) -> str:
    """Genera token firmado con timestamp para sesiones con expiración."""
    ts = str(int(time.time()))
    payload = f"{username}|{ts}"
    message = payload.encode("utf-8")
    signature = hmac.new(SECRET_KEY.encode("utf-8"), message, hashlib.sha256).digest()
    sig_b64 = base64.urlsafe_b64encode(signature).decode("utf-8").rstrip("=")
    return f"{payload}.{sig_b64}"


def verify_cookie(token: str) -> str | None:
    """Verifica firma e integridad; rechaza sesiones expiradas."""
    if not token or "." not in token:
        return None
    payload, _, sig_b64 = token.rpartition(".")
    if not payload or not sig_b64:
        return None

    message = payload.encode("utf-8")
    expected_sig = hmac.new(SECRET_KEY.encode("utf-8"), message, hashlib.sha256).digest()
    expected_sig_b64 = base64.urlsafe_b64encode(expected_sig).decode("utf-8").rstrip("=")

    if not hmac.compare_digest(sig_b64, expected_sig_b64):
        return None

    # Verificar expiración del timestamp
    parts = payload.split("|")
    if len(parts) != 2:
        return None
    username, ts_str = parts
    try:
        if time.time() - int(ts_str) > SESSION_MAX_AGE:
            return None
    except ValueError:
        return None

    return username

def hash_password(password: str) -> str:
    salt = os.urandom(16)
    pwdhash = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), salt, 100000)
    return salt.hex() + ':' + pwdhash.hex()

def verify_password(stored: str, provided: str) -> bool:
    if ':' not in stored:
        return secrets.compare_digest(stored, provided)
    try:
        salt_hex, pwdhash_hex = stored.split(':')
        salt = bytes.fromhex(salt_hex)
        pwdhash = hashlib.pbkdf2_hmac('sha256', provided.encode('utf-8'), salt, 100000)
        return hmac.compare_digest(pwdhash.hex(), pwdhash_hex)
    except Exception:
        return False

# ─── Cache de autenticación ──────────────────────────────────────────────────
# Se invalida automáticamente al detectar cambio en el fichero.
_auth_cache: dict = {}
_auth_cache_mtime: float = 0.0


def load_auth() -> dict:
    global _auth_cache, _auth_cache_mtime

    default_plain = {"admin": "cambiame", "familia": "david2026"}
    default_auth = {k: hash_password(v) for k, v in default_plain.items()}

    if os.path.exists(AUTH_FILE):
        try:
            current_mtime = os.path.getmtime(AUTH_FILE)
            if _auth_cache and current_mtime == _auth_cache_mtime:
                return _auth_cache

            with open(AUTH_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict) and data:
                updated = False
                for k, v in data.items():
                    if ":" not in v:
                        data[k] = hash_password(v)
                        updated = True
                if updated:
                    with open(AUTH_FILE, "w", encoding="utf-8") as f_out:
                        json.dump(data, f_out, indent=2)
                _auth_cache = data
                _auth_cache_mtime = os.path.getmtime(AUTH_FILE)
                return data
        except Exception:
            pass

    try:
        with open(AUTH_FILE, "w", encoding="utf-8") as f:
            json.dump(default_auth, f, indent=2)
        _auth_cache = default_auth
        _auth_cache_mtime = os.path.getmtime(AUTH_FILE)
    except Exception:
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

def obtener_usuario_actual(request: Request):
    path = request.url.path
    if path in ["/", "/login", "/logout"] or path.startswith("/share/") or path == "/favicon.ico":
        return "guest"
    
    token = request.cookies.get("session_user")
    if token:
        username = verify_cookie(token)
        if username:
            users = load_auth()
            if username in users:
                return username
    return None

def verificar_credenciales(request: Request):
    user = obtener_usuario_actual(request)
    if user and user != "guest":
        return user
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Acceso Denegado"
    )

def verify_admin(request: Request):
    user = verificar_credenciales(request)
    if user != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Acceso exclusivo para administradores."
        )
    return user

class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        ip_cliente = get_client_ip(request)
        metodo = request.method
        url = request.url.path

        response = await call_next(request)

        # ── Cabeceras de seguridad ────────────────────────────────────────────
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "SAMEORIGIN"
        # X-XSS-Protection está obsoleto en navegadores modernos; CSP es la
        # protección real contra XSS.
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; "
            "script-src 'self' 'unsafe-inline'; "
            "style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data:; "
            "media-src 'self' blob:; "
            "frame-ancestors 'self';"
        )
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Permissions-Policy"] = "geolocation=(), microphone=()"

        # ── Log de acceso (buffered, no bloqueante) ───────────────────────────
        # Usamos os.open con O_APPEND|O_CREAT que es atómico en Linux/NAS.
        # No llamamos a funciones pesadas dentro del event loop; sólo un
        # write() de una línea pequeña.
        now = datetime.now()
        archivo_log = os.path.join(LOGS_DIR, f"accesos_{now.strftime('%Y-%m-%d')}.log")
        linea_log = (
            f"[{now.strftime('%H:%M:%S')}] IP: {ip_cliente} | "
            f"{metodo} {url} | Código: {response.status_code}\n"
        ).encode("utf-8")
        try:
            fd = os.open(archivo_log, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
            os.write(fd, linea_log)
            os.close(fd)
        except OSError:
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

app = FastAPI(title="Explorador Web NAS", lifespan=lifespan)
app.add_middleware(SecurityHeadersMiddleware)

@app.get("/favicon.ico", include_in_schema=False)
def favicon():
    svg_icon = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100"><text y=".9em" font-size="90">🎬</text></svg>'
    return Response(content=svg_icon, media_type="image/svg+xml")

@app.get("/", response_class=HTMLResponse)
def get_landing(request: Request):
    return templates.TemplateResponse(request, "landing.html", {})

@app.get("/login", response_class=HTMLResponse)
def get_login(request: Request):
    ip = get_client_ip(request)
    if is_ip_locked(ip):
        return templates.TemplateResponse(request, "login.html", {"locked": True})
    return templates.TemplateResponse(request, "login.html", {"locked": False})

@app.post("/login")
def login_post(request: Request, username: str = Form(...), password: str = Form(...)):
    ip = get_client_ip(request)
    
    if is_ip_locked(ip):
        return RedirectResponse(url="/login?error=locked", status_code=status.HTTP_303_SEE_OTHER)
    
    users = load_auth()
    if username in users and verify_password(users[username], password):
        reset_login_failures(ip)
        
        response = RedirectResponse(url="/app", status_code=status.HTTP_303_SEE_OTHER)
        signed_token = sign_cookie(username)
        response.set_cookie(
            key="session_user",
            value=signed_token,
            httponly=True,
            secure=True,
            samesite="lax",
            max_age=SESSION_MAX_AGE,  # Expiración alineada con la firma del token
        )
        return response
    
    register_login_failure(ip)
    return RedirectResponse(url="/login?error=1", status_code=status.HTTP_303_SEE_OTHER)

@app.get("/app", response_class=HTMLResponse)
def get_dashboard(request: Request):
    user = obtener_usuario_actual(request)
    if not user or user == "guest":
        return RedirectResponse(url="/login", status_code=status.HTTP_303_SEE_OTHER)
    return templates.TemplateResponse(request, "index.html", {})

@app.get("/logout", response_class=HTMLResponse)
def logout(request: Request):
    response = RedirectResponse(url="/", status_code=status.HTTP_303_SEE_OTHER)
    response.delete_cookie(key="session_user")
    return response

@app.get("/api/user_info")
def get_user_info(request: Request):
    username = verificar_credenciales(request)
    return {"username": username, "is_admin": (username == "admin")}

# ─── Cache de vídeos en memoria ──────────────────────────────────────────────
# Evita deserializar el JSON completo (hasta 13 MB) en cada petición.
_videos_cache: list[dict] = []
_videos_cache_mtime: float = 0.0
VIDEOS_CACHE_TTL: float = 30.0  # segundos antes de releer el fichero


def _load_videos_cached() -> list[dict]:
    """Devuelve la lista de vídeos usando caché invalidada por mtime."""
    global _videos_cache, _videos_cache_mtime
    try:
        current_mtime = os.path.getmtime(DB_FILE)
    except OSError:
        return _videos_cache

    if _videos_cache and (current_mtime == _videos_cache_mtime):
        return _videos_cache

    _videos_cache = load_json(DB_FILE, [])
    _videos_cache_mtime = current_mtime
    return _videos_cache


@app.get("/api/videos")
def get_videos(request: Request):
    verificar_credenciales(request)
    videos = _load_videos_cached()
    categories = load_categories()
    valid_videos = []

    for idx, v in enumerate(videos):
        if v.get("is_duplicate", False):
            continue
        v_path = v.get("path", "")
        if any(v_path.startswith(exc) for exc in EXCLUDED_FOLDERS):
            continue

        v_copy = dict(v)
        vid_id = str(v_copy.get("id", idx))
        v_copy["category"] = categories.get(vid_id, "")
        valid_videos.append(v_copy)

    return valid_videos

@app.get("/api/categories")
def get_categories(request: Request):
    verificar_credenciales(request)
    categories = load_categories()
    return sorted(list(set(c for c in categories.values() if c)))

@app.post("/api/videos/category")
def set_video_category(req: CategoryRequest, request: Request):
    verify_admin(request)
    categories = load_categories()
    if req.category.strip():
        categories[str(req.id)] = req.category.strip()
    else:
        if str(req.id) in categories:
            del categories[str(req.id)]
    save_json(CATEGORIES_FILE, categories)
    return {"success": True}

@app.get("/api/dashboard_stats")
def get_dashboard_stats(request: Request):
    verificar_credenciales(request)
    videos = _load_videos_cached()
    valid_videos = [v for v in videos if not v.get("is_duplicate", False) and not any(v.get("path", "").startswith(exc) for exc in EXCLUDED_FOLDERS)]
    total_vids = len(valid_videos)
    
    if total_vids == 0:
        return {"total_videos": 0, "total_size_gb": 0, "avg_size_mb": 0, "total_duration_hrs": 0, "avg_duration_sec": 0}
        
    total_size = sum(float(v.get("size_mb", 0)) for v in valid_videos if v.get("size_mb") is not None)
    total_duration = sum(float(v.get("duration", 0)) for v in valid_videos if v.get("duration") is not None)
    
    return {
        "total_videos": total_vids,
        "total_size_gb": round(total_size / 1024, 2),
        "avg_size_mb": round(total_size / total_vids, 2),
        "total_duration_hrs": round(total_duration / 3600, 2),
        "avg_duration_sec": round(total_duration / total_vids, 2)
    }

@app.get("/api/status")
def get_status(request: Request):
    verificar_credenciales(request)
    state = load_json(STATE_FILE, {})
    videos = _load_videos_cached()
    state["total_count"] = len(videos)
    global ACTIVE_SCANNER_PROCESS
    if state.get("is_completed") is False:
        if ACTIVE_SCANNER_PROCESS is None or ACTIVE_SCANNER_PROCESS.poll() is not None:
            state["is_completed"] = "ZOMBIE"
    return state

@app.get("/api/folders")
def get_folders(request: Request):
    verificar_credenciales(request)
    stats = load_json(FOLDER_STATS_FILE, {})
    return list(stats.values())

@app.get("/api/errors")
def get_errors(request: Request):
    verificar_credenciales(request)
    return load_json(ERROR_LOG_FILE, [])

@app.post("/api/errors/clear")
def clear_errors(request: Request):
    verify_admin(request)
    save_json(ERROR_LOG_FILE, [])
    return {"success": True}

@app.post("/api/scan")
def trigger_scan(req: ScanRequest = ScanRequest(), request: Request = None):
    verify_admin(request)
    launch_scanner_process(resume=req.resume, clean_first=req.clean_first, target_path=req.path)
    launch_geolocator_low_priority()
    return {"message": "Escaneo iniciado."}

@app.post("/api/scan/stop")
def stop_scan(request: Request):
    verify_admin(request)
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

@app.post("/api/videos/rename")
def rename_video(req: RenameRequest, request: Request):
    verify_admin(request)
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
def delete_video(req: DeleteRequest, request: Request):
    verify_admin(request)
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
def stream_video(video_id: int, request: Request):
    verificar_credenciales(request)
    videos = _load_videos_cached()
    path = next((v.get("path") for v in videos if v.get("id") == video_id), None)
    if not path or not os.path.exists(path):
        raise HTTPException(status_code=404, detail="El vídeo no existe en el disco.")
    if needs_transcoding(path):
        return StreamingResponse(ffmpeg_stream_generator(path), media_type="video/mp4")
    else:
        return FileResponse(path)

@app.get("/share/{video_id}/video.mp4")
def share_video_whatsapp(video_id: int):
    # Endpoint sin autenticación para compartir por enlace directo (WhatsApp, etc.).
    # El video_id no es secreto, pero la URL completa sí actúa como token.
    videos = _load_videos_cached()
    path = next((v.get("path") for v in videos if v.get("id") == video_id), None)
    if not path or not os.path.exists(path):
        raise HTTPException(status_code=404, detail="El vídeo no existe en el disco.")
    if needs_transcoding(path):
        return StreamingResponse(ffmpeg_stream_generator(path), media_type="video/mp4")
    else:
        return FileResponse(path)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
