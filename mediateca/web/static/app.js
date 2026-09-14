const mediateca = (() => {
  function escapeHtml(str) {
    const div = document.createElement("div");
    div.textContent = str ?? "";
    return div.innerHTML;
  }

  function fmtDuration(seconds) {
    if (!seconds) return "—";
    seconds = Math.round(seconds);
    const h = Math.floor(seconds / 3600);
    const m = Math.floor((seconds % 3600) / 60);
    const s = seconds % 60;
    const pad = (n) => String(n).padStart(2, "0");
    return h ? `${h}:${pad(m)}:${pad(s)}` : `${m}:${pad(s)}`;
  }

  function cardHtml(item) {
    const thumb = item.thumbnail_path
      ? `<img src="/media/${encodeURI(item.thumbnail_path)}" alt="" loading="lazy">`
      : `<div class="card-thumb-placeholder">${escapeHtml((item.media_type || "?")[0].toUpperCase())}</div>`;
    return `
      <a class="card" href="/item/${item.id}">
        <div class="card-thumb">
          ${thumb}
          <span class="badge badge-duration">${fmtDuration(item.duration)}</span>
        </div>
        <div class="card-body">
          <h3 class="card-title">${escapeHtml(item.title)}</h3>
          <p class="card-meta">${escapeHtml(item.uploader || "Autor desconocido")}</p>
          <span class="badge badge-platform">${escapeHtml(item.extractor)}</span>
        </div>
      </a>`;
  }

  function debounce(fn, wait) {
    let t;
    return (...args) => {
      clearTimeout(t);
      t = setTimeout(() => fn(...args), wait);
    };
  }

  function initLibraryPage() {
    const grid = document.getElementById("grid");
    const emptyMsg = document.getElementById("empty-msg");
    const searchInput = document.getElementById("search-input");
    const platformFilter = document.getElementById("platform-filter");
    const loadMoreBtn = document.getElementById("load-more-btn");
    const PAGE_SIZE = 60;
    // El primer bloque de tarjetas viene ya renderizado por el servidor
    // (para no duplicar esa carga con un fetch extra al abrir la página);
    // el botón "Cargar más" trae su punto de partida en data-offset.
    let offset = loadMoreBtn ? parseInt(loadMoreBtn.dataset.offset, 10) || 0 : 0;

    function buildParams(offsetValue) {
      const params = new URLSearchParams();
      if (searchInput.value.trim()) params.set("q", searchInput.value.trim());
      if (platformFilter.value) params.set("platform", platformFilter.value);
      params.set("limit", PAGE_SIZE);
      params.set("offset", offsetValue);
      return params;
    }

    function setLoadMoreVisible(visible) {
      if (loadMoreBtn) loadMoreBtn.style.display = visible ? "inline-block" : "none";
    }

    async function refresh() {
      const res = await fetch(`/api/library?${buildParams(0).toString()}`);
      const items = await res.json();

      grid.innerHTML = items.map(cardHtml).join("");
      emptyMsg.style.display = items.length ? "none" : "block";
      offset = items.length;
      // Si volvió una página llena, puede que haya más detrás.
      setLoadMoreVisible(items.length === PAGE_SIZE);
    }

    async function loadMore() {
      loadMoreBtn.disabled = true;
      try {
        const res = await fetch(`/api/library?${buildParams(offset).toString()}`);
        const items = await res.json();
        grid.insertAdjacentHTML("beforeend", items.map(cardHtml).join(""));
        offset += items.length;
        setLoadMoreVisible(items.length === PAGE_SIZE);
      } finally {
        loadMoreBtn.disabled = false;
      }
    }

    searchInput.addEventListener("input", debounce(refresh, 300));
    platformFilter.addEventListener("change", refresh);
    if (loadMoreBtn) loadMoreBtn.addEventListener("click", loadMore);
  }

  function jobStateLabel(state) {
    const labels = {
      en_cola: "En cola",
      descargando: "Descargando",
      procesando: "Procesando",
      listo: "Completado",
      error: "Error",
      pausado: "Pausada",
      interrumpido: "Interrumpida",
    };
    return labels[state] || state;
  }

  const ACTIVE_JOB_STATES = ["en_cola", "descargando", "procesando"];
  const RESUMABLE_JOB_STATES = ["pausado", "error", "interrumpido"];
  const BAR_CLASS_BY_STATE = { error: "error", listo: "listo", pausado: "pausado", interrumpido: "pausado" };

  function jobRowHtml(job) {
    const pct = Math.max(0, Math.min(100, job.progress || 0));
    const barClass = BAR_CLASS_BY_STATE[job.state] || "";
    const canPause = ACTIVE_JOB_STATES.includes(job.state);
    const canResume = RESUMABLE_JOB_STATES.includes(job.state);
    const viewLink = job.state === "listo" && job.item_id
      ? `<a class="job-view-link" href="/item/${job.item_id}">Ver en la biblioteca</a>`
      : "";

    return `
      <div class="job-url">${escapeHtml(job.url)}</div>
      <div class="job-bar-track"><div class="job-bar-fill ${barClass}" style="width:${pct}%"></div></div>
      <div class="job-footer">
        <span class="job-state-label state-${job.state}">${escapeHtml(jobStateLabel(job.state))}</span>
        <span class="job-message">${escapeHtml(job.message || "")}</span>
        <span class="job-actions">
          ${canPause ? `<button type="button" class="btn-link" data-action="pause" data-id="${job.job_id}">Pausar</button>` : ""}
          ${canResume ? `<button type="button" class="btn-link" data-action="resume" data-id="${job.job_id}">Reanudar</button>` : ""}
        </span>
      </div>
      ${viewLink}`;
  }

  function fmtFilesize(bytes) {
    if (!bytes) return "";
    const units = ["B", "KB", "MB", "GB"];
    let size = bytes, i = 0;
    while (size >= 1024 && i < units.length - 1) { size /= 1024; i++; }
    return `${size.toFixed(1)} ${units[i]}`;
  }

  function formatOptionLabel(f) {
    const bits = [f.resolution || "audio"];
    if (f.fps) bits.push(`${f.fps}fps`);
    if (f.vcodec) bits.push(f.vcodec);
    if (f.acodec && !f.vcodec) bits.push(f.acodec);
    else if (!f.acodec && f.vcodec) bits.push("sin audio");
    if (f.ext) bits.push(f.ext);
    const size = fmtFilesize(f.filesize);
    if (size) bits.push(size);
    return `${bits.join(" · ")}${f.format_note ? " (" + f.format_note + ")" : ""}`;
  }

  function initDownloadPage() {
    const form = document.getElementById("download-form");
    const jobsEl = document.getElementById("jobs");
    const emptyMsg = document.getElementById("jobs-empty");
    const urlsField = document.getElementById("urls");
    const audioOnlyBox = document.getElementById("audio-only");
    const audioOptions = document.getElementById("audio-options");
    const previewBtn = document.getElementById("preview-btn");
    const previewEl = document.getElementById("preview");
    const pollers = {};
    // Vista previa activa (si la hay): recuerda para qué URL exacta se
    // pidió, para no usarla por accidente si el usuario cambió el texto.
    let preview = null;

    function toggleAudioOptions() {
      audioOptions.style.display = audioOnlyBox.checked ? "flex" : "none";
    }
    audioOnlyBox.addEventListener("change", toggleAudioOptions);
    toggleAudioOptions();

    function clearPreview() {
      preview = null;
      previewEl.style.display = "none";
      previewEl.innerHTML = "";
    }
    // Si el usuario toca el texto, la vista previa que tenía en pantalla
    // queda desactualizada (podría ser de otra URL, u otro video).
    urlsField.addEventListener("input", clearPreview);

    function singleUrlOrNull() {
      const lines = urlsField.value.split("\n").map((u) => u.trim()).filter(Boolean);
      return lines.length === 1 ? lines[0] : null;
    }

    function renderPreviewVideo(data, forUrl) {
      preview = { type: "video", forUrl, formats: data.formats };
      const thumb = data.thumbnail
        ? `<img class="preview-thumb" src="${escapeHtml(data.thumbnail)}" alt="">`
        : "";
      const options = data.formats.length
        ? data.formats.map(
            (f) => `<option value="${escapeHtml(f.format_id || "")}">${escapeHtml(formatOptionLabel(f))}</option>`
          ).join("")
        : `<option value="">(sin formatos detallados; se usará la calidad de arriba)</option>`;
      previewEl.innerHTML = `
        <div class="preview-head">
          ${thumb}
          <div>
            <div class="preview-title">${escapeHtml(data.title)}</div>
            <div class="muted small">${escapeHtml(data.uploader || "")}${data.duration ? " · " + fmtDuration(data.duration) : ""}</div>
          </div>
        </div>
        <label class="field">
          <span>Formato exacto</span>
          <select id="format-select">
            <option value="">Usar la calidad elegida arriba</option>
            ${options}
          </select>
        </label>
        <p class="muted small">Si eliges un formato de solo audio, "Solo audio" arriba lo convierte a tu formato preferido; si no, se guarda tal cual.</p>
      `;
      previewEl.style.display = "block";
    }

    function renderPreviewPlaylist(data, forUrl) {
      preview = { type: "playlist", forUrl, entries: data.entries };
      const rows = data.entries.map(
        (e, i) => `
          <label class="preview-entry">
            <input type="checkbox" class="preview-entry-check" data-index="${i}" checked>
            <span>${escapeHtml(e.title)}</span>
            ${e.duration ? `<span class="muted small">${fmtDuration(e.duration)}</span>` : ""}
          </label>`
      ).join("");
      previewEl.innerHTML = `
        <div class="preview-title">Lista de reproducción: ${escapeHtml(data.title)}</div>
        <div class="muted small">${data.entry_count} videos encontrados · ${escapeHtml(data.uploader || "")}</div>
        <div class="preview-select-all">
          <button type="button" class="btn-link" data-check="all">Marcar todos</button>
          <button type="button" class="btn-link" data-check="none">Desmarcar todos</button>
        </div>
        <div class="preview-entry-list">${rows}</div>
      `;
      previewEl.style.display = "block";
      previewEl.querySelectorAll("[data-check]").forEach((btn) => {
        btn.addEventListener("click", () => {
          const checked = btn.dataset.check === "all";
          previewEl.querySelectorAll(".preview-entry-check").forEach((cb) => (cb.checked = checked));
        });
      });
    }

    previewBtn.addEventListener("click", async () => {
      const url = singleUrlOrNull();
      if (!url) {
        clearPreview();
        previewEl.innerHTML = `<p class="muted small">Pega exactamente una URL para poder ver la vista previa.</p>`;
        previewEl.style.display = "block";
        return;
      }
      previewBtn.disabled = true;
      previewEl.style.display = "block";
      previewEl.innerHTML = `<p class="muted small">Consultando… (las listas largas pueden tardar unos segundos)</p>`;
      const controller = new AbortController();
      const timeout = setTimeout(() => controller.abort(), 25000);
      try {
        const res = await fetch("/api/probe", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ url }),
          signal: controller.signal,
        });
        const data = await res.json();
        if (!res.ok) throw new Error(data.detail || "No se pudo obtener información de esa URL.");
        if (data.is_playlist) renderPreviewPlaylist(data, url);
        else renderPreviewVideo(data, url);
      } catch (err) {
        clearPreview();
        const msg = err && err.name === "AbortError"
          ? "La consulta tardó demasiado. Prueba de nuevo o usa una URL de video suelto."
          : String((err && err.message) || err);
        previewEl.innerHTML = `<p class="job-message" style="color:var(--danger)">${escapeHtml(msg)}</p>`;
        previewEl.style.display = "block";
      } finally {
        clearTimeout(timeout);
        previewBtn.disabled = false;
      }
    });

    function setEmptyVisible(visible) {
      if (emptyMsg) emptyMsg.style.display = visible ? "block" : "none";
    }

    function upsertJobRow(job) {
      let row = document.getElementById(`job-${job.job_id}`);
      if (!row) {
        row = document.createElement("div");
        row.className = "job-row";
        row.id = `job-${job.job_id}`;
        jobsEl.prepend(row);
      }
      row.innerHTML = jobRowHtml(job);
      setEmptyVisible(false);
      return row;
    }

    function stopPolling(jobId) {
      if (pollers[jobId]) {
        pollers[jobId].close();
        delete pollers[jobId];
      }
    }

    function pollJob(jobId) {
      // Antes: setInterval haciendo fetch a /api/jobs/<id> cada segundo,
      // por cada job activo, por cada pestaña abierta. Ahora: una única
      // conexión persistente (Server-Sent Events) por job; el servidor
      // solo manda un evento nuevo cuando algo cambió de verdad.
      stopPolling(jobId);
      const source = new EventSource(`/api/jobs/${jobId}/stream`);
      pollers[jobId] = source;

      source.addEventListener("message", (ev) => {
        try {
          const job = JSON.parse(ev.data);
          upsertJobRow(job);
          if (!ACTIVE_JOB_STATES.includes(job.state)) stopPolling(jobId);
        } catch (err) {
          stopPolling(jobId);
        }
      });
      source.addEventListener("not_found", () => stopPolling(jobId));
      // EventSource reintenta solo por defecto; como el servidor siempre
      // cierra el stream cuando el job llega a un estado final o no existe
      // (arriba ya lo cerramos a mano en esos casos), un error aquí es un
      // corte de verdad: no tiene sentido seguir reintentando solos.
      source.onerror = () => stopPolling(jobId);
    }

    // Al abrir esta pestaña (o volver a ella, o reabrir el navegador),
    // recuperamos los trabajos guardados en el servidor en vez de empezar
    // con la lista vacía: así el progreso sobrevive a cambiar de pestaña y
    // a cerrar todo mientras algo se sigue descargando.
    (async () => {
      try {
        const res = await fetch("/api/jobs");
        const jobs = await res.json();
        setEmptyVisible(jobs.length === 0);
        for (const job of jobs) {
          upsertJobRow(job);
          if (ACTIVE_JOB_STATES.includes(job.state)) pollJob(job.job_id);
        }
      } catch (err) {
        // Sin conexión con el servidor todavía; se deja vacío sin más.
      }
    })();

    jobsEl.addEventListener("click", async (ev) => {
      const btn = ev.target.closest("button[data-action]");
      if (!btn) return;
      const jobId = btn.dataset.id;
      btn.disabled = true;
      try {
        if (btn.dataset.action === "pause") {
          await fetch(`/api/jobs/${jobId}/pause`, { method: "POST" });
        } else if (btn.dataset.action === "resume") {
          const res = await fetch(`/api/jobs/${jobId}/resume`, { method: "POST" });
          if (res.ok) {
            const jobRes = await fetch(`/api/jobs/${jobId}`);
            if (jobRes.ok) upsertJobRow(await jobRes.json());
            pollJob(jobId);
          }
        }
      } finally {
        btn.disabled = false;
      }
    });

    async function createDownloadJob(url, extra) {
      try {
        const res = await fetch("/api/download", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ url, ...extra }),
        });
        const data = await res.json();
        if (!res.ok) throw new Error(data.detail || "No se pudo iniciar la descarga");
        upsertJobRow({ job_id: data.job_id, url, state: "en_cola", progress: 0, message: "En cola…", item_id: null });
        pollJob(data.job_id);
      } catch (err) {
        const tempId = `tmp-${Date.now()}-${Math.random().toString(36).slice(2)}`;
        upsertJobRow({ job_id: tempId, url, state: "error", progress: 0, message: String(err.message || err), item_id: null });
      }
    }

    form.addEventListener("submit", async (ev) => {
      ev.preventDefault();
      const quality = document.getElementById("quality").value;
      const audioOnly = audioOnlyBox.checked;
      const audioFormat = document.getElementById("audio-format").value || undefined;
      const audioBitrate = document.getElementById("audio-bitrate").value || undefined;
      const baseExtra = { audio_only: audioOnly, quality, audio_format: audioFormat, audio_bitrate: audioBitrate };

      const currentUrl = singleUrlOrNull();
      const previewMatches = preview && currentUrl && preview.forUrl === currentUrl;

      if (previewMatches && preview.type === "playlist") {
        const checked = Array.from(previewEl.querySelectorAll(".preview-entry-check"))
          .map((cb, i) => (cb.checked ? preview.entries[i] : null))
          .filter(Boolean);
        for (const entry of checked) {
          await createDownloadJob(entry.url, baseExtra);
        }
      } else if (previewMatches && preview.type === "video") {
        const formatId = document.getElementById("format-select")?.value || undefined;
        await createDownloadJob(currentUrl, { ...baseExtra, format_id: formatId });
      } else {
        const urls = urlsField.value.split("\n").map((u) => u.trim()).filter(Boolean);
        if (!urls.length) return;
        for (const url of urls) {
          await createDownloadJob(url, baseExtra);
        }
      }

      urlsField.value = "";
      clearPreview();
    });
  }

  function initItemPage() {
    const btn = document.getElementById("delete-btn");
    if (!btn) return;
    btn.addEventListener("click", async () => {
      if (!confirm("¿Eliminar este elemento de la biblioteca?")) return;
      const alsoFile = confirm("¿Eliminar también el archivo del disco? Esto no se puede deshacer.");
      const id = btn.dataset.id;
      const res = await fetch(`/api/items/${id}?delete_file=${alsoFile}`, { method: "DELETE" });
      if (res.ok) {
        window.location.href = "/";
      } else {
        alert("No se pudo eliminar el elemento.");
      }
    });
  }

  function initSettingsPage() {
    const btn = document.getElementById("rescan-btn");
    const msg = document.getElementById("rescan-msg");
    if (!btn) return;
    btn.addEventListener("click", async () => {
      btn.disabled = true;
      if (msg) msg.textContent = "Reindexando…";
      try {
        const res = await fetch("/api/rescan", { method: "POST" });
        const data = await res.json();
        if (!res.ok) throw new Error(data.detail || "Error al reindexar");
        const resetMsg = "La biblioteca se sincroniza sola al arrancar el servidor; este botón es para hacerlo al momento.";
        if (msg) {
          msg.textContent = data.added
            ? `${data.added} archivo(s) añadido(s). Recarga la Biblioteca para verlos.`
            : `Todo al día. ${resetMsg}`;
        }
      } catch (err) {
        if (msg) msg.textContent = String((err && err.message) || err);
      } finally {
        btn.disabled = false;
      }
    });
  }

  return { initLibraryPage, initDownloadPage, initItemPage, initSettingsPage };
})();
