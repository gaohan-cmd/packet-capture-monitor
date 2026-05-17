const state = {
  captures: [],
  selectedId: null,
  selected: null,
  config: {
    target_url: "https://ikuuu.win",
    target_scheme: "https",
    target_host: "ikuuu.win",
    include_subdomains: true,
    sensitive_headers: ["authorization", "cookie", "set-cookie", "proxy-authorization"],
  },
  filters: {
    q: "",
    method: "",
    status: "",
  },
  activeBody: "response",
  showSensitive: false,
};

const $ = (selector) => document.querySelector(selector);
const requestList = $("#requestList");
const connectionState = $("#connectionState");
const targetUrlInput = $("#targetUrlInput");
const targetFormStatus = $("#targetFormStatus");

const formatBytes = (bytes = 0) => {
  if (!bytes) return "0 B";
  const units = ["B", "KB", "MB", "GB"];
  const index = Math.min(Math.floor(Math.log(bytes) / Math.log(1024)), units.length - 1);
  const value = bytes / 1024 ** index;
  return `${value.toFixed(value >= 10 || index === 0 ? 0 : 1)} ${units[index]}`;
};

const statusClass = (status, item) => {
  if (item?.state === "error") return "error";
  if (!status) return "pending";
  if (status >= 200 && status < 300) return "ok";
  if (status >= 300 && status < 400) return "redirect";
  if (status >= 400 && status < 500) return "warn";
  if (status >= 500) return "bad";
  return "pending";
};

const escapeHtml = (value) =>
  String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");

const maskHeaderValue = (name, value) => {
  const lowered = String(name).toLowerCase();
  if (state.showSensitive || !state.config.sensitive_headers.includes(lowered)) {
    return value;
  }
  if (!value) return "";
  return "••••••••";
};

const updateConnection = (mode) => {
  connectionState.classList.remove("live", "offline");
  if (mode === "live") {
    connectionState.classList.add("live");
    connectionState.querySelector("span:last-child").textContent = "实时连接";
  } else if (mode === "offline") {
    connectionState.classList.add("offline");
    connectionState.querySelector("span:last-child").textContent = "连接断开";
  } else {
    connectionState.querySelector("span:last-child").textContent = "连接中";
  }
};

const updateStats = (stats = {}) => {
  $("#metricTotal").textContent = stats.total ?? 0;
  $("#metricOk").textContent = stats.ok ?? 0;
  $("#metricFailed").textContent = stats.failed ?? 0;
  $("#metricLatency").textContent = `${stats.avg_duration_ms ?? 0} ms`;
  $("#metricBytes").textContent = formatBytes(stats.total_response_bytes ?? 0);
};

const currentTargetUrl = () => state.config.target_url || `${state.config.target_scheme || "https"}://${state.config.target_host}`;

const upsertCapture = (item) => {
  const index = state.captures.findIndex((capture) => capture.id === item.id);
  if (index >= 0) {
    state.captures[index] = item;
  } else {
    state.captures.unshift(item);
  }
  state.captures.sort((a, b) => String(b.updated_at).localeCompare(String(a.updated_at)));
};

