/* =============================================================================
   app.js — Lógica principal del Explorador Web NAS
   Depende de: jQuery, Bootstrap 5, DataTables + responsive
   ============================================================================= */

'use strict';

// ── Configuración ─────────────────────────────────────────────────────────────
const EXCLUDED_FOLDERS = ["/media/volumeUSB3/usbshare3-2"];

// ── Estado global ─────────────────────────────────────────────────────────────
let table          = null;
let rawFoldersData = [];
let currentPath    = "";
let isAdmin        = false;
let viewMode       = 'list';   // 'list' | 'grid'

// ── Filtro personalizado de DataTables ────────────────────────────────────────
$.fn.dataTable.ext.search.push(function (settings, data, dataIndex) {
    if (settings.nTable.id !== 'videosTable') return true;

    const rowData    = settings.aoData[dataIndex]._aData || {};
    const rowPath    = rowData.path || "";

    for (let i = 0; i < EXCLUDED_FOLDERS.length; i++) {
        if (rowPath.startsWith(EXCLUDED_FOLDERS[i])) return false;
    }

    const locFilter     = $('#filterLocation').val().toLowerCase();
    const catFilter     = $('#filterCategory').val().toLowerCase();
    const dateTextQuery = $('#filterDateText').val().toLowerCase().trim();
    const startDate     = $('#filterStartDate').val();
    const endDate       = $('#filterEndDate').val();

    const rowCaptureDate = rowData.capture_date || "";
    const rowLocation    = rowData.place_name   || "";
    const rowCategory    = rowData.category     || "";

    if (locFilter     && !rowLocation.toLowerCase().includes(locFilter))    return false;
    if (catFilter     && rowCategory.toLowerCase() !== catFilter)            return false;
    if (dateTextQuery && !rowCaptureDate.toLowerCase().includes(dateTextQuery)) return false;

    if ($('#filterWithThumb').is(':checked') && !rowData.thumb) return false;

    const sizeMin = parseFloat($('#filterSizeMin').val());
    const sizeMax = parseFloat($('#filterSizeMax').val());
    const rowSize = parseFloat(rowData.size_mb) || 0;
    if (!isNaN(sizeMin) && rowSize < sizeMin) return false;
    if (!isNaN(sizeMax) && rowSize > sizeMax) return false;

    if (startDate || endDate) {
        if (!rowCaptureDate || rowCaptureDate === "No disponible") return false;
        const rowDateOnly = rowCaptureDate.substring(0, 10);
        if (startDate && rowDateOnly < startDate) return false;
        if (endDate   && rowDateOnly > endDate)   return false;
    }
    return true;
});

// ── Inicialización ────────────────────────────────────────────────────────────
$(document).ready(function () {
    // Cargar info de usuario antes de montar la tabla
    fetch('/api/user_info')
        .then(r => r.json())
        .then(userInfo => {
            isAdmin = userInfo.is_admin;
            document.getElementById('currentUsername').textContent = userInfo.username || '?';

            if (isAdmin) {
                document.getElementById('currentUserBadge').classList.replace('bg-secondary', 'bg-danger');
                document.getElementById('adminScanButtons').style.display = 'flex';
                document.getElementById('btnClearErrors').style.display  = 'inline-block';
                document.getElementById('errorsTabItem').style.removeProperty('display');
                loadErrorsLogCount();
            }

            _initDataTable();
        });

    loadStats();
    loadCategoriesDropdown();

    $('#filterLocation, #filterCategory, #filterDateText, #filterStartDate, #filterEndDate').on('keyup change', () => { if (table) table.draw(); });
    $('#filterWithThumb').on('change', () => { if (table) table.draw(); });
    $('#filterSizeMin, #filterSizeMax').on('input change', () => { if (table) table.draw(); });

    setInterval(checkStatus, 3000);
});

