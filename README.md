# scanner-videos
Aplicación web autoadministrable desarrollada en **Python (FastAPI)** y **Docker**, diseñada para indexar, explorar, buscar, reproducir y gestionar grandes volúmenes de archivos multimedia (más de 35.000 vídeos) en un NAS Synology.

## 🚀 Tecnologías Utilizadas

*   **Backend:**
    *   **Python 3.x / FastAPI:** Framework web de alto rendimiento para la API REST y el streaming de vídeo.
    *   **Uvicorn:** Servidor ASGI para FastAPI.
    *   **FFmpeg / FFprobe:** Transcodificación condicional de vídeo en tiempo real para asegurar compatibilidad web universal (`h264`, `aac`, fragmentación de MP4).
    *   **Pydantic:** Validación de esquemas de datos y peticiones HTTP.
*   **Frontend:**
    *   **Bootstrap 5 & Bootstrap Icons:** Interfaz de usuario responsive, moderna y optimizada para dispositivos móviles.
    *   **DataTables (jQuery):** Tablas de datos de alto rendimiento optimizadas para decenas de miles de registros con paginación y renderizado diferido (`deferRender`).
    *   **JavaScript Vanilla:** Gestión dinámica de enlaces inteligentes (detectando automáticamente si se accede desde la red local o desde la web pública), reproductores multimedia y llamadas asíncronas (`fetch`).
*   **Almacenamiento y Estado (Bases de datos JSON planas):**
    *   `videos.json`: Metadatos indexados de los vídeos (ruta, tamaño, duración, fechas y ubicación).
    *   `video_categories.json`: Gestión de etiquetas y categorías personalizadas por vídeo.
    *   `scanner_state.json`: Estado en tiempo real del proceso de escaneo.
    *   `folder_stats.json`: Estadísticas y contadores por directorio.
    *   `errors.json`: Registro centralizado de errores del sistema.
    *   `auth.json`: Credenciales de usuarios controladas por multi-usuario.
*   **Infraestructura y Despliegue:**
    *   **Docker & Docker Compose:** Contenedorización y aislamiento de la aplicación en el NAS.

---

## 🔒 Seguridad y Arquitectura Multi-usuario

*   **Autenticación HTTP Basic:** El sistema soporta múltiples usuarios configurables mediante el fichero `auth.json` (por ejemplo, `admin` y `familia`), permitiendo revocar accesos al instante simplemente eliminando el usuario del fichero.
*   **Excepción de Enlaces Públicos (`/share/`):** Las rutas de reproducción y compartición directa de vídeos (`/share/{id}/video.mp4`) están abiertas al público sin credenciales, permitiendo enviar enlaces directamente por WhatsApp o mensajería. Esto permite que terceros reproduzcan el vídeo exacto al instante sin exponer las credenciales del panel de control ni dar acceso a la base de datos general.
*   **Middleware de Auditoría:** Registro automático de todas las peticiones HTTP, direcciones IP y códigos de estado en ficheros de logs diarios ubicados en `logs/accesos_YYYY-MM-DD.log`.

---

## 🌐 Configuración de Red: Router y Proxy Inverso Synology

Para acceder de forma segura a la aplicación desde el exterior mediante el dominio público `https://davidesi.ddns.net`:

### 1. Configuración del Router
*   **Reenvío de Puertos (Port Forwarding):** Abrir y redirigir el puerto **443 (TCP)** externo hacia la dirección IP local de tu NAS Synology (`192.168.1.103`).

### 2. Configuración en el NAS Synology (DSM)
*   **Portal de Inicio de Sesión:** Asegúrate de que **ningún** servicio nativo de Synology (ni el propio DSM, ni File Station, ni Fotos) tenga configurado el dominio `davidesi.ddns.net` en su apartado de "Dominio personalizado", para evitar que el servidor web interno (Nginx) intercepte la petición antes de que llegue al proxy inverso.
*   **Proxy Inverso:**
    *   Ir a **Panel de control** > **Portal de inicio de sesión** > **Avanzado** > **Proxy Inverso** y crear la regla:
    *   **Origen:**
        *   Protocolo: `HTTPS`
        *   Nombre de host: `davidesi.ddns.net`
        *   Puerto: `443`
    *   **Destino:**
        *   Protocolo: `HTTP`
        *   Nombre de host: `192.168.1.103` (IP local del NAS)
        *   Puerto: `8000`
    *   *Nota:* Asocia un certificado SSL válido (por ejemplo, Let's Encrypt) a este dominio en la sección de seguridad del DSM.

---

## ⚙️ Despliegue y Ejecución

Para compilar y levantar el contenedor asegurando que se empaquetan todos los scripts actualizados, ejecuta en la terminal SSH de tu NAS:

```bash
sudo docker compose up --build -d
