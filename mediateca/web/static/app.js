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

  function initDownloadPage() {
    const form = document.getElementById("download-form");
    const jobsEl = document.getElementById("jobs");
    const emptyMsg = document.getElementById("jobs-empty");
    const pollers = {};

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
      // solo manda un evento cuando algo cambió de verdad.
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

    form.addEventListener("submit", async (ev) => {
      ev.preventDefault();
      const urls = document
        .getElementById("urls")
        .value.split("\n")
        .map((u) => u.trim())
        .filter(Boolean);
      if (!urls.length) return;

      const quality = document.getElementById("quality").value;
      const audioOnly = document.getElementById("audio-only").checked;

      for (const url of urls) {
        try {
          const res = await fetch("/api/download", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ url, audio_only: audioOnly, quality }),
          });
          if (!res.ok) throw new Error("No se pudo iniciar la descarga");
          const { job_id } = await res.json();
          upsertJobRow({ job_id, url, state: "en_cola", progress: 0, message: "En cola…", item_id: null });
          pollJob(job_id);
        } catch (err) {
          const tempId = `tmp-${Date.now()}-${Math.random().toString(36).slice(2)}`;
          upsertJobRow({ job_id: tempId, url, state: "error", progress: 0, message: String(err), item_id: null });
        }
      }

      document.getElementById("urls").value = "";
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

  return { initLibraryPage, initDownloadPage, initItemPage };
})();
