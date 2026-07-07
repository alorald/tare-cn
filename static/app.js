/* ============================================================
   FinFlow AaaS Console — 前端逻辑
   SPA 路由 / API 封装 / 票据上传 / WebSocket 进度 / 渲染
   ============================================================ */
(function () {
  "use strict";

  /* ---------- 配置 ---------- */
  const CONFIG = {
    API_BASE: "http://localhost:8080",
    WS_BASE: "ws://localhost:8080",
    TOAST_TIMEOUT: 4200,
  };

  /* 工作流步骤定义 */
  const WORKFLOW_STEPS = [
    { key: "receive", name: "任务接收", desc: "Qwen3 Coder 解析指令并分解任务" },
    { key: "parse", name: "票据解析", desc: "GMI VLM 完成 OCR 与字段提取" },
    { key: "compliance", name: "合规推理", desc: "GPT OSS 120B 匹配税法并检测风险" },
    { key: "advice", name: "建议生成", desc: "GLM-4.5 生成的多语种合规建议" },
    { key: "report", name: "报表输出", desc: "DeepSeek V3.1 校验并聚合财报" },
  ];

  /* Agent 定义 */
  const AGENTS_META = [
    {
      id: "receipt",
      name: "票据多模态解析 Agent",
      role: "Receipt Multimodal Parser",
      icon: "R",
      cls: "a1",
      desc: "上传票据图片后，调用 GMI Cloud VLM 完成 OCR、版面分析与结构化字段提取，输出带置信度的票据数据。",
      models: ["gmi-cloud-vlm", "deepseek-v3.1"],
      color: "#3b82f6",
    },
    {
      id: "compliance",
      name: "多国财税合规决策 Agent",
      role: "Compliance Decision Agent",
      icon: "C",
      cls: "a2",
      desc: "基于提取数据调用 GPT OSS 120B 完成多国税法匹配、税率计算与税务风险检测，给出合规建议。",
      models: ["gpt-oss-120b", "glm-4.5"],
      color: "#8b5cf6",
    },
    {
      id: "orchestrator",
      name: "业财自动化调度 Agent",
      role: "Orchestration Agent",
      icon: "O",
      cls: "a3",
      desc: "负责任务分解、子任务分发与结果聚合，编排从票据解析到报表输出的全链路工作流。",
      models: ["qwen3-coder"],
      color: "#10b981",
    },
  ];

  /* 平台 & 国家选项 */
  const PLATFORMS = [
    { id: "amazon", name: "Amazon", logo: "A", color: "#ff9900" },
    { id: "ebay", name: "eBay", logo: "E", color: "#e53238" },
    { id: "shopify", name: "Shopify", logo: "S", color: "#96bf48" },
    { id: "shopee", name: "Shopee", logo: "P", color: "#ee4d2d" },
    { id: "stripe", name: "Stripe", logo: "T", color: "#635bff" },
  ];
  const COUNTRIES = [
    "美国(US)", "英国(GB)", "德国(DE)", "法国(FR)", "日本(JP)",
    "新加坡(SG)", "澳大利亚(AU)", "加拿大(CA)", "意大利(IT)", "西班牙(ES)",
  ];

  /* ---------- 应用状态 ---------- */
  const state = {
    route: "dashboard",
    dashboard: null,
    gmiStatus: null,
    gmiModels: [],
    tasks: [],
    ws: null,
    currentTask: null,
    receiptImage: null,       // { base64, name, size }
    receiptText: "",
    selectedPlatform: "amazon",
    selectedCountry: "美国(US)",
    parseResult: null,
    taskSteps: {},            // { stepKey: { status, duration, message, data } }
    taskStartTime: null,
    agentTasks: null,         // Agent 任务分布
    wsReconnect: null,
  };

  /* ---------- DOM 引用 ---------- */
  const $ = (sel, root) => (root || document).querySelector(sel);
  const $$ = (sel, root) => Array.from((root || document).querySelectorAll(sel));
  const app = $("#app");

  /* ============================================================
     工具函数
     ============================================================ */
  function esc(str) {
    if (str === null || str === undefined) return "";
    return String(str)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
  }

  function fmtMoney(n) {
    const v = Number(n || 0);
    return "$" + v.toFixed(2);
  }
  function fmtNum(n) {
    return Number(n || 0).toLocaleString("en-US");
  }
  function fmtTime(ts) {
    if (!ts) return "-";
    let d;
    if (typeof ts === "number") {
      d = ts > 1e12 ? new Date(ts) : new Date(ts * 1000);
    } else {
      d = new Date(ts);
    }
    if (isNaN(d.getTime())) return String(ts);
    const pad = (x) => String(x).padStart(2, "0");
    return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
  }
  function fmtDuration(ms) {
    if (!ms || ms < 0) return "-";
    if (ms < 1000) return ms + "ms";
    return (ms / 1000).toFixed(1) + "s";
  }
  function fmtSize(bytes) {
    if (bytes < 1024) return bytes + " B";
    if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + " KB";
    return (bytes / 1024 / 1024).toFixed(2) + " MB";
  }

  function statusBadge(status) {
    const s = String(status || "").toLowerCase();
    const map = {
      completed: "green", done: "green", success: "green", online: "green", active: "green",
      processing: "blue", running: "blue", in_progress: "blue", pending: "amber", queued: "amber",
      scaling: "purple", waiting: "amber",
      failed: "red", error: "red", offline: "red", stopped: "gray", idle: "gray",
    };
    const cls = map[s] || "gray";
    return `<span class="badge ${cls}">${esc(status || "未知")}</span>`;
  }

  /* 语法高亮 JSON */
  function highlightJSON(obj) {
    let json;
    try { json = JSON.stringify(obj, null, 2); } catch { json = String(obj); }
    json = esc(json);
    return json
      .replace(/(&quot;[^&]*?&quot;)(\s*:)/g, '<span class="json-key">$1</span>$2')
      .replace(/:\s*(&quot;[^&]*?&quot;)/g, (m, p1) => ': <span class="json-str">' + p1 + "</span>")
      .replace(/:\s*(true|false)/g, ': <span class="json-bool">$1</span>')
      .replace(/:\s*(null)/g, ': <span class="json-null">$1</span>')
      .replace(/:\s*(-?\d+\.?\d*)/g, ': <span class="json-num">$1</span>');
  }

  /* 语法高亮 curl */
  function highlightShell(code) {
    let html = esc(code);
    html = html.replace(/(curl)/g, '<span class="c-fn">$1</span>');
    html = html.replace(/(-X|-H|-d|--data-raw)(\s+)/g, '<span class="c-key">$1</span>$2');
    html = html.replace(/(GET|POST|PUT|DELETE)(\s+)/g, '<span class="c-num">$1</span>$2');
    html = html.replace(/(https?:\/\/[^\s'\"]+)/g, '<span class="c-str">$1</span>');
    html = html.replace(/('[^']*')/g, '<span class="c-str">$1</span>');
    return html;
  }

  /* ============================================================
     API 封装
     ============================================================ */
  async function api(path, options) {
    options = options || {};
    const opts = Object.assign(
      {
        headers: { "Content-Type": "application/json" },
        timeout: 25000,
      },
      options
    );
    if (opts.body && typeof opts.body !== "string") {
      opts.body = JSON.stringify(opts.body);
    }
    const ctrl = new AbortController();
    opts.signal = ctrl.signal;
    const timer = setTimeout(() => ctrl.abort(), opts.timeout);

    try {
      const res = await fetch(CONFIG.API_BASE + path, opts);
      clearTimeout(timer);
      const ct = res.headers.get("content-type") || "";
      let data;
      if (ct.includes("application/json")) {
        data = await res.json();
      } else {
        data = await res.text();
      }
      if (!res.ok) {
        const msg = (data && (data.detail || data.message)) || ("HTTP " + res.status);
        const err = new Error(msg);
        err.status = res.status;
        err.data = data;
        throw err;
      }
      return data;
    } catch (e) {
      clearTimeout(timer);
      if (e.name === "AbortError") {
        const err = new Error("请求超时");
        err.isTimeout = true;
        throw err;
      }
      throw e;
    }
  }

  const API = {
    dashboard: () => api("/api/dashboard"),
    tasks: () => api("/api/tasks"),
    task: (id) => api("/api/tasks/" + encodeURIComponent(id)),
    createTask: (body) => api("/api/tasks", { method: "POST", body }),
    parseReceipt: (body) => api("/api/receipts/parse", { method: "POST", body }),
    report: (taskId) => api("/api/reports/" + encodeURIComponent(taskId)),
    docs: () => api("/api/docs"),
    gmiModels: () => api("/api/gmi/models"),
    gmiStatus: () => api("/api/gmi/status"),
  };

  /* ============================================================
     Toast 通知
     ============================================================ */
  const toastBox = $("#toast-container");
  function toast(type, title, msg) {
    const icons = { success: "✓", error: "!", warn: "!", info: "i" };
    const el = document.createElement("div");
    el.className = "toast " + (type || "info");
    el.innerHTML = `
      <div class="toast-icon">${icons[type] || "i"}</div>
      <div class="toast-body">
        <div class="toast-title">${esc(title || "")}</div>
        ${msg ? `<div class="toast-msg">${esc(msg)}</div>` : ""}
      </div>`;
    toastBox.appendChild(el);
    setTimeout(() => {
      el.classList.add("out");
      setTimeout(() => el.remove(), 300);
    }, CONFIG.TOAST_TIMEOUT);
  }

  /* ============================================================
     模态框管理
     ============================================================ */
  const modalOverlay = $("#modal-overlay");
  const modalTitle = $("#modal-title");
  const modalBody = $("#modal-body");
  const modalFooter = $("#modal-footer");
  const modalDone = $("#modal-done");
  const modalCancel = $("#modal-cancel");

  function openModal(title, bodyHTML) {
    modalTitle.textContent = title || "";
    modalBody.innerHTML = bodyHTML || "";
    modalOverlay.classList.add("show");
    modalDone.style.display = "none";
    modalCancel.style.display = "";
  }
  function closeModal() {
    modalOverlay.classList.remove("show");
    modalBody.innerHTML = "";
  }
  $("#modal-close").addEventListener("click", closeModal);
  modalOverlay.addEventListener("click", (e) => {
    if (e.target === modalOverlay) closeModal();
  });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && modalOverlay.classList.contains("show")) closeModal();
  });
  modalCancel.addEventListener("click", () => {
    if (state.currentTask) {
      toast("warn", "任务已取消", "工作流进度已停止刷新");
    }
    closeModal();
  });
  modalDone.addEventListener("click", () => {
    closeModal();
    if (state.currentTask && state.currentTask.id) {
      location.hash = "#reports";
      setTimeout(() => loadReport(state.currentTask.id), 300);
    }
  });

  /* ============================================================
     连接状态
     ============================================================ */
  function setConnStatus(status, text) {
    const el = $("#conn-status");
    el.classList.remove("online", "offline");
    if (status) el.classList.add(status);
    $(".conn-text", el).textContent = text;
  }

  /* ============================================================
     路由
     ============================================================ */
  const ROUTES = {
    dashboard: { title: "总览仪表盘", render: renderDashboard, onEnter: loadDashboard },
    receipts: { title: "票据中心", render: renderReceipts, onEnter: null },
    agents: { title: "Agent 集群", render: renderAgents, onEnter: loadAgents },
    reports: { title: "财税报表", render: renderReports, onEnter: loadReportsList },
    gmi: { title: "GMI 监控", render: renderGMI, onEnter: loadGMI },
    "api-docs": { title: "API 文档", render: renderApiDocs, onEnter: loadApiDocs },
  };

  function getRoute() {
    const h = (location.hash || "#dashboard").replace(/^#/, "");
    return ROUTES[h] ? h : "dashboard";
  }

  function router() {
    const route = getRoute();
    state.route = route;
    const def = ROUTES[route];

    $("#page-title").textContent = def.title;
    $$(".nav-item").forEach((n) => {
      n.classList.toggle("active", n.dataset.route === route);
    });

    app.innerHTML = `<div class="loading-screen"><div class="loader"></div><p>加载中…</p></div>`;
    try {
      def.render();
    } catch (e) {
      console.error(e);
      app.innerHTML = `<div class="empty-state"><div class="es-title">渲染出错</div><div class="es-desc">${esc(e.message)}</div></div>`;
    }
    if (def.onEnter) {
      Promise.resolve(def.onEnter()).catch((e) => {
        console.error(e);
        toast("error", "数据加载失败", e.message);
      });
    }
    // 移动端关闭侧栏
    document.querySelector(".app-layout").classList.remove("sidebar-open");
  }

  window.addEventListener("hashchange", router);

  /* ============================================================
     页面 1 — 总览仪表盘
     ============================================================ */
  function renderDashboard() {
    app.innerHTML = `
      <div class="page" id="page-dashboard">
        <div class="grid grid-stats mb-20" id="dash-stats">
          ${[0,1,2,3].map(()=>`
            <div class="card stat-card">
              <div class="skeleton sk-block" style="height:42px;width:42px;border-radius:11px;margin-bottom:14px"></div>
              <div class="skeleton sk-line" style="width:60%"></div>
              <div class="skeleton sk-line" style="width:40%;height:22px"></div>
            </div>`).join("")}
        </div>

        <div class="grid grid-2 mb-20">
          <div class="card" id="dash-agents">
            <div class="card-title">Agent 集群状态</div>
            <div class="skeleton sk-line"></div><div class="skeleton sk-line"></div>
            <div class="skeleton sk-line"></div><div class="skeleton sk-line"></div>
          </div>
          <div class="card" id="dash-budget">
            <div class="card-title">Token 预算消耗</div>
            <div class="skeleton sk-block"></div>
          </div>
        </div>

        <div class="card" id="dash-recent">
          <div class="card-title">最近任务</div>
          <div class="skeleton sk-line"></div><div class="skeleton sk-line"></div>
          <div class="skeleton sk-line"></div>
        </div>
      </div>`;
  }

  async function loadDashboard() {
    const data = await API.dashboard();
    state.dashboard = data;
    setConnStatus("online", "已连接后端");

    // 统计卡片
    const ts = data.task_stats || {};
    const tb = data.token_budget || {};
    const budgetUsed = Number(tb.used || 0);
    const budgetTotal = Number(tb.total || 50);
    const budgetPct = tb.percentage != null ? tb.percentage : (budgetTotal ? (budgetUsed / budgetTotal) * 100 : 0);
    updateSidebarBudget(budgetUsed, budgetTotal, budgetPct);

    const stats = [
      { label: "本月任务总数", value: fmtNum(ts.total), icon: "blue", svg: iconChart(), foot: `<span class="stat-trend up">▲ ${ts.completed || 0} 已完成</span> · ${ts.processing || 0} 处理中` },
      { label: "已处理票据", value: fmtNum(ts.completed), icon: "green", svg: iconReceipt(), foot: `失败 ${ts.failed || 0} · 成功率 ${ts.total ? Math.round((ts.completed / ts.total) * 100) : 0}%` },
      { label: "GMI Token 消耗", value: fmtMoney(budgetUsed) + " / " + fmtMoney(budgetTotal), icon: "purple", svg: iconBolt(), foot: `<span class="stat-trend ${budgetPct > 80 ? "down" : "up"}">已用 ${budgetPct.toFixed(1)}%</span>` },
      { label: "平均推理延迟", value: "1.8s", icon: "amber", svg: iconClock(), foot: "P95 3.2s · 赛事额度内" },
    ];
    $("#dash-stats").innerHTML = stats.map((s) => `
      <div class="card stat-card">
        <div class="stat-icon ${s.icon}">${s.svg}</div>
        <div class="stat-label">${esc(s.label)}</div>
        <div class="stat-value">${s.value}</div>
        <div class="stat-foot">${s.foot}</div>
      </div>`).join("");

    // Agent 状态表
    const agents = data.agent_status || [];
    $("#dash-agents").innerHTML = `
      <div class="card-title">Agent 集群状态</div>
      <div class="table-wrap">
        <table class="table">
          <thead><tr><th>Agent</th><th>状态</th><th>待处理</th><th>今日完成</th><th>Token 消耗</th></tr></thead>
          <tbody>
            ${agents.length ? agents.map((a) => `
              <tr>
                <td><b>${esc(a.name)}</b></td>
                <td>${statusBadge(a.status)}</td>
                <td class="text-mono">${fmtNum(a.pending)}</td>
                <td class="text-mono">${fmtNum(a.completed_today)}</td>
                <td class="text-mono">${fmtMoney(a.token_cost)}</td>
              </tr>`).join("") : `<tr><td colspan="5" class="text-dim text-center" style="padding:24px">暂无 Agent 数据</td></tr>`}
          </tbody>
        </table>
      </div>`;

    // 预算卡片
    $("#dash-budget").innerHTML = `
      <div class="card-title">Token 预算消耗</div>
      <div style="display:flex;align-items:flex-end;gap:10px;margin-bottom:10px">
        <div style="font-size:30px;font-weight:700">${fmtMoney(budgetUsed)}</div>
        <div class="text-dim text-sm" style="margin-bottom:6px">/ ${fmtMoney(budgetTotal)}</div>
      </div>
      <div class="progress"><div class="progress-bar ${budgetPct > 80 ? "danger" : budgetPct > 50 ? "warn" : "accent"}" style="width:${Math.min(budgetPct, 100)}%"></div></div>
      <div class="flex justify-between mt-16 text-sm">
        <span class="text-dim">剩余 ${fmtMoney(Number(tb.remaining != null ? tb.remaining : budgetTotal - budgetUsed))}</span>
        <span class="text-mono">${budgetPct.toFixed(1)}%</span>
      </div>
      <div class="metric-row mt-20">
        <span class="metric-name">多模态推理 (VLM)</span>
        <span class="text-mono text-sm">~30%</span>
      </div>
      <div class="metric-row">
        <span class="metric-name">DeepSeek V3.1</span>
        <span class="text-mono text-sm">~25%</span>
      </div>
      <div class="metric-row">
        <span class="metric-name">GPT OSS 120B</span>
        <span class="text-mono text-sm">~25%</span>
      </div>`;

    // 最近任务
    const recent = data.recent_tasks || [];
    $("#dash-recent").innerHTML = `
      <div class="card-title">最近任务</div>
      <div class="table-wrap">
        <table class="table">
          <thead><tr><th>任务 ID</th><th>任务名称</th><th>状态</th><th>创建时间</th></tr></thead>
          <tbody>
            ${recent.length ? recent.map((t) => `
              <tr>
                <td class="mono">${esc(t.id)}</td>
                <td>${esc(t.name)}</td>
                <td>${statusBadge(t.status)}</td>
                <td class="text-mono">${fmtTime(t.created_at)}</td>
              </tr>`).join("") : `<tr><td colspan="4" class="text-dim text-center" style="padding:24px">暂无任务</td></tr>`}
          </tbody>
        </table>
      </div>`;
  }

  /* ============================================================
     页面 2 — 票据中心
     ============================================================ */
  function renderReceipts() {
    app.innerHTML = `
      <div class="page" id="page-receipts">
        <div class="grid grid-2">
          <!-- 左侧：上传 + 配置 -->
          <div>
            <div class="card mb-20">
              <div class="card-title">${iconUpload()} 票据上传</div>
              <div class="upload-zone" id="upload-zone">
                <div class="upload-icon">${iconUpload(28)}</div>
                <div class="upload-title">拖拽票据图片到此处</div>
                <div class="upload-hint">或 <b>点击选择文件</b> · 支持 JPG / PNG / WEBP，&lt; 5MB</div>
                <input type="file" id="file-input" accept="image/*" style="display:none" />
              </div>
              <div class="form-hint mt-16">上传票据图片后，将由 GMI Cloud VLM 完成 OCR 与字段提取；也可跳过图片直接在下方输入票据文本。</div>
            </div>

            <div class="card mb-20">
              <div class="card-title">${iconText()} 票据文本（可选）</div>
              <div class="form-group">
                <label class="form-label">手动输入票据文本 <span class="opt">— 未上传图片时使用</span></label>
                <textarea class="form-control" id="receipt-text" placeholder="例如：Amazon Order #112-7782214-5560010&#10;Buyer: John Smith&#10;Item: Wireless Earbuds x 2&#10;Amount: $89.99&#10;Tax (8.5%): $7.65&#10;Total: $97.64&#10;Ship to: 1234 Market St, San Francisco, CA 94103, USA"></textarea>
              </div>
            </div>

            <div class="card mb-20">
              <div class="card-title">${iconConfig()} 任务配置</div>
              <div class="form-group">
                <label class="form-label">选择平台</label>
                <div class="chip-group" id="platform-chips">
                  ${PLATFORMS.map((p) => `
                    <div class="chip ${p.id === state.selectedPlatform ? "active" : ""}" data-platform="${p.id}">
                      <span class="chip-logo" style="background:${p.color}">${p.logo}</span>
                      ${p.name}
                    </div>`).join("")}
                </div>
              </div>
              <div class="form-group mb-0">
                <label class="form-label">选择目标国家</label>
                <select class="form-control" id="country-select">
                  ${COUNTRIES.map((c) => `<option value="${esc(c)}" ${c === state.selectedCountry ? "selected" : ""}>${esc(c)}</option>`).join("")}
                </select>
              </div>
            </div>

            <button class="btn btn-accent btn-lg btn-block" id="trigger-btn">
              ${iconBolt(18)} 一键触发智能体
            </button>
          </div>

          <!-- 右侧：解析结果 + 进度 -->
          <div>
            <div class="card mb-20" id="parse-card">
              <div class="card-title">${iconDoc()} 解析结果</div>
              <div id="parse-result"><div class="parse-empty">上传票据或输入文本后点击触发，解析结果将展示在此</div></div>
            </div>

            <div class="card">
              <div class="card-title">${iconFlow()} 实时工作流进度</div>
              <div id="workflow-area">
                <div class="parse-empty">触发任务后将显示 Agent 工作流进度</div>
              </div>
            </div>
          </div>
        </div>
      </div>`;

    bindReceiptsEvents();
  }

  function bindReceiptsEvents() {
    const zone = $("#upload-zone");

    // 每次点击重新查询 input，避免 innerHTML 重建后引用到已分离的旧节点
    zone.addEventListener("click", (e) => {
      if (e.target.closest(".file-remove")) return; // 移除按钮不触发选文件
      const input = $("#file-input", zone);
      if (input) input.click();
    });
    const fileInput = $("#file-input");
    if (fileInput) {
      fileInput.addEventListener("change", (e) => {
        if (e.target.files[0]) handleFile(e.target.files[0]);
      });
    }

    ["dragenter", "dragover"].forEach((ev) =>
      zone.addEventListener(ev, (e) => { e.preventDefault(); zone.classList.add("dragover"); })
    );
    ["dragleave", "drop"].forEach((ev) =>
      zone.addEventListener(ev, (e) => { e.preventDefault(); zone.classList.remove("dragover"); })
    );
    zone.addEventListener("drop", (e) => {
      const f = e.dataTransfer.files[0];
      if (f) handleFile(f);
    });

    // 平台选择
    $$("#platform-chips .chip").forEach((chip) => {
      chip.addEventListener("click", () => {
        $$("#platform-chips .chip").forEach((c) => c.classList.remove("active"));
        chip.classList.add("active");
        state.selectedPlatform = chip.dataset.platform;
      });
    });

    $("#country-select").addEventListener("change", (e) => {
      state.selectedCountry = e.target.value;
    });

    $("#receipt-text").addEventListener("input", (e) => {
      state.receiptText = e.target.value;
    });

    $("#trigger-btn").addEventListener("click", triggerTask);
  }

  function handleFile(file) {
    if (!file.type.startsWith("image/")) {
      toast("error", "文件类型不支持", "请上传图片文件 (JPG/PNG/WEBP)");
      return;
    }
    if (file.size > 5 * 1024 * 1024) {
      toast("error", "文件过大", "票据图片需小于 5MB");
      return;
    }
    const reader = new FileReader();
    reader.onload = (e) => {
      // e.target.result 形如 data:image/png;base64,xxxx
      const dataUrl = e.target.result;
      const base64 = dataUrl.split(",")[1];
      state.receiptImage = { base64, dataUrl, name: file.name, size: file.size, type: file.type };
      renderFilePreview();
      toast("success", "图片已加载", file.name);
    };
    reader.onerror = () => toast("error", "读取失败", "无法读取该图片");
    reader.readAsDataURL(file);
  }

  function renderFilePreview() {
    const zone = $("#upload-zone");
    if (!zone) return;
    if (!state.receiptImage) {
      zone.classList.remove("has-file");
      zone.innerHTML = `
        <div class="upload-icon">${iconUpload(28)}</div>
        <div class="upload-title">拖拽票据图片到此处</div>
        <div class="upload-hint">或 <b>点击选择文件</b> · 支持 JPG / PNG / WEBP，&lt; 5MB</div>
        <input type="file" id="file-input" accept="image/*" style="display:none" />`;
      $("#file-input").addEventListener("change", (e) => { if (e.target.files[0]) handleFile(e.target.files[0]); });
      return;
    }
    const img = state.receiptImage;
    zone.classList.add("has-file");
    zone.innerHTML = `
      <div class="file-preview">
        <img src="${img.dataUrl}" alt="票据预览" />
        <div class="file-info">
          <div class="file-name">${esc(img.name)}</div>
          <div class="file-size">${fmtSize(img.size)} · ${esc(img.type)}</div>
        </div>
        <button class="file-remove" id="file-remove" title="移除">${iconClose(16)}</button>
      </div>`;
    $("#file-remove").addEventListener("click", (e) => {
      e.stopPropagation();
      state.receiptImage = null;
      renderFilePreview();
    });
  }

  async function triggerTask() {
    const btn = $("#trigger-btn");
    const hasImage = !!state.receiptImage;
    const text = (state.receiptText || "").trim();

    if (!hasImage && !text) {
      toast("warn", "缺少输入", "请上传票据图片或输入票据文本");
      return;
    }

    btn.disabled = true;
    btn.innerHTML = `<span class="spin">${iconBolt(18)}</span> 正在创建任务…`;

    // 重置进度
    state.taskSteps = {};
    state.taskStartTime = Date.now();
    WORKFLOW_STEPS.forEach((s) => { state.taskSteps[s.key] = { status: "pending" }; });
    renderWorkflowArea();

    const payload = {
      task_type: "receipt_compliance",
      platform: state.selectedPlatform,
      target_country: state.selectedCountry,
      receipt_text: text || null,
    };
    if (hasImage) payload.receipt_image_base64 = state.receiptImage.base64;

    try {
      const res = await API.createTask(payload);
      state.currentTask = { id: res.task_id, status: res.status };
      toast("success", "任务已创建", "任务 ID: " + res.task_id);

      // 打开进度模态框
      openProgressModal(res.task_id);
      // 第一步立即接收
      updateStep("receive", "processing", "任务已接收，开始分解…");

      connectWS(res.task_id);
    } catch (e) {
      toast("error", "任务创建失败", e.message);
      // 演示模式：后端未就绪时本地模拟
      if (isConnectionError(e)) {
        toast("warn", "后端未连接", "将启动本地演示流程");
        demoWorkflow();
      }
    } finally {
      btn.disabled = false;
      btn.innerHTML = `${iconBolt(18)} 一键触发智能体`;
    }
  }

  function isConnectionError(e) {
    return e && (e.isTimeout || /Failed to fetch|NetworkError|load failed/i.test(e.message));
  }

  /* ---------- WebSocket 连接 ---------- */
  function connectWS(taskId) {
    try {
      if (state.ws) { state.ws.close(); state.ws = null; }
      const url = CONFIG.WS_BASE + "/ws?task_id=" + encodeURIComponent(taskId);
      const ws = new WebSocket(url);
      state.ws = ws;

      ws.onopen = () => {
        setConnStatus("online", "WebSocket 已连接");
      };
      ws.onmessage = (ev) => {
        let msg;
        try { msg = JSON.parse(ev.data); } catch { return; }
        handleWSMessage(msg);
      };
      ws.onerror = () => {
        setConnStatus("offline", "WebSocket 异常");
      };
      ws.onclose = () => {
        if (state.currentTask && state.currentTask.id === taskId) {
          // 尝试用轮询兜底
          pollTask(taskId);
        }
      };
    } catch (e) {
      console.error("WS error", e);
    }
  }

  async function handleWSMessage(msg) {
    if (!msg) return;

    // 后端进度事件结构：{ task_id, step, status, message, data, timestamp }
    // 后端没有 type 字段，用 step 字段识别进度消息
    const hasProgress = msg.type === "progress" || (msg.step && msg.status);

    if (hasProgress) {
      // 后端 step 名称 -> 前端 WORKFLOW_STEPS key 映射
      const stepMap = {
        // 后端实际发送的 step
        task_start: "receive",
        orchestration: "receive",
        receipt_vision: "parse",
        receipt_verify: "parse",
        compliance_decision: "compliance",
        compliance_advice: "advice",
        report_generation: "report",
        // 兼容旧映射
        receive: "receive", parse: "parse", receipt: "parse",
        compliance: "compliance", reasoning: "compliance",
        advice: "advice", suggestion: "advice",
        report: "report", output: "report",
      };

      // task_complete 完成所有步骤
      if (msg.step === "task_complete") {
        finishAllSteps();
        toast("success", "任务完成", msg.message || "智能体工作流已全部完成");
        modalDone.style.display = "";
        modalCancel.style.display = "none";
        // 拉取完整任务结果（含报表）
        if (state.currentTask && state.currentTask.id) {
          try {
            const t = await API.task(state.currentTask.id);
            if (t && t.result) {
              state.parseResult = t.result;
              renderParseResult();
            }
          } catch (e) { /* 忽略 */ }
        }
        return;
      }

      // task_error 任务失败
      if (msg.step === "task_error") {
        toast("error", "任务失败", msg.message || "");
        updateStep("receive", "failed", msg.message);
        return;
      }

      const stepKey = stepMap[msg.step] || msg.step;
      const status = msg.status === "done" ? "done" : msg.status === "error" ? "failed" : "processing";
      if (stepKey && state.taskSteps[stepKey]) {
        updateStep(stepKey, status, msg.message, msg.data);
      } else if (msg.step) {
        // 未知步骤，尝试更新
        updateStep(stepKey || msg.step, status, msg.message, msg.data);
      }
    } else if (msg.type === "parse_result") {
      state.parseResult = msg.data;
      renderParseResult();
    } else if (msg.type === "completed" || msg.type === "done") {
      finishAllSteps();
      if (msg.data) {
        state.parseResult = msg.data;
        renderParseResult();
      }
      toast("success", "任务完成", "智能体工作流已全部完成");
      modalDone.style.display = "";
      modalCancel.style.display = "none";
    } else if (msg.type === "error" || msg.type === "failed") {
      toast("error", "任务失败", msg.message || "");
      updateStep(msg.step || "receive", "failed", msg.message);
    } else if (msg.type === "subscribed") {
      // 订阅确认，忽略
    }
  }

  /* 轮询兜底（WS 断开时） */
  async function pollTask(taskId) {
    let tries = 0;
    while (state.currentTask && state.currentTask.id === taskId && tries < 60) {
      tries++;
      try {
        const t = await API.task(taskId);
        if (t && t.status && t.progress) {
          // 根据 task.status 同步步骤
          syncStepsFromTask(t);
        }
        if (t && (t.status === "completed" || t.status === "completed_with_errors" || t.status === "done")) {
          finishAllSteps();
          if (t.result && t.result.report) {
            state.parseResult = t.result;
            renderParseResult();
          }
          toast("success", "任务完成", t.status === "completed_with_errors" ? "任务完成（部分步骤有警告）" : "智能体工作流已全部完成");
          modalDone.style.display = "";
          modalCancel.style.display = "none";
          break;
        }
        if (t && (t.status === "failed" || t.status === "error")) break;
      } catch (e) { /* 忽略 */ }
      await sleep(1500);
    }
  }

  function syncStepsFromTask(task) {
    if (!task.progress) return;
    const prog = task.progress;
    // REST API 返回 progress 是事件列表 [{step, status, message, data, ...}]
    const stepMap = {
      task_start: "receive",
      orchestration: "receive",
      receipt_vision: "parse",
      receipt_verify: "parse",
      compliance_decision: "compliance",
      compliance_advice: "advice",
      report_generation: "report",
    };
    if (Array.isArray(prog)) {
      prog.forEach((evt) => {
        if (!evt || !evt.step) return;
        if (evt.step === "task_complete") {
          finishAllSteps();
          return;
        }
        if (evt.step === "task_error") {
          updateStep("receive", "failed", evt.message);
          return;
        }
        const stepKey = stepMap[evt.step] || evt.step;
        const status = evt.status === "done" ? "done" : evt.status === "error" ? "failed" : "processing";
        if (stepKey) updateStep(stepKey, status, evt.message, evt.data);
      });
    }
    if (task.parse_result) { state.parseResult = task.parse_result; renderParseResult(); }
  }

  /* ---------- 步骤更新 ---------- */
  function updateStep(stepKey, status, message, data) {
    if (!state.taskSteps[stepKey]) {
      state.taskSteps[stepKey] = { status, message, data };
    } else {
      const prev = state.taskSteps[stepKey];
      const now = Date.now();
      let duration = prev.duration;
      if (prev.status === "processing" && (status === "done" || status === "completed")) {
        if (prev.startedAt) duration = now - prev.startedAt;
      }
      if (status === "processing" && !prev.startedAt) {
        state.taskSteps[stepKey].startedAt = now;
      }
      state.taskSteps[stepKey] = {
        status,
        message,
        data,
        duration,
        startedAt: prev.startedAt || (status === "processing" ? now : null),
      };
    }
    // 上一步完成则激活下一步
    const idx = WORKFLOW_STEPS.findIndex((s) => s.key === stepKey);
    if ((status === "done" || status === "completed") && idx >= 0 && idx < WORKFLOW_STEPS.length - 1) {
      const next = WORKFLOW_STEPS[idx + 1];
      if (state.taskSteps[next.key].status === "pending") {
        state.taskSteps[next.key].status = "processing";
        state.taskSteps[next.key].startedAt = Date.now();
      }
    }
    renderWorkflowArea();
    renderModalSteps();
    if (data && stepKey === "parse") {
      state.parseResult = data;
      renderParseResult();
    }
  }

  function finishAllSteps() {
    WORKFLOW_STEPS.forEach((s) => {
      const cur = state.taskSteps[s.key];
      if (cur && cur.status !== "done" && cur.status !== "completed") {
        const dur = cur.startedAt ? Date.now() - cur.startedAt : null;
        state.taskSteps[s.key] = { status: "done", duration: dur || cur.duration, message: cur.message, data: cur.data };
      }
    });
    renderWorkflowArea();
    renderModalSteps();
  }

  function renderWorkflowArea() {
    const area = $("#workflow-area");
    if (!area) return;
    area.innerHTML = `
      <div class="workflow-progress">
        ${WORKFLOW_STEPS.map((s) => {
          const st = state.taskSteps[s.key] || { status: "pending" };
          const cls = st.status === "done" || st.status === "completed" ? "done" : st.status === "processing" ? "processing" : "";
          return `<div class="wf-step ${cls}"><div class="wf-dot"></div><div class="wf-label">${esc(s.name)}</div></div>`;
        }).join("")}
      </div>
      <div class="steps mt-16">
        ${WORKFLOW_STEPS.map((s, i) => {
          const st = state.taskSteps[s.key] || { status: "pending" };
          const cls = st.status === "done" || st.status === "completed" ? "done" : st.status === "processing" ? "processing" : st.status === "failed" ? "" : "";
          const num = i + 1;
          const icon = st.status === "done" || st.status === "completed"
            ? iconCheck()
            : st.status === "processing"
            ? iconLoader()
            : st.status === "failed"
            ? "!"
            : num;
          const meta = st.duration ? `耗时 ${fmtDuration(st.duration)}` : (st.status === "processing" ? "进行中…" : "");
          return `
            <div class="step ${cls}">
              <div class="step-icon">${icon}</div>
              <div class="step-body">
                <div class="step-name">${esc(s.name)} ${st.status === "processing" ? statusBadge("进行中") : st.status === "done" || st.status === "completed" ? statusBadge("完成") : st.status === "failed" ? statusBadge("失败") : statusBadge("待处理")}</div>
                <div class="step-desc">${esc(s.desc)}</div>
                ${st.message ? `<div class="step-desc" style="color:var(--text-dim)">${esc(st.message)}</div>` : ""}
                ${meta ? `<div class="step-meta">${esc(meta)}</div>` : ""}
              </div>
            </div>`;
        }).join("")}
      </div>`;
  }

  function renderParseResult() {
    const el = $("#parse-result");
    if (!el) return;
    if (!state.parseResult) {
      el.innerHTML = `<div class="parse-empty">解析中…</div>`;
      return;
    }
    const r = state.parseResult;
    const fields = r.fields || r.extracted_fields || r.items || (Array.isArray(r) ? r : []);
    if (!fields.length && !Object.keys(r).length) {
      el.innerHTML = `<div class="parse-empty">暂无可显示字段</div>`;
      return;
    }
    let rows;
    if (Array.isArray(fields) && fields.length) {
      rows = fields.map((f) => {
        const key = f.key || f.name || f.field || "-";
        const val = f.value != null ? f.value : (f.text || "-");
        const conf = f.confidence != null ? f.confidence : (f.score != null ? f.score : null);
        return fieldRow(key, val, conf);
      }).join("");
    } else {
      rows = Object.entries(r).filter(([k]) => !["fields", "extracted_fields", "items"].includes(k)).slice(0, 12).map(([k, v]) => {
        const val = typeof v === "object" ? JSON.stringify(v) : v;
        return fieldRow(k, val, null);
      }).join("");
    }
    el.innerHTML = `
      <div class="field-row" style="border-bottom:2px solid var(--border)">
        <div class="text-mute text-sm"><b>字段</b></div>
        <div class="text-mute text-sm"><b>提取值</b></div>
        <div class="text-mute text-sm"><b>置信度</b></div>
      </div>
      ${rows}`;
  }

  function fieldRow(key, val, conf) {
    let confCls = "";
    let confWidth = 0;
    if (conf != null) {
      conf = Number(conf);
      if (conf < 0.5) confCls = "verylow";
      else if (conf < 0.75) confCls = "low";
      confWidth = Math.round(conf * 100);
    }
    return `
      <div class="field-row">
        <div class="field-key">${esc(key)}</div>
        <div class="field-val">${esc(val)}</div>
        <div class="field-conf">
          ${conf != null ? `<div class="conf-bar ${confCls}"><div style="width:${confWidth}%"></div></div><span class="conf-val">${confWidth}%</span>` : `<span class="text-mute">-</span>`}
        </div>
      </div>`;
  }

  /* ---------- 进度模态框 ---------- */
  function openProgressModal(taskId) {
    openModal("智能体工作流进度", `
      <div class="modal-task-info">
        <div class="mti-row"><span class="mti-key">任务 ID</span><span class="mti-val">${esc(taskId)}</span></div>
        <div class="mti-row"><span class="mti-key">平台</span><span class="mti-val">${esc(state.selectedPlatform)}</span></div>
        <div class="mti-row"><span class="mti-key">目标国家</span><span class="mti-val">${esc(state.selectedCountry)}</span></div>
        <div class="mti-row"><span class="mti-key">总耗时</span><span class="mti-val" id="modal-elapsed">0.0s</span></div>
      </div>
      <div id="modal-steps"></div>
      <div class="modal-result" id="modal-result" style="display:none">
        <div class="modal-result-title">解析结果预览</div>
        <div id="modal-result-body"></div>
      </div>`);
    renderModalSteps();
    // 计时
    const elapsedEl = $("#modal-elapsed");
    state.modalTimer = setInterval(() => {
      if (state.taskStartTime && elapsedEl) {
        elapsedEl.textContent = ((Date.now() - state.taskStartTime) / 1000).toFixed(1) + "s";
      }
    }, 100);
  }

  function renderModalSteps() {
    const wrap = $("#modal-steps");
    if (!wrap) return;
    wrap.innerHTML = `
      <div class="steps">
        ${WORKFLOW_STEPS.map((s, i) => {
          const st = state.taskSteps[s.key] || { status: "pending" };
          const cls = st.status === "done" || st.status === "completed" ? "done" : st.status === "processing" ? "processing" : "";
          const num = i + 1;
          const icon = st.status === "done" || st.status === "completed"
            ? iconCheck() : st.status === "processing" ? iconLoader() : st.status === "failed" ? "!" : num;
          const meta = st.duration ? `耗时 ${fmtDuration(st.duration)}` : (st.status === "processing" ? "进行中…" : "");
          return `
            <div class="step ${cls}">
              <div class="step-icon">${icon}</div>
              <div class="step-body">
                <div class="step-name">${esc(s.name)} ${st.status === "processing" ? statusBadge("进行中") : st.status === "done" || st.status === "completed" ? statusBadge("完成") : st.status === "failed" ? statusBadge("失败") : statusBadge("待处理")}</div>
                <div class="step-desc">${esc(s.desc)}</div>
                ${st.message ? `<div class="step-desc" style="color:var(--text-dim)">${esc(st.message)}</div>` : ""}
                ${meta ? `<div class="step-meta">${esc(meta)}</div>` : ""}
              </div>
            </div>`;
        }).join("")}
      </div>`;
    // 渲染结果预览
    const resultBox = $("#modal-result");
    if (resultBox && state.parseResult) {
      resultBox.style.display = "";
      $("#modal-result-body").innerHTML = `<div class="json-view">${highlightJSON(state.parseResult)}</div>`;
    }
  }

  function closeProgressModal() {
    if (state.modalTimer) { clearInterval(state.modalTimer); state.modalTimer = null; }
  }

  /* ---------- 本地演示流程（后端未就绪时） ---------- */
  async function demoWorkflow() {
    const demoTaskId = "demo-" + Date.now().toString(36);
    state.currentTask = { id: demoTaskId, status: "processing" };
    openProgressModal(demoTaskId);
    setConnStatus("offline", "后端离线 · 演示模式");

    const steps = [
      { key: "receive", msg: "Qwen3 Coder 已解析指令，分解为 4 个子任务", dur: 1200 },
      { key: "parse", msg: "GMI VLM 完成 OCR，提取 8 个字段，平均置信度 92%", dur: 2600, parse: true },
      { key: "compliance", msg: "GPT OSS 120B 匹配美国加州销售税法，税率 8.5%", dur: 2200 },
      { key: "advice", msg: "GLM-4.5 生成中英双语合规建议，未发现高风险", dur: 1600 },
      { key: "report", msg: "DeepSeek V3.1 校验金额，利润表与 VAT 表已生成", dur: 1400 },
    ];

    updateStep("receive", "processing", "正在解析指令…");
    for (const s of steps) {
      await sleep(s.dur);
      if (s.parse) {
        state.parseResult = demoParseResult();
        renderParseResult();
      }
      updateStep(s.key, "done", s.msg);
      await sleep(150);
      const idx = WORKFLOW_STEPS.findIndex((w) => w.key === s.key);
      if (idx < WORKFLOW_STEPS.length - 1) {
        updateStep(WORKFLOW_STEPS[idx + 1].key, "processing", "开始处理…");
      }
    }
    finishAllSteps();
    modalDone.style.display = "";
    modalCancel.style.display = "none";
    toast("success", "演示流程完成", "连接真实后端可体验完整 Agent 工作流");
  }

  function demoParseResult() {
    return {
      fields: [
        { key: "平台", value: "Amazon", confidence: 0.99 },
        { key: "订单号", value: "112-7782214-5560010", confidence: 0.97 },
        { key: "买家", value: "John Smith", confidence: 0.95 },
        { key: "商品", value: "Wireless Earbuds x 2", confidence: 0.93 },
        { key: "金额", value: "$89.99", confidence: 0.98 },
        { key: "税率", value: "8.5%", confidence: 0.91 },
        { key: "税额", value: "$7.65", confidence: 0.96 },
        { key: "总计", value: "$97.64", confidence: 0.99 },
      ],
      ship_to: "1234 Market St, San Francisco, CA 94103, USA",
      currency: "USD",
      platform: state.selectedPlatform,
    };
  }

  /* ============================================================
     页面 3 — Agent 集群
     ============================================================ */
  function renderAgents() {
    app.innerHTML = `
      <div class="page" id="page-agents">
        <div class="grid grid-3 mb-20" id="agent-cards">
          ${AGENTS_META.map((a) => agentCardHTML(a)).join("")}
        </div>
        <div class="grid grid-2">
          <div class="card" id="agent-dist">
            <div class="card-title">${iconPie()} Agent 任务分布</div>
            <div class="pie-wrap">
              <canvas id="pie-canvas" width="180" height="180"></canvas>
              <div class="pie-legend" id="pie-legend"></div>
            </div>
          </div>
          <div class="card" id="agent-status">
            <div class="card-title">${iconActivity()} 实时状态</div>
            <div class="skeleton sk-line"></div><div class="skeleton sk-line"></div>
            <div class="skeleton sk-line"></div>
          </div>
        </div>
      </div>`;
  }

  function agentCardHTML(a) {
    return `
      <div class="agent-card">
        <span class="agent-status-tag">${statusBadge("运行中")}</span>
        <div class="agent-head">
          <div class="agent-avatar ${a.cls}">${a.icon}</div>
          <div>
            <div class="agent-name">${esc(a.name)}</div>
            <div class="agent-role">${esc(a.role)}</div>
          </div>
        </div>
        <div class="agent-desc">${esc(a.desc)}</div>
        <div class="agent-meta">
          <div class="meta-item"><div class="meta-label">今日完成</div><div class="meta-val" id="agent-done-${a.id}">-</div></div>
          <div class="meta-item"><div class="meta-label">待处理</div><div class="meta-val" id="agent-pending-${a.id}">-</div></div>
        </div>
        <div class="agent-models">${a.models.map((m) => `<span class="model-tag">${esc(m)}</span>`).join("")}</div>
      </div>`;
  }

  async function loadAgents() {
    try {
      const data = await API.dashboard();
      state.dashboard = data;
      setConnStatus("online", "已连接后端");
      const agents = data.agent_status || [];
      // 映射到卡片
      const map = {};
      agents.forEach((a) => {
        const name = (a.name || "").toLowerCase();
        if (name.includes("receipt") || name.includes("票据")) map.receipt = a;
        else if (name.includes("compliance") || name.includes("合规")) map.compliance = a;
        else if (name.includes("orchestr") || name.includes("调度")) map.orchestrator = a;
      });
      AGENTS_META.forEach((meta) => {
        const a = map[meta.id];
        const doneEl = $("#agent-done-" + meta.id);
        const pendEl = $("#agent-pending-" + meta.id);
        if (a) {
          if (doneEl) doneEl.textContent = fmtNum(a.completed_today);
          if (pendEl) pendEl.textContent = fmtNum(a.pending);
        } else {
          if (doneEl) doneEl.textContent = "0";
          if (pendEl) pendEl.textContent = "0";
        }
      });
      // 状态表
      renderAgentStatus(agents);
      // 饼图
      renderPie(agents);
    } catch (e) {
      if (isConnectionError(e)) {
        setConnStatus("offline", "后端未连接");
        const demo = demoAgents();
        renderAgentStatus(demo);
        renderPie(demo);
        toast("warn", "使用演示数据", "后端未连接，已加载演示 Agent 数据");
      } else {
        toast("error", "加载失败", e.message);
      }
    }
  }

  function demoAgents() {
    return [
      { name: "票据多模态解析 Agent", status: "运行中", pending: 3, completed_today: 128, token_cost: 4.2 },
      { name: "多国财税合规决策 Agent", status: "运行中", pending: 1, completed_today: 96, token_cost: 3.8 },
      { name: "业财自动化调度 Agent", status: "运行中", pending: 2, completed_today: 224, token_cost: 1.6 },
    ];
  }

  function renderAgentStatus(agents) {
    const el = $("#agent-status");
    if (!el) return;
    el.innerHTML = `
      <div class="card-title">${iconActivity()} 实时状态</div>
      <div class="table-wrap">
        <table class="table">
          <thead><tr><th>Agent</th><th>状态</th><th>待处理</th><th>今日完成</th><th>Token</th></tr></thead>
          <tbody>
            ${agents.map((a) => `
              <tr>
                <td><b>${esc(a.name)}</b></td>
                <td>${statusBadge(a.status)}</td>
                <td class="mono">${fmtNum(a.pending)}</td>
                <td class="mono">${fmtNum(a.completed_today)}</td>
                <td class="mono">${fmtMoney(a.token_cost)}</td>
              </tr>`).join("")}
          </tbody>
        </table>
      </div>`;
  }

  function renderPie(agents) {
    const canvas = $("#pie-canvas");
    const legend = $("#pie-legend");
    if (!canvas) return;
    const colors = ["#3b82f6", "#8b5cf6", "#10b981"];
    const data = agents.map((a, i) => ({
      label: a.name,
      value: Number(a.completed_today) || 0,
      color: colors[i % colors.length],
    }));
    const total = data.reduce((s, d) => s + d.value, 0) || 1;

    // 绘制饼图
    const ctx = canvas.getContext("2d");
    const cx = canvas.width / 2, cy = canvas.height / 2, r = 70;
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    let start = -Math.PI / 2;
    data.forEach((d) => {
      const angle = (d.value / total) * Math.PI * 2;
      ctx.beginPath();
      ctx.moveTo(cx, cy);
      ctx.arc(cx, cy, r, start, start + angle);
      ctx.closePath();
      ctx.fillStyle = d.color;
      ctx.fill();
      ctx.strokeStyle = "#1e293b";
      ctx.lineWidth = 3;
      ctx.stroke();
      start += angle;
    });
    // 中心圆
    ctx.beginPath();
    ctx.arc(cx, cy, r * 0.55, 0, Math.PI * 2);
    ctx.fillStyle = "#1e293b";
    ctx.fill();
    ctx.fillStyle = "#e2e8f0";
    ctx.font = "bold 16px sans-serif";
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";
    ctx.fillText(fmtNum(total), cx, cy - 6);
    ctx.font = "10px sans-serif";
    ctx.fillStyle = "#94a3b8";
    ctx.fillText("总任务", cx, cy + 12);

    // 图例
    legend.innerHTML = data.map((d) => `
      <div class="legend-item">
        <span class="legend-dot" style="background:${d.color}"></span>
        <span>${esc(d.label)}</span>
        <span class="legend-val">${fmtNum(d.value)}</span>
      </div>`).join("");
  }

  /* ============================================================
     页面 4 — 财税报表
     ============================================================ */
  function renderReports() {
    app.innerHTML = `
      <div class="page" id="page-reports">
        <div class="grid grid-2">
          <div>
            <div class="card">
              <div class="card-title">${iconDoc()} 报表列表</div>
              <div id="reports-list">
                <div class="skeleton sk-block"></div>
              </div>
            </div>
          </div>
          <div>
            <div class="card">
              <div class="card-title">${iconCode()} 报表详情（JSON）</div>
              <div id="report-detail">
                <div class="empty-state">
                  ${iconDoc(40)}
                  <div class="es-title">选择左侧报表查看详情</div>
                  <div class="es-desc">点击任意报表项以加载完整内容</div>
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>`;
  }

  const REPORT_DEFS = [
    { id: "income", name: "利润表", sub: "Income Statement · 收入、成本与净利润", color: "#3b82f6", icon: "I" },
    { id: "vat", name: "VAT 申报表", sub: "VAT Return · 增值税销项与进项", color: "#10b981", icon: "V" },
    { id: "risk", name: "税务风险评估", sub: "Tax Risk Assessment · 风险点与建议", color: "#f59e0b", icon: "R" },
  ];

  async function loadReportsList() {
    const el = $("#reports-list");
    el.innerHTML = REPORT_DEFS.map((r) => `
      <div class="report-item" data-report="${r.id}">
        <div class="report-icon" style="background:${r.color}22;color:${r.color}">${r.icon}</div>
        <div class="report-meta">
          <div class="report-name">${esc(r.name)}</div>
          <div class="report-sub">${esc(r.sub)}</div>
        </div>
        <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="color:var(--text-mute)"><polyline points="9 18 15 12 9 6"/></svg>
      </div>`).join("");
    $$("#reports-list .report-item").forEach((item) => {
      item.addEventListener("click", () => loadReport(item.dataset.report));
    });
    // 尝试加载最近任务报表
    try {
      const tasks = await API.tasks();
      const recentDone = (tasks.tasks || tasks || []).find((t) => /done|completed/i.test(t.status));
      if (recentDone) {
        $$("#reports-list .report-item").forEach((i) => i.dataset.task = recentDone.id);
      }
    } catch { /* 忽略 */ }
  }

  async function loadReport(reportId) {
    const detail = $("#report-detail");
    detail.innerHTML = `<div class="loading-screen" style="height:120px"><div class="loader"></div><p>加载报表中…</p></div>`;
    // 先展示演示报表
    let report;
    try {
      // 尝试通过任务 ID 获取真实报表
      const taskId = reportId && reportId.length > 10 ? reportId : null;
      if (taskId) {
        report = await API.report(taskId);
      } else {
        report = demoReport(reportId);
      }
    } catch (e) {
      report = demoReport(reportId);
      if (!isConnectionError(e)) toast("warn", "使用演示报表", e.message);
    }
    detail.innerHTML = `
      <div class="flex justify-between mb-16">
        <div>
          <div style="font-size:15px;font-weight:600">${esc(report.title || report.name || reportId)}</div>
          <div class="text-mute text-sm mt-0">${esc(report.subtitle || "生成于 " + fmtTime(Date.now()))}</div>
        </div>
        ${report.currency ? `<span class="badge purple plain">${esc(report.currency)}</span>` : ""}
      </div>
      <div class="json-view">${highlightJSON(report)}</div>`;
  }

  function demoReport(id) {
    const base = {
      income: {
        title: "利润表 Income Statement",
        subtitle: "期间 2026-01 至 2026-06 · 跨境销售业务",
        currency: "USD",
        revenue: 48250.00,
        cost_of_goods: 26800.00,
        gross_profit: 21450.00,
        operating_expenses: 7320.00,
        platform_fees: 2890.00,
        shipping: 3410.00,
        net_profit: 7830.00,
        net_margin: "16.23%",
        currency_note: "已按当期汇率折算为美元",
      },
      vat: {
        title: "VAT 申报表 VAT Return",
        subtitle: "申报国 德国(DE) · 季度申报",
        currency: "EUR",
        country: "DE",
        output_vat: 3120.45,
        input_vat: 1840.20,
        vat_payable: 1280.25,
        vat_rate: "19%",
        threshold_warning: false,
        due_date: "2026-07-20",
      },
      risk: {
        title: "税务风险评估 Tax Risk Assessment",
        subtitle: "基于 GPT OSS 120B 推理生成",
        currency: "USD",
        risk_level: "中低",
        risk_score: 28,
        findings: [
          { area: "销售税", level: "中", detail: "加州销售税率 8.5% 已正确计提，建议关注平台代扣代缴凭证" },
          { area: "转移定价", level: "低", detail: "关联交易金额较小，暂无显著风险" },
          { area: "发票合规", level: "低", detail: "票据字段完整，未发现缺失关键字段" },
        ],
        recommendations: [
          "保留所有平台结算凭证以备审计",
          "关注欧盟 IOSS 简化申报门槛 (€150)",
          "建议每季度复核汇率折算口径",
        ],
      },
    };
    return base[id] || base.income;
  }

  /* ============================================================
     页面 5 — GMI 监控
     ============================================================ */
  function renderGMI() {
    app.innerHTML = `
      <div class="page" id="page-gmi">
        <div class="card mb-20">
          <div class="card-title">${iconServer()} 推理实例</div>
          <div class="table-wrap" id="gmi-instances">
            <div class="skeleton sk-line"></div><div class="skeleton sk-line"></div>
            <div class="skeleton sk-line"></div><div class="skeleton sk-line"></div>
          </div>
        </div>
        <div class="grid grid-2 mb-20">
          <div class="card">
            <div class="card-title">${iconToken()} Token 消耗明细（按模型）</div>
            <div id="gmi-tokens"><div class="skeleton sk-line"></div><div class="skeleton sk-line"></div></div>
          </div>
          <div class="card">
            <div class="card-title">${iconScale()} 弹性扩缩容日志</div>
            <div class="log-list" id="gmi-scaling"><div class="skeleton sk-line"></div><div class="skeleton sk-line"></div></div>
          </div>
        </div>
        <div class="card">
          <div class="card-title">${iconModel()} 可用模型列表</div>
          <div id="gmi-models"><div class="skeleton sk-line"></div><div class="skeleton sk-line"></div></div>
        </div>
      </div>`;
  }

  async function loadGMI() {
    const [statusRes, modelsRes] = await Promise.all([
      API.gmiStatus().catch((e) => { if (isConnectionError(e)) return null; throw e; }),
      API.gmiModels().catch((e) => { if (isConnectionError(e)) return null; throw e; }),
    ]);

    if (!statusRes && !modelsRes) {
      setConnStatus("offline", "后端未连接");
      toast("warn", "使用演示数据", "GMI 后端未连接，已加载演示数据");
      loadDemoGMI();
      return;
    }
    setConnStatus("online", "已连接后端");
    state.gmiStatus = statusRes;
    state.gmiModels = (modelsRes && modelsRes.models) || [];

    // 实例表
    const instances = (statusRes && statusRes.instances) || [];
    $("#gmi-instances").innerHTML = `
      <table class="table">
        <thead><tr><th>实例 ID</th><th>模型</th><th>类型</th><th>状态</th><th>GPU</th><th>Token 用量</th><th>创建时间</th></tr></thead>
        <tbody>
          ${instances.length ? instances.map((i) => {
            const gpu = Number(i.gpu || 0);
            const gpuCls = gpu > 80 ? "high" : gpu > 50 ? "mid" : "";
            return `
              <tr>
                <td class="mono">${esc(i.id)}</td>
                <td><span class="model-tag">${esc(i.model)}</span></td>
                <td>${esc(i.type || "-")}</td>
                <td>${statusBadge(i.status)}</td>
                <td>
                  <div class="gpu-bar">
                    <div class="gpu-track"><div class="gpu-fill ${gpuCls}" style="width:${gpu}%"></div></div>
                    <span class="gpu-pct">${gpu}%</span>
                  </div>
                </td>
                <td class="mono">${fmtNum(i.token_usage)}</td>
                <td class="mono">${fmtTime(i.created_at)}</td>
              </tr>`;
          }).join("") : `<tr><td colspan="7" class="text-dim text-center" style="padding:24px">暂无运行实例</td></tr>`}
        </tbody>
      </table>`;

    // Token 明细
    const tokenUsage = (statusRes && statusRes.token_usage_today) || {};
    const models = tokenUsage.models || [];
    const total = tokenUsage.total_cost || 0;
    $("#gmi-tokens").innerHTML = `
      <div class="flex justify-between mb-16">
        <span class="text-dim text-sm">今日 Token 总消耗</span>
        <span class="text-mono" style="font-size:18px;font-weight:700">${fmtMoney(total)}</span>
      </div>
      ${models.length ? models.map((m) => {
        const pct = total ? (m.cost / total) * 100 : 0;
        return `
          <div class="metric-row">
            <span class="metric-name"><span class="model-tag">${esc(m.model || m.name)}</span></span>
            <div class="metric-bar"><div style="width:${pct}%"></div></div>
            <span class="metric-val">${fmtMoney(m.cost || m.token_cost || 0)}</span>
          </div>`;
      }).join("") : `<div class="empty-state text-sm">暂无消耗记录</div>`}`;

    // 扩缩容日志
    const scaling = (statusRes && statusRes.scaling_log) || [];
    $("#gmi-scaling").innerHTML = scaling.length ? scaling.map((l) => `
      <div class="log-item ${l.action === "scale_up" || l.type === "scale_up" ? "scale-up" : "scale-down"}">
        <span class="log-time">${esc(l.time || l.timestamp || fmtTime(Date.now()))}</span>
        <span class="log-msg">${esc(l.message || l.action || "")}</span>
      </div>`).join("") : `<div class="empty-state text-sm">暂无扩缩容记录</div>`;

    // 模型列表
    renderModels();
  }

  function renderModels() {
    const el = $("#gmi-models");
    if (!el) return;
    const models = state.gmiModels;
    el.innerHTML = models.length ? `
      <div class="table-wrap">
        <table class="table">
          <thead><tr><th>模型 ID</th><th>类型</th><th>上下文</th><th>状态</th></tr></thead>
          <tbody>
            ${models.map((m) => `
              <tr>
                <td><span class="model-tag">${esc(m.id || m.name)}</span></td>
                <td>${esc(m.type || m.category || "LLM")}</td>
                <td class="mono">${esc(m.context_length || m.max_tokens || "-")}</td>
                <td>${statusBadge(m.status || "可用")}</td>
              </tr>`).join("")}
          </tbody>
        </table>
      </div>` : `<div class="empty-state text-sm">暂无可用模型</div>`;
  }

  function loadDemoGMI() {
    const demo = {
      instances: [
        { id: "ins-vlm-01", model: "gmi-cloud-vlm", type: "多模态", status: "运行中", gpu: 78, token_usage: 84200, created_at: Date.now() - 3600000 },
        { id: "ins-ds-02", model: "deepseek-v3.1", type: "LLM", status: "运行中", gpu: 62, token_usage: 156000, created_at: Date.now() - 7200000 },
        { id: "ins-gptoss-03", model: "gpt-oss-120b", type: "LLM", status: "运行中", gpu: 91, token_usage: 203400, created_at: Date.now() - 5400000 },
        { id: "ins-qwen-04", model: "qwen3-coder", type: "LLM", status: "空闲", gpu: 12, token_usage: 42100, created_at: Date.now() - 10800000 },
        { id: "ins-glm-05", model: "glm-4.5", type: "LLM", status: "运行中", gpu: 45, token_usage: 67800, created_at: Date.now() - 1800000 },
      ],
      token_usage_today: {
        models: [
          { model: "gpt-oss-120b", cost: 3.20 },
          { model: "deepseek-v3.1", cost: 2.80 },
          { model: "gmi-cloud-vlm", cost: 2.10 },
          { model: "glm-4.5", cost: 0.85 },
          { model: "qwen3-coder", cost: 0.65 },
        ],
        total_cost: 9.60,
      },
      scaling_log: [
        { time: fmtTime(Date.now() - 900000), action: "scale_up", message: "gpt-oss-120b 实例扩容 +1 (负载 91%)" },
        { time: fmtTime(Date.now() - 1800000), action: "scale_up", message: "gmi-cloud-vlm 实例扩容 +1 (票据解析高峰)" },
        { time: fmtTime(Date.now() - 3600000), action: "scale_down", message: "qwen3-coder 实例缩容 -1 (空闲超 10 分钟)" },
        { time: fmtTime(Date.now() - 7200000), action: "scale_up", message: "deepseek-v3.1 实例扩容 +1 (并发任务 8)" },
      ],
    };
    state.gmiStatus = demo;
    state.gmiModels = [
      { id: "gmi-cloud-vlm", type: "VLM", context_length: "32K", status: "可用" },
      { id: "deepseek-v3.1", type: "LLM", context_length: "128K", status: "可用" },
      { id: "gpt-oss-120b", type: "LLM", context_length: "128K", status: "可用" },
      { id: "qwen3-coder", type: "LLM", context_length: "64K", status: "可用" },
      { id: "glm-4.5", type: "LLM", context_length: "32K", status: "可用" },
    ];

    const instances = demo.instances;
    $("#gmi-instances").innerHTML = `
      <table class="table">
        <thead><tr><th>实例 ID</th><th>模型</th><th>类型</th><th>状态</th><th>GPU</th><th>Token 用量</th><th>创建时间</th></tr></thead>
        <tbody>
          ${instances.map((i) => {
            const gpu = i.gpu;
            const gpuCls = gpu > 80 ? "high" : gpu > 50 ? "mid" : "";
            return `
              <tr>
                <td class="mono">${esc(i.id)}</td>
                <td><span class="model-tag">${esc(i.model)}</span></td>
                <td>${esc(i.type)}</td>
                <td>${statusBadge(i.status)}</td>
                <td><div class="gpu-bar"><div class="gpu-track"><div class="gpu-fill ${gpuCls}" style="width:${gpu}%"></div></div><span class="gpu-pct">${gpu}%</span></div></td>
                <td class="mono">${fmtNum(i.token_usage)}</td>
                <td class="mono">${fmtTime(i.created_at)}</td>
              </tr>`;
          }).join("")}
        </tbody>
      </table>`;

    const tu = demo.token_usage_today;
    $("#gmi-tokens").innerHTML = `
      <div class="flex justify-between mb-16">
        <span class="text-dim text-sm">今日 Token 总消耗</span>
        <span class="text-mono" style="font-size:18px;font-weight:700">${fmtMoney(tu.total_cost)}</span>
      </div>
      ${tu.models.map((m) => {
        const pct = (m.cost / tu.total_cost) * 100;
        return `
          <div class="metric-row">
            <span class="metric-name"><span class="model-tag">${esc(m.model)}</span></span>
            <div class="metric-bar"><div style="width:${pct}%"></div></div>
            <span class="metric-val">${fmtMoney(m.cost)}</span>
          </div>`;
      }).join("")}`;

    $("#gmi-scaling").innerHTML = demo.scaling_log.map((l) => `
      <div class="log-item ${l.action === "scale_up" ? "scale-up" : "scale-down"}">
        <span class="log-time">${esc(l.time)}</span>
        <span class="log-msg">${esc(l.message)}</span>
      </div>`).join("");

    renderModels();
  }

  /* ============================================================
     页面 6 — API 文档
     ============================================================ */
  const API_DOCS = [
    { method: "GET", path: "/api/dashboard", desc: "获取仪表盘总览：任务统计、Agent 状态、Token 预算、最近任务", body: null },
    { method: "GET", path: "/api/gmi/models", desc: "获取 GMI Cloud 可用模型列表", body: null },
    { method: "GET", path: "/api/gmi/status", desc: "获取 GMI 推理实例状态、Token 消耗明细、扩缩容日志", body: null },
    { method: "POST", path: "/api/tasks", desc: "创建财税智能体任务，触发完整 Agent 工作流", body: `{ "task_type": "receipt_compliance", "platform": "amazon", "target_country": "美国(US)", "receipt_text": "..." }` },
    { method: "GET", path: "/api/tasks/{id}", desc: "查询单个任务详情与进度", body: null },
    { method: "GET", path: "/api/tasks", desc: "获取所有任务列表", body: null },
    { method: "POST", path: "/api/receipts/parse", desc: "单独解析票据图片，返回结构化字段与置信度", body: `{ "image": "base64_string" }` },
    { method: "GET", path: "/api/reports/{task_id}", desc: "获取指定任务的财税报表", body: null },
    { method: "GET", path: "/api/docs", desc: "获取 API 文档信息", body: null },
    { method: "WS", path: "/ws", desc: "WebSocket 推送任务进度：{ type, task_id, step, message, data }", body: null },
  ];

  function renderApiDocs() {
    app.innerHTML = `
      <div class="page" id="page-api-docs">
        <div class="card mb-20">
          <div class="card-title">${iconBook()} 接口列表</div>
          <div id="api-list">
            ${API_DOCS.map((d) => apiEndpointHTML(d)).join("")}
          </div>
        </div>
        <div class="card">
          <div class="card-title">${iconCode()} 示例代码</div>
          <div class="code-block">
            <button class="copy-btn" id="copy-curl">复制</button>
            <pre id="curl-sample">${highlightShell(curlSample())}</pre>
          </div>
          <div class="form-hint mt-16">Base URL: <span class="text-mono">${CONFIG.API_BASE}</span> · WebSocket: <span class="text-mono">${CONFIG.WS_BASE}/ws</span></div>
        </div>
      </div>`;
    $("#copy-curl").addEventListener("click", () => {
      const text = curlSample();
      navigator.clipboard.writeText(text).then(
        () => toast("success", "已复制", "curl 示例已写入剪贴板"),
        () => toast("error", "复制失败", "请手动选择文本复制")
      );
    });
    $$("#api-list .api-endpoint").forEach((ep) => {
      ep.addEventListener("click", () => {
        const path = $(".api-path", ep).textContent;
        const method = $(".method", ep).textContent;
        navigator.clipboard.writeText(method + " " + CONFIG.API_BASE + path).then(
          () => toast("success", "已复制端点", method + " " + path)
        );
      });
    });
  }

  function apiEndpointHTML(d) {
    return `
      <div class="api-endpoint" title="点击复制端点">
        <div class="api-head">
          <span class="method ${d.method}">${d.method}</span>
          <span class="api-path">${esc(d.path)}</span>
        </div>
        <div class="api-desc">${esc(d.desc)}</div>
        ${d.body ? `<div class="code-block" style="margin-top:8px"><pre>${highlightJSON(d.body)}</pre></div>` : ""}
      </div>`;
  }

  function curlSample() {
    return `# 1. 创建财税智能体任务
curl -X POST ${CONFIG.API_BASE}/api/tasks \\
  -H "Content-Type: application/json" \\
  -d '{
    "task_type": "receipt_compliance",
    "platform": "amazon",
    "target_country": "美国(US)",
    "receipt_text": "Amazon Order #112-7782214-5560010..."
  }'

# 2. 查询任务详情
curl ${CONFIG.API_BASE}/api/tasks/{task_id}

# 3. 获取仪表盘
curl ${CONFIG.API_BASE}/api/dashboard

# 4. 解析票据图片
curl -X POST ${CONFIG.API_BASE}/api/receipts/parse \\
  -H "Content-Type: application/json" \\
  -d '{"image": "<base64_string>"}'

# 5. WebSocket 监听进度
wscat -c ${CONFIG.WS_BASE}/ws?task_id={task_id}`;
  }

  async function loadApiDocs() {
    // 可选：从后端拉取文档补充
    try {
      const docs = await API.docs();
      if (docs) setConnStatus("online", "已连接后端");
    } catch (e) {
      if (isConnectionError(e)) setConnStatus("offline", "后端未连接");
    }
  }

  /* ============================================================
     侧边栏预算 & 交互
     ============================================================ */
  function updateSidebarBudget(used, total, pct) {
    $("#sidebar-budget-pct").textContent = "$" + Number(used).toFixed(2);
    $("#sidebar-budget-bar").style.width = Math.min(pct, 100) + "%";
    $("#sidebar-budget-text").textContent = `已用 $${used.toFixed(2)} / $${total.toFixed(0)} (${pct.toFixed(1)}%)`;
  }

  function bindLayout() {
    const layout = $(".app-layout");
    $("#sidebar-collapse").addEventListener("click", () => layout.classList.toggle("sidebar-collapsed"));
    $("#menu-toggle").addEventListener("click", () => layout.classList.toggle("sidebar-open"));
    $("#refresh-btn").addEventListener("click", () => {
      const def = ROUTES[state.route];
      if (def && def.onEnter) {
        toast("info", "正在刷新", "重新加载当前页面数据");
        Promise.resolve(def.onEnter()).catch((e) => toast("error", "刷新失败", e.message));
      }
    });
  }

  /* ============================================================
     SVG 图标
     ============================================================ */
  function iconChart() { return `<svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 3v18h18"/><path d="M7 14l4-4 3 3 5-6"/></svg>`; }
  function iconReceipt() { return `<svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M4 2v20l2-1 2 1 2-1 2 1 2-1 2 1 2-1 2 1V2l-2 1-2-1-2 1-2-1-2 1-2-1-2 1z"/><path d="M8 7h8"/><path d="M8 11h8"/></svg>`; }
  function iconBolt(s) { return `<svg viewBox="0 0 24 24" width="${s||18}" height="${s||18}" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"/></svg>`; }
  function iconClock() { return `<svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/></svg>`; }
  function iconUpload(s) { return `<svg viewBox="0 0 24 24" width="${s||20}" height="${s||20}" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="17 8 12 3 7 8"/><line x1="12" y1="3" x2="12" y2="15"/></svg>`; }
  function iconText() { return `<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M4 7V4h16v3"/><path d="M9 20h6"/><path d="M12 4v16"/></svg>`; }
  function iconConfig() { return `<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z"/></svg>`; }
  function iconDoc(s) { return `<svg viewBox="0 0 24 24" width="${s||18}" height="${s||18}" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/></svg>`; }
  function iconFlow() { return `<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="3" width="6" height="6"/><rect x="15" y="15" width="6" height="6"/><path d="M9 6h6a3 3 0 0 1 3 3v6"/></svg>`; }
  function iconCheck() { return `<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"/></svg>`; }
  function iconLoader() { return `<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" style="animation:spin 0.9s linear infinite"><path d="M21 12a9 9 0 1 1-6.219-8.56"/></svg>`; }
  function iconClose(s) { return `<svg viewBox="0 0 24 24" width="${s||16}" height="${s||16}" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>`; }
  function iconPie() { return `<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21.21 15.89A10 10 0 1 1 8 2.83"/><path d="M22 12A10 10 0 0 0 12 2v10z"/></svg>`; }
  function iconActivity() { return `<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="22 12 18 12 15 21 9 3 6 12 2 12"/></svg>`; }
  function iconServer() { return `<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="2" y="2" width="20" height="8" rx="2"/><rect x="2" y="14" width="20" height="8" rx="2"/><line x1="6" y1="6" x2="6.01" y2="6"/><line x1="6" y1="18" x2="6.01" y2="18"/></svg>`; }
  function iconToken() { return `<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><path d="M14.31 8l5.74 9.94"/><path d="M9.69 8h11.48"/><path d="M7.38 12l5.74-9.94"/><path d="M9.69 16L3.95 6.06"/><path d="M14.31 16H2.83"/><path d="M16.62 12l-5.74 9.94"/></svg>`; }
  function iconScale() { return `<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 3v18h18"/><path d="M7 14l4-4 4 4 6-6"/></svg>`; }
  function iconModel() { return `<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 2L2 7l10 5 10-5-10-5z"/><path d="M2 17l10 5 10-5"/><path d="M2 12l10 5 10-5"/></svg>`; }
  function iconBook() { return `<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20"/><path d="M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2z"/></svg>`; }
  function iconCode() { return `<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="16 18 22 12 16 6"/><polyline points="8 6 2 12 8 18"/></svg>`; }

  /* ============================================================
     工具
     ============================================================ */
  function sleep(ms) { return new Promise((r) => setTimeout(r, ms)); }

  /* ============================================================
     初始化
     ============================================================ */
  function init() {
    bindLayout();
    router();
    // 探测后端连接
    API.dashboard().then((d) => {
      setConnStatus("online", "已连接后端");
      const tb = d.token_budget || {};
      const pct = tb.percentage != null ? tb.percentage : (tb.total ? (tb.used / tb.total) * 100 : 0);
      updateSidebarBudget(Number(tb.used || 0), Number(tb.total || 50), pct);
    }).catch((e) => {
      if (isConnectionError(e)) setConnStatus("offline", "后端未连接");
      else setConnStatus("offline", "后端异常");
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