const renderRequestList = () => {
  if (!state.captures.length) {
    requestList.innerHTML = `
      <div class="request-empty">
        <div class="request-empty-visual" aria-hidden="true">
          <span></span>
          <span></span>
          <span></span>
        </div>
        <h3>暂无请求</h3>
        <p>正在等待 <strong>${escapeHtml(currentTargetUrl())}</strong> 的流量</p>
      </div>
    `;
    return;
  }

  requestList.innerHTML = state.captures
    .map((item) => {
      const request = item.request || {};
      const response = item.response || {};
      const status = response.status_code || item.status_code;
      const body = response.body || {};
      const path = request.path || item.path || "/";
      const contentType = body.content_type || item.content_type || "";
      const active = item.id === state.selectedId ? " active" : "";
      return `
        <button class="request-row${active}" data-id="${escapeHtml(item.id)}">
          <span class="method-pill">${escapeHtml(request.method || item.method || "-")}</span>
          <span class="status-badge ${statusClass(status, item)}">${escapeHtml(status || item.state || "Pending")}</span>
          <span class="path-cell">
            <strong>${escapeHtml(path)}</strong>
            <span>${escapeHtml(contentType || request.host || item.host || "")}</span>
          </span>
          <span class="muted-cell">${escapeHtml(response.duration_ms ?? item.duration_ms ?? "-")} ms</span>
          <span class="muted-cell">${formatBytes(body.size ?? item.response_size ?? 0)}</span>
        </button>
      `;
    })
    .join("");

  requestList.querySelectorAll(".request-row").forEach((row) => {
    row.addEventListener("click", () => selectCapture(row.dataset.id));
  });
};

const loadCaptures = async () => {
  const params = new URLSearchParams();
  if (state.filters.q) params.set("q", state.filters.q);
  if (state.filters.method) params.set("method", state.filters.method);
  if (state.filters.status) params.set("status", state.filters.status);
  params.set("limit", "300");

  const response = await fetch(`/api/captures?${params}`);
  const data = await response.json();
  state.captures = data.items || [];
  updateStats(data.stats);
  renderRequestList();

  if (state.selectedId && state.captures.some((item) => item.id === state.selectedId)) {
    await selectCapture(state.selectedId, { keepScroll: true });
  } else if (state.captures[0]) {
    await selectCapture(state.captures[0].id, { keepScroll: true });
  } else {
    showEmptyState();
  }
};

const showEmptyState = () => {
  state.selected = null;
  state.selectedId = null;
  $("#emptyState").classList.remove("hidden");
  $("#detailContent").classList.add("hidden");
};

const selectCapture = async (id) => {
  state.selectedId = id;
  renderRequestList();
  const response = await fetch(`/api/captures/${encodeURIComponent(id)}`);
  if (!response.ok) {
    showEmptyState();
    return;
  }
  state.selected = await response.json();
  renderDetail();
};

const renderDetail = () => {
  const item = state.selected;
  if (!item) {
    showEmptyState();
    return;
  }
  $("#emptyState").classList.add("hidden");
  $("#detailContent").classList.remove("hidden");

  const request = item.request || {};
  const response = item.response || {};
  const status = response.status_code || item.status_code;

  $("#detailMethod").textContent = request.method || item.method || "-";
  $("#detailPath").textContent = request.path || item.path || "/";
  $("#detailUrl").textContent = request.url || item.url || "";
  $("#detailStatus").textContent = status || item.state || "Pending";
  $("#detailStatus").className = `status-badge ${statusClass(status, item)}`;
  $("#detailDuration").textContent = `${response.duration_ms ?? item.duration_ms ?? "-"} ms`;

  renderOverview(item);
  renderHeaders("#requestHeaders", request.headers || []);
  renderHeaders("#responseHeaders", response.headers || []);
  renderBody("#requestBodyPreview", request.body || {});
  renderBody("#responseBodyPreview", response.body || {});
  renderBody("#bodyInspector", (state.activeBody === "request" ? request.body : response.body) || {});
};

const renderOverview = (item) => {
  const request = item.request || {};
  const response = item.response || {};
  const values = [
    ["URL", request.url || item.url || ""],
    ["Host", request.host || item.host || ""],
    ["HTTP", request.http_version || "-"],
    ["状态", response.status_code ? `${response.status_code} ${response.reason || ""}` : item.state || "pending"],
    ["开始时间", item.started_at || item.created_at || "-"],
    ["完成时间", item.completed_at || "-"],
    ["Content-Type", response.body?.content_type || item.content_type || "-"],
    ["响应大小", formatBytes(response.body?.size || item.response_size || 0)],
  ];

  $("#overviewGrid").innerHTML = values
    .map(
      ([key, value]) => `
      <div class="kv-item">
        <span>${escapeHtml(key)}</span>
        <strong>${escapeHtml(value)}</strong>
      </div>
    `,
    )
    .join("");
};