// ── DataTable ─────────────────────────────────────────────────────────────────
function _initDataTable() {
    table = $('#videosTable').DataTable({
        ajax:        { url: '/api/videos', dataSrc: '' },
        deferRender: true,
        language:    { url: 'https://cdn.datatables.net/plug-ins/1.13.6/i18n/es-ES.json' },
        responsive: {
            details: {
                renderer: function(api, rowIdx, columns) {
                    const row    = api.row(rowIdx).data();
                    const hidden = columns.filter(col => col.hidden);

                    let html = '';
                    if (row.thumb) {
                        html += '<div class="text-center mb-2">' +
                            '<img src="/static/' + row.thumb + '" ' +
                            'style="max-width:100%;width:400px;border-radius:8px;" loading="lazy" alt="Miniatura">' +
                            '</div>';
                    }
                    const rows = hidden.map(col =>
                        '<tr><td class="fw-bold pe-2" style="white-space:nowrap">' + col.title + ':</td>' +
                        '<td>' + col.data + '</td></tr>'
                    ).join('');
                    return (html + (rows ? '<table class="table table-sm mb-0">' + rows + '</table>' : '')) || false;
                }
            }
        },
        columns: [
            {
                data: 'name',
                className: 'fw-bold all',
                render(data, type, row) {
                    const safeName    = _escapeName(row.name);
                    const displayName = _escapeHtml(row.name);
                    const link = `<a href="javascript:void(0);" onclick="playVideo(${row.id}, '${safeName}')"
                               class="text-decoration-none text-primary d-inline-block py-1">
                                <i class="bi bi-play-circle-fill me-1 text-primary d-md-none"></i>${displayName}
                            </a>`;
                    if (!row.thumb) return link;
                    return `<span class="thumb-trigger d-inline-flex align-items-center gap-1"
                                  data-thumb="/static/${row.thumb}">${link}<span
                                  class="d-none d-md-inline text-muted thumb-icon"
                                  title="Ver miniatura">🎞️</span></span>`;
                }
            },
            { data: 'duration',      className: 'min-tablet', render(data) { const v = parseFloat(data); return isNaN(v) ? data : v.toFixed(2); } },
            { data: 'size_mb',       className: 'desktop',    render(data) { const v = parseFloat(data); return isNaN(v) ? data : v.toFixed(2); } },
            { data: 'capture_date',  className: 'all' },
            { data: 'file_date',     className: 'desktop' },
            {
                data: 'place_name',
                className: 'all',
                render(data) {
                    if (!data) return '<span class="text-muted small">Desconocida</span>';
                    return `<span class="badge bg-info text-dark"><i class="bi bi-geo-alt-fill"></i> ${data}</span>`;
                }
            },
            { data: 'path', className: 'none' },
            {
                data: 'category',
                className: 'all',
                render(data) {
                    if (!data) return '<span class="text-muted small">Sin categoría</span>';
                    return `<span class="badge bg-secondary"><i class="bi bi-tag-fill"></i> ${data}</span>`;
                }
            },
            {
                data: null,
                className: 'all text-end',
                render(data, type, row) {
                    const safeName  = _escapeName(row.name);
                    const safeCat   = (row.category || '').replace(/'/g, "\\'");
                    const adminBtns = isAdmin ? `
                        <button class="btn btn-sm btn-outline-warning" title="Asignar categoría"
                                onclick="editCategory(${row.id}, '${safeCat}')">
                            <i class="bi bi-tag"></i>
                        </button>
                        <button class="btn btn-sm btn-outline-secondary"
                                onclick="renameVideo(${row.id}, '${safeName}')">
                            <i class="bi bi-pencil"></i>
                        </button>
                        <button class="btn btn-sm btn-outline-danger"
                                onclick="deleteVideo(${row.id}, '${safeName}')">
                            <i class="bi bi-trash"></i>
                        </button>` : '';

                    return `<div class="d-flex justify-content-end gap-1">
                                ${adminBtns}
                                <button class="btn btn-sm btn-success" title="Copiar enlace WhatsApp"
                                        onclick="copyDirectLink(${row.id})">
                                    <i class="bi bi-whatsapp"></i>
                                </button>
                                <button class="btn btn-sm btn-primary" title="Reproducir"
                                        onclick="playVideo(${row.id}, '${safeName}')">
                                    <i class="bi bi-play-fill"></i>
                                </button>
                            </div>`;
                }
            }
        ]  
    });

    // Re-renderizar cuadrícula cuando los datos cambien
    table.on('draw', function () {
        if (viewMode === 'grid') renderGrid();
    });
}

// ── Helpers de escape ─────────────────────────────────────────────────────────
function _escapeHtml(str) {
    return (str || '').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}
function _escapeName(str) {
    return (str || '').replace(/\\/g, '\\\\').replace(/'/g, "\\'").replace(/"/g, '&quot;');
}

// ── Vista cuadrícula ──────────────────────────────────────────────────────────
function setViewMode(mode) {
    viewMode = mode;
    const isList = mode === 'list';
    document.getElementById('btnListView').classList.toggle('active', isList);
    document.getElementById('btnGridView').classList.toggle('active', !isList);
    document.querySelector('.table-responsive').classList.toggle('d-none', !isList);
    document.getElementById('videosGrid').classList.toggle('d-none', isList);
    if (!isList) renderGrid();
}

function renderGrid() {
    if (!table) return;
    const container = document.getElementById('videosGrid');
    const rows = table.rows({ search: 'applied' }).data().toArray()
        .filter(r => r.thumb);

    if (!rows.length) {
        container.innerHTML = '<div class="text-center text-muted py-5 fs-5">'
            + '🎞️ No hay vídeos con miniatura en la selección actual.</div>';
        return;
    }

    container.innerHTML = rows.map(r => {
        const safeName    = _escapeName(r.name);
        const displayName = _escapeHtml(r.name);
        const date  = (r.capture_date || '').substring(0, 10) || '—';
        const size  = parseFloat(r.size_mb)  ? parseFloat(r.size_mb).toFixed(2)  + ' MB' : '—';
        const dur   = parseFloat(r.duration) ? parseFloat(r.duration).toFixed(2) + ' s'  : '—';
        const place = r.place_name ? `<br>📍 ${_escapeHtml(r.place_name)}` : '';
        return `<div class="grid-card" onclick="playVideo(${r.id}, '${safeName}')">
            <img src="/static/${r.thumb}" alt="${displayName}" loading="lazy">
            <div class="grid-card-overlay">
                <div class="grid-card-title">${displayName}</div>
                <div class="grid-card-meta">
                    📅 ${date} &nbsp;⋅&nbsp; 💾 ${size} &nbsp;⋅&nbsp; ⏱ ${dur}${place}
                </div>
            </div>
        </div>`;
    }).join('');
}

// ── Categorías ────────────────────────────────────────────────────────────────
function loadCategoriesDropdown() {
    fetch('/api/categories')
        .then(r => r.json())
        .then(cats => {
            const select   = $('#filterCategory');
            const currentVal = select.val();
            select.html('<option value="">Todas las categorías...</option>');
            cats.forEach(c => select.append(`<option value="${c}">${c}</option>`));
            if (currentVal) select.val(currentVal);
        });
}

function editCategory(id, currentCat) {
    if (!isAdmin) return alert("Acción restringida al administrador.");
    const newCat = prompt("Categoría para este vídeo (vacío para eliminarla):", currentCat);
    if (newCat === null) return;
    fetch('/api/videos/category', {
        method:  'POST',
        headers: { 'Content-Type': 'application/json' },
        body:    JSON.stringify({ id, category: newCat.trim() })
    })
    .then(r => { if (!r.ok) throw new Error(); return r.json(); })
    .then(d => { if (d.success) { table.ajax.reload(null, false); loadCategoriesDropdown(); } })
    .catch(() => alert("Error: no tienes permisos para esta acción."));
}

// ── Estadísticas ──────────────────────────────────────────────────────────────
function loadStats() {
    fetch('/api/dashboard_stats')
        .then(r => { if (!r.ok) throw new Error(); return r.json(); })
        .then(data => {
            document.getElementById('statTotalVids').innerText  = (data.total_videos       || 0).toLocaleString('es-ES');
            document.getElementById('statTotalSize').innerText  = (data.total_size_gb      || 0).toLocaleString('es-ES') + ' GB';
            document.getElementById('statTotalHours').innerText = (data.total_duration_hrs || 0).toLocaleString('es-ES') + ' h';
            document.getElementById('statAvgSize').innerText    = (data.avg_size_mb        || 0).toLocaleString('es-ES') + ' MB';
            document.getElementById('statThumbsCount').innerText = (data.thumbs_count      || 0).toLocaleString('es-ES');
        })
        .catch(err => console.error("Error al cargar estadísticas", err));
}

// ── Escaneo ───────────────────────────────────────────────────────────────────
function triggerScan(resume, path = null) {
    if (!isAdmin) return alert("Acción restringida al administrador.");
    fetch('/api/scan', {
        method:  'POST',
        headers: { 'Content-Type': 'application/json' },
        body:    JSON.stringify({ resume, clean_first: !resume, path })
    })
    .then(r => { if (!r.ok) throw new Error(); return r.json(); })
    .then(() => checkStatus())
    .catch(() => alert("Error: no tienes permisos para iniciar escaneos."));
}

function stopScan() {
    if (!isAdmin) return alert("Acción restringida al administrador.");
    fetch('/api/scan/stop', { method: 'POST' })
        .then(r => { if (!r.ok) throw new Error(); return r.json(); })
        .then(() => checkStatus())
        .catch(() => alert("Error al cancelar el escaneo."));
}

function scanSpecificFolder(folderPath) {
    if (!isAdmin) return alert("Acción restringida al administrador.");
    if (confirm(`¿Escanear únicamente:\n${folderPath}?`)) {
        triggerScan(false, folderPath);
        alert("Escaneo específico iniciado.");
    }
}

// ── Enlace directo (WhatsApp) ─────────────────────────────────────────────────
function copyDirectLink(id) {
    const { protocol, hostname, port } = window.location;
    const isLocal = hostname.startsWith("192.168.") || hostname === "localhost" || hostname === "127.0.0.1";
    const base    = protocol + "//" + hostname + (isLocal && port ? ":" + port : "");
    const url     = `${base}/share/${id}/video.mp4`;

    if (navigator.clipboard && window.isSecureContext) {
        navigator.clipboard.writeText(url)
            .then(() => alert("✅ ¡Enlace copiado!\n🔗 " + url))
            .catch(() => _fallbackCopy(url));
    } else {
        _fallbackCopy(url);
    }
}

function _fallbackCopy(text) {
    const ta = document.createElement("textarea");
    ta.value = text;
    ta.style.position = "fixed";
    document.body.appendChild(ta);
    ta.focus(); ta.select();
    try {
        document.execCommand('copy')
            ? alert("✅ ¡Enlace copiado!\n🔗 " + text)
            : alert("⚠️ Cópialo manualmente:\n\n" + text);
    } catch {
        alert("⚠️ Error. Cópialo manualmente:\n\n" + text);
    }
    document.body.removeChild(ta);
}

// ── Filtros ───────────────────────────────────────────────────────────────────
function resetFilters() {
    $('#filterLocation, #filterCategory, #filterDateText, #filterStartDate, #filterEndDate').val('');
    $('#filterSizeMin, #filterSizeMax').val('');
    $('#filterWithThumb').prop('checked', false);
    if (table) table.draw();
}

// ── CRUD de vídeos ────────────────────────────────────────────────────────────
function renameVideo(id, currentName) {
    if (!isAdmin) return alert("Acción restringida al administrador.");
    const newName = prompt("Nuevo nombre:", currentName);
    if (!newName || !newName.trim() || newName === currentName) return;
    fetch('/api/videos/rename', {
        method:  'POST',
        headers: { 'Content-Type': 'application/json' },
        body:    JSON.stringify({ id, new_name: newName.trim() })
    })
    .then(r => { if (!r.ok) throw new Error(); return r.json(); })
    .then(d => { if (d.success && table) table.ajax.reload(null, false); })
    .catch(() => alert("Error: no tienes permisos para renombrar vídeos."));
}

function deleteVideo(id, name) {
    if (!isAdmin) return alert("Acción restringida al administrador.");
    if (!confirm(`¿Eliminar "${name}" de la web?`)) return;
    fetch('/api/videos/delete', {
        method:  'POST',
        headers: { 'Content-Type': 'application/json' },
        body:    JSON.stringify({ id })
    })
    .then(r => { if (!r.ok) throw new Error(); return r.json(); })
    .then(d => { if (d.success && table) table.ajax.reload(null, false); })
    .catch(() => alert("Error: no tienes permisos para eliminar vídeos."));
}

// ── Reproductor ───────────────────────────────────────────────────────────────
function playVideo(id, safeName) {
    document.getElementById('videoModalTitle').innerText = safeName;
    const player   = document.getElementById('webVideoPlayer');
    const alertBox = document.getElementById('videoErrorAlert');

    alertBox.classList.add('d-none');
    player.style.display = 'block';
    player.onerror = () => { alertBox.classList.remove('d-none'); player.style.display = 'none'; };
    player.src = `/api/stream?video_id=${id}`;

    new bootstrap.Modal(document.getElementById('videoModal')).show();
}

function stopVideo() {
    const player = document.getElementById('webVideoPlayer');
    player.pause();
    player.src = "";
    document.getElementById('videoErrorAlert').classList.add('d-none');
    player.style.display = 'block';
}

// ── Estado del escáner ────────────────────────────────────────────────────────
function checkStatus() {
    fetch('/api/status')
        .then(r => r.json())
        .then(data => {
            const statusEl  = document.getElementById('statusText');
            const cancelBtn = document.getElementById('btnCancelScan');

            if (cancelBtn && isAdmin) {
                cancelBtn.style.display = data.is_completed === false ? 'inline-block' : 'none';
            }

            if (!statusEl) return;

            if (data.is_completed === "ZOMBIE") {
                statusEl.innerHTML = `<span class="badge bg-danger">
                    <i class="bi bi-x-circle-fill"></i>
                    Escáner detenido inesperadamente | Pulsa "Retomar" o revisa Errores
                </span>`;
            } else if (data.is_completed === false) {
                const files   = data.files_scanned || 0;
                const startTs = data.start_timestamp || 0;
                let speedText = "", elapsedText = "";

                if (startTs > 0) {
                    const elapsed = Math.floor(Date.now() / 1000 - startTs);
                    if (elapsed > 0) {
                        elapsedText = ` | ⏱️ ${Math.floor(elapsed / 60)}m ${elapsed % 60}s`;
                        if (files > 0) speedText = ` (${(files / elapsed).toFixed(1)} arch/s)`;
                    }
                }
                statusEl.innerHTML = `<span class="badge bg-warning text-dark">
                    <span class="spinner-border spinner-border-sm me-1"></span>
                    Escaneando... | 📁 Ficheros: <strong>${files}</strong>${speedText}${elapsedText}
                    | 📍 ${data.last_folder || 'Iniciando...'}
                </span>`;
                if (table) table.ajax.reload(null, false);
            } else {
                statusEl.innerHTML = `<span class="badge bg-success">
                    Completado | Último escaneo: ${data.last_run || 'N/A'}
                </span>`;
            }
        });
    loadErrorsLogCount();
}

// ── Errores ───────────────────────────────────────────────────────────────────
function loadErrorsLogCount() {
    fetch('/api/errors')
        .then(r => r.json())
        .then(data => { document.getElementById('errorBadgeCount').innerText = data.length || 0; });
}

function loadErrorsLog() {
    fetch('/api/errors')
        .then(r => r.json())
        .then(data => {
            const container = document.getElementById('errorsListContainer');
            document.getElementById('errorBadgeCount').innerText = data.length || 0;

            if (!data || data.length === 0) {
                container.innerHTML = '<div class="p-4 text-center text-muted">No hay errores registrados.</div>';
                return;
            }
            container.innerHTML = data.map(err =>
                `<div class="p-2 border-bottom text-danger font-monospace small">
                    <strong>[${err.context}]</strong> ${err.timestamp}: ${err.message}
                 </div>`
            ).join('');
        });
}

function clearErrorsLog() {
    if (!isAdmin) return alert("Acción restringida al administrador.");
    fetch('/api/errors/clear', { method: 'POST' })
        .then(r => { if (!r.ok) throw new Error(); loadErrorsLog(); })
        .catch(() => alert("Error: no tienes permisos para limpiar los errores."));
}

// ── Explorador de carpetas ────────────────────────────────────────────────────
function initFolderExplorer() {
    fetch('/api/folders')
        .then(r => r.json())
        .then(data => {
            rawFoldersData = data.filter(f =>
                !EXCLUDED_FOLDERS.some(exc => f.path.startsWith(exc))
            );
            currentPath = "";
            renderFolderView("");
        });
}

function renderFolderView(targetPath) {
    currentPath = targetPath;
    const container = document.getElementById('foldersListContainer');

    // Breadcrumb
    let breadcrumbHtml = `<li class="breadcrumb-item"><a onclick="renderFolderView('')">Raíz</a></li>`;
    if (targetPath) {
        let accumulated = "";
        targetPath.split('/').filter(p => p).forEach((part, index, parts) => {
            accumulated += "/" + part;
            breadcrumbHtml += index === parts.length - 1
                ? `<li class="breadcrumb-item active">${part}</li>`
                : `<li class="breadcrumb-item"><a onclick="renderFolderView('${accumulated}')">${part}</a></li>`;
        });
    }
    document.getElementById('folderBreadcrumbs').innerHTML = breadcrumbHtml;

    // Hijos directos
    let children = [];
    if (targetPath === "") {
        const rootsMap = {};
        rawFoldersData.forEach(f => {
            const parts = f.path.split('/').filter(p => p);
            if (parts.length >= 2) {
                const rootPath = "/" + parts[0] + "/" + parts[1];
                if (!rootsMap[rootPath]) {
                    rootsMap[rootPath] = { path: rootPath, total_files: 0, videos_found: 0, scanned_at: f.scanned_at };
                }
                rootsMap[rootPath].total_files   += (f.total_files   || 0);
                rootsMap[rootPath].videos_found  += (f.videos_found  || 0);
            }
        });
        children = Object.values(rootsMap);
    } else {
        const depth = targetPath.split('/').filter(p => p).length;
        rawFoldersData.forEach(f => {
            if (f.path.startsWith(targetPath + "/") && f.path !== targetPath &&
                f.path.split('/').filter(p => p).length === depth + 1) {
                children.push(f);
            }
        });
    }

    if (children.length === 0) {
        container.innerHTML = '<div class="p-4 text-center text-muted">No hay subcarpetas registradas.</div>';
        return;
    }

    const scanBtn = (path) => isAdmin
        ? `<button class="btn btn-sm btn-outline-primary" title="Escanear solo esta carpeta"
                   onclick="scanSpecificFolder('${path}')">
               <i class="bi bi-arrow-clockwise"></i> Escanear
           </button>`
        : '';

    container.innerHTML = children
        .sort((a, b) => a.path.localeCompare(b.path))
        .map(folder => {
            const name = folder.path.split('/').pop();
            return `<div class="folder-item">
                        <div style="cursor:pointer;flex-grow:1;" onclick="renderFolderView('${folder.path}')">
                            <i class="bi bi-folder-fill text-warning me-2 fs-5"></i>
                            <strong>${name}</strong><br>
                            <small class="text-muted ms-4" style="font-size:0.75rem;">${folder.path}</small>
                        </div>
                        <div class="d-flex align-items-center gap-2">
                            <span class="badge bg-secondary">Ficheros: ${folder.total_files || 0}</span>
                            <span class="badge bg-success">Vídeos: ${folder.videos_found || 0}</span>
                            ${scanBtn(folder.path)}
                        </div>
                    </div>`;
        })
        .join('');
}

// ── Popup de miniatura al pasar el ratón (sólo dispositivos con hover) ────────
(function () {
    if (!window.matchMedia('(hover: hover)').matches) return;

    const popup = document.createElement('div');
    popup.id = 'thumbPopup';
    Object.assign(popup.style, {
        display: 'none', position: 'fixed', zIndex: '9999',
        pointerEvents: 'none', borderRadius: '10px',
        boxShadow: '0 6px 24px rgba(0,0,0,.6)', overflow: 'hidden',
        background: '#000', transition: 'opacity .1s',
    });
    const img = document.createElement('img');
    Object.assign(img.style, { display: 'block', width: '400px', height: 'auto' });
    popup.appendChild(img);
    document.body.appendChild(popup);

    let activeTrigger = null;

    document.addEventListener('mouseover', function (e) {
        const el = e.target.closest('.thumb-trigger');
        if (!el) {
            popup.style.display = 'none';
            activeTrigger = null;
            return;
        }
        if (el === activeTrigger) return;
        activeTrigger = el;
        img.src = el.dataset.thumb;
        popup.style.display = 'block';
    });

    document.addEventListener('mousemove', function (e) {
        if (popup.style.display === 'none') return;
        const offset = 18;
        const pw = popup.offsetWidth  || 400;
        const ph = popup.offsetHeight || 225;
        let x = e.clientX + offset;
        let y = e.clientY + offset;
        if (x + pw > window.innerWidth  - 8) x = e.clientX - pw - offset;
        if (y + ph > window.innerHeight - 8) y = e.clientY - ph - offset;
        popup.style.left = x + 'px';
        popup.style.top  = y + 'px';
    });

    document.addEventListener('mouseout', function (e) {
        if (!activeTrigger) return;
        if (!activeTrigger.contains(e.relatedTarget)) {
            popup.style.display = 'none';
            activeTrigger = null;
        }
    });
}());