const renderHeaders = (selector, headers) => {
  const target = $(selector);
  if (!headers.length) {
    target.innerHTML = `<div class="body-empty">暂无头信息</div>`;
    return;
  }
  target.innerHTML = headers
    .map(
      (header) => `
      <div class="header-row">
        <div class="header-name">${escapeHtml(header.name)}</div>
        <div class="header-value">${escapeHtml(maskHeaderValue(header.name, header.value))}</div>
      </div>
    `,
    )
    .join("");
};

const syntaxHighlightJson = (jsonText) => {
  let parsed;
  try {
    parsed = JSON.parse(jsonText);
  } catch {
    return escapeHtml(jsonText);
  }
  return escapeHtml(JSON.stringify(parsed, null, 2)).replace(
    /("(?:\\u[\da-fA-F]{4}|\\[^u]|[^\\"])*"(\s*:)?|\b(true|false|null)\b|-?\d+(?:\.\d*)?(?:[eE][+-]?\d+)?)/g,
    (match) => {
      let cls = "json-number";
      if (match.startsWith('"')) {
        cls = match.endsWith(":") ? "json-key" : "json-string";
      } else if (match === "true" || match === "false") {
        cls = "json-boolean";
      } else if (match === "null") {
        cls = "json-null";
      }
      return `<span class="${cls}">${match}</span>`;
    },
  );
};

const renderBody = (selector, body) => {
  const target = $(selector);
  if (!body || body.kind === "empty" || !body.size) {
    target.innerHTML = `<div class="body-empty">无数据</div>`;
    return;
  }

  const meta = `
    <div class="body-meta">
      <span>${escapeHtml(body.kind || "binary")}</span>
      <span>${escapeHtml(body.content_type || "unknown")}</span>
      <span>${formatBytes(body.size || 0)}</span>
      ${body.truncated ? "<span>已截断</span>" : ""}
    </div>
  `;

  if (body.kind === "image" && body.base64) {
    target.innerHTML = `
      ${meta}
      <div class="image-preview">
        <img alt="response preview" src="data:${escapeHtml(body.content_type)};base64,${body.base64}" />
      </div>
    `;
    return;
  }

  if ((body.kind === "json" || body.kind === "text") && body.text !== undefined) {
    const code = body.kind === "json" ? syntaxHighlightJson(body.text) : escapeHtml(body.text);
    target.innerHTML = `${meta}<pre>${code}</pre>`;
    return;
  }

  if (body.hex) {
    target.innerHTML = `${meta}<pre>${escapeHtml(body.hex)}</pre>`;
    return;
  }

  target.innerHTML = `${meta}<pre>${escapeHtml(body.base64 || "二进制数据")}</pre>`;
};

const connectWebSocket = () => {
  updateConnection("connecting");
  const protocol = location.protocol === "https:" ? "wss" : "ws";
  const socket = new WebSocket(`${protocol}://${location.host}/ws/captures`);

  socket.addEventListener("open", () => updateConnection("live"));
  socket.addEventListener("close", () => {
    updateConnection("offline");
    setTimeout(connectWebSocket, 1200);
  });
  socket.addEventListener("message", async (event) => {
    const message = JSON.parse(event.data);
    if (message.type === "snapshot") {
      state.captures = message.items || [];
      updateStats(message.stats);
      renderRequestList();
      if (!state.selectedId && state.captures[0]) {
        await selectCapture(state.captures[0].id);
      }
      return;
    }
    if (message.type === "config") {
      applyConfig(message.config);
      return;
    }
    if (message.type === "clear") {
      state.captures = [];
      updateStats(message.stats);
      renderRequestList();
      showEmptyState();
      return;
    }
    if (message.type === "capture") {
      upsertCapture(message.item);
      updateStats(message.stats);
      renderRequestList();
      if (!state.selectedId) {
        await selectCapture(message.item.id);
      } else if (state.selectedId === message.item.id) {
        await selectCapture(message.item.id);
      }
    }
  });
};

const applyConfig = (config) => {
  state.config = { ...state.config, ...config };
  const targetUrl = currentTargetUrl();
  const user = state.config.user || {};
  $("#targetLabel").textContent = targetUrl;
  targetUrlInput.value = targetUrl;
  $("#userBadge").textContent = user.username ? `用户 ${user.username}` : "";
  $("#emptyDetailText").textContent = user.proxy_username
    ? `代理账号 ${user.proxy_username} · 目标 ${targetUrl}`
    : `目标 ${targetUrl}`;
  renderRequestList();
};

const debounce = (fn, delay = 180) => {
  let handle;
  return (...args) => {
    clearTimeout(handle);
    handle = setTimeout(() => fn(...args), delay);
  };
};

const bindEvents = () => {
  $("#targetForm").addEventListener("submit", async (event) => {
    event.preventDefault();
    const targetUrl = targetUrlInput.value.trim();
    targetFormStatus.textContent = "";
    targetFormStatus.className = "";
    try {
      const response = await fetch("/api/config", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          target_url: targetUrl,
          include_subdomains: state.config.include_subdomains,
        }),
      });
      if (!response.ok) {
        const payload = await response.json().catch(() => ({}));
        throw new Error(payload.detail || "目标地址无效");
      }
      applyConfig(await response.json());
      targetFormStatus.textContent = "已应用";
      targetFormStatus.className = "ok";
      setTimeout(() => {
        targetFormStatus.textContent = "";
        targetFormStatus.className = "";
      }, 1600);
    } catch (error) {
      targetFormStatus.textContent = error.message || "保存失败";
      targetFormStatus.className = "error";
    }
  });

  $("#refreshButton").addEventListener("click", loadCaptures);
  $("#logoutButton").addEventListener("click", async () => {
    await fetch("/api/logout", { method: "POST" });
    location.href = "/login";
  });
  $("#clearButton").addEventListener("click", async () => {
    if (!confirm("清空当前采集记录？")) return;
    await fetch("/api/captures", { method: "DELETE" });
  });

  $("#searchInput").addEventListener(
    "input",
    debounce((event) => {
      state.filters.q = event.target.value;
      loadCaptures();
    }),
  );
  $("#methodFilter").addEventListener("change", (event) => {
    state.filters.method = event.target.value;
    loadCaptures();
  });
  $("#statusFilter").addEventListener("change", (event) => {
    state.filters.status = event.target.value;
    loadCaptures();
  });

  document.querySelectorAll(".tabs .tab[data-tab]").forEach((button) => {
    button.addEventListener("click", () => {
      document.querySelectorAll(".tabs .tab[data-tab]").forEach((tab) => tab.classList.remove("active"));
      document.querySelectorAll(".tab-page").forEach((page) => page.classList.remove("active"));
      button.classList.add("active");
      $(`#tab-${button.dataset.tab}`).classList.add("active");
    });
  });

  document.querySelectorAll(".body-toolbar .tab[data-body]").forEach((button) => {
    button.addEventListener("click", () => {
      document.querySelectorAll(".body-toolbar .tab[data-body]").forEach((tab) => tab.classList.remove("active"));
      button.classList.add("active");
      state.activeBody = button.dataset.body;
      renderDetail();
    });
  });

  $("#showSensitiveToggle").addEventListener("change", (event) => {
    state.showSensitive = event.target.checked;
    renderDetail();
  });
};

const loadConfig = async () => {
  const response = await fetch("/api/config");
  applyConfig(await response.json());
};

const boot = async () => {
  bindEvents();
  await loadConfig();
  await loadCaptures();
  connectWebSocket();
};

boot().catch((error) => {
  updateConnection("offline");
  console.error(error);
});
