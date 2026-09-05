// 设置页逻辑

// 加载设置
async function loadSettings() {
    try {
        const data = await api("/api/settings");
        const s = data.data;
        const form = document.getElementById("settings-form");

        form.web_port.value = s.web_port || "*:45600";
        form.ncm_api_port.value = s.ncm_api_port || "45601";
        form.ncm_api_auto_start.checked = s.ncm_api_auto_start === "true";
        form.use_custom_api_url.checked = s.use_custom_api_url === "true";
        form.custom_api_url.value = s.custom_api_url || "";
        toggleCustomUrl();
        form.qq_api_port.value = s.qq_api_port || "45602";
        form.qq_api_auto_start.checked = s.qq_api_auto_start === "true";
        form.use_custom_qq_api_url.checked = s.use_custom_qq_api_url === "true";
        form.qq_api_base_url.value = s.qq_api_base_url || "http://127.0.0.1:45602";
        toggleCustomQqUrl();
        form.kugou_api_port.value = s.kugou_api_port || "45603";
        form.kugou_api_auto_start.checked = s.kugou_api_auto_start === "true";
        form.use_custom_kugou_api_url.checked = s.use_custom_kugou_api_url === "true";
        form.kugou_api_base_url.value = s.kugou_api_base_url || "http://127.0.0.1:45603";
        toggleCustomKugouUrl();

        form.output_dir.value = s.output_dir || "";
        form.level.value = s.level || "exhigh";
        form.max_retries.value = s.max_retries || "3";
        form.default_playlist_limit.value = s.default_playlist_limit || "50";
        form.exclude_keywords.value = s.exclude_keywords || "";
        // 加载过滤范围（兼容旧版 'both'）
        let scopes = [];
        const rawScope = s.exclude_scope || "playlist,search";
        if (rawScope === "both") {
            scopes = ["playlist", "search"];
        } else {
            scopes = rawScope.split(",").map(item => item.trim()).filter(item => item);
        }
        form.scope_playlist.checked = scopes.includes("playlist");
        form.scope_search.checked = scopes.includes("search");
        form.hourly_limit_per_account.value = s.hourly_limit_per_account || "50";
        form.download_mode.value = s.download_mode || "fallback";
        form.prefer_non_vip.checked = s.prefer_non_vip === "true";
        form.sync_times.value = s.sync_times || "03:00,09:00,21:00";
        form.sync_jitter.value = s.sync_jitter || "600";

        form.write_metadata.checked = s.write_metadata === "true";
        form.write_lyric.checked = s.write_lyric === "true";
        form.auto_sync_enabled.checked = s.auto_sync_enabled === "true";
    } catch (e) {
        showToast(e.message, "错误");
    }
}

// 保存设置
document.getElementById("settings-form").addEventListener("submit", async function(e) {
    e.preventDefault();
    const form = this;
    const payload = {
        web_port: form.web_port.value.trim(),
        ncm_api_port: form.ncm_api_port.value.trim(),
        ncm_api_auto_start: form.ncm_api_auto_start.checked ? "true" : "false",
        use_custom_api_url: form.use_custom_api_url.checked ? "true" : "false",
        custom_api_url: form.use_custom_api_url.checked ? form.custom_api_url.value.trim() : "",
        qq_api_port: form.qq_api_port.value.trim(),
        qq_api_auto_start: form.qq_api_auto_start.checked ? "true" : "false",
        use_custom_qq_api_url: form.use_custom_qq_api_url.checked ? "true" : "false",
        qq_api_base_url: form.use_custom_qq_api_url.checked ? form.qq_api_base_url.value.trim() : "",
        kugou_api_port: form.kugou_api_port.value.trim(),
        kugou_api_auto_start: form.kugou_api_auto_start.checked ? "true" : "false",
        use_custom_kugou_api_url: form.use_custom_kugou_api_url.checked ? "true" : "false",
        kugou_api_base_url: form.use_custom_kugou_api_url.checked ? form.kugou_api_base_url.value.trim() : "",
        output_dir: form.output_dir.value.trim(),
        level: form.level.value,
        max_retries: form.max_retries.value,
        default_playlist_limit: form.default_playlist_limit.value,
        exclude_keywords: form.exclude_keywords.value.trim(),
        // 收集 checkbox 选中项拼接为逗号分隔字符串
        exclude_scope: [
            form.scope_playlist.checked ? "playlist" : "",
            form.scope_search.checked ? "search" : "",
        ].filter(Boolean).join(","),
        hourly_limit_per_account: form.hourly_limit_per_account.value,
        download_mode: form.download_mode.value,
        prefer_non_vip: form.prefer_non_vip.checked ? "true" : "false",
        sync_times: form.sync_times.value.trim(),
        sync_jitter: form.sync_jitter.value,
        write_metadata: form.write_metadata.checked ? "true" : "false",
        write_lyric: form.write_lyric.checked ? "true" : "false",
        auto_sync_enabled: form.auto_sync_enabled.checked ? "true" : "false",
    };

    try {
        const data = await api("/api/settings", {
            method: "PUT",
            body: JSON.stringify(payload),
        });
        showToast(data.msg, "成功");
        loadSettings();
        refreshNcmStatus();  // 端口修改后自动重启完成，立即刷新状态显示
        refreshQqStatus();
        refreshKugouStatus();
    } catch (e) {
        showToast(e.message, "错误");
    }
});

// 自定义URL切换
function toggleCustomUrl() {
    const useCustom = document.getElementById("use-custom-api-url").checked;
    const customInput = document.getElementById("custom-api-url-input");
    const builtinSection = document.getElementById("ncm-builtin-section");

    if (useCustom) {
        customInput.disabled = false;
        customInput.required = true;
        customInput.focus();
        builtinSection.style.opacity = "0.5";
        builtinSection.style.pointerEvents = "none";
    } else {
        customInput.disabled = true;
        customInput.required = false;
        customInput.value = "";
        builtinSection.style.opacity = "";
        builtinSection.style.pointerEvents = "";
    }
}

document.getElementById("use-custom-api-url").addEventListener("change", toggleCustomUrl);

// QQ音乐自定义URL切换
function toggleCustomQqUrl() {
    const useCustom = document.getElementById("use-custom-qq-api-url").checked;
    const customInput = document.getElementById("qq-api-url-input");
    const builtinSection = document.getElementById("qq-builtin-section");

    if (useCustom) {
        customInput.disabled = false;
        customInput.required = true;
        customInput.focus();
        builtinSection.style.opacity = "0.5";
        builtinSection.style.pointerEvents = "none";
    } else {
        customInput.disabled = true;
        customInput.required = false;
        customInput.value = "";
        builtinSection.style.opacity = "";
        builtinSection.style.pointerEvents = "";
    }
}

document.getElementById("use-custom-qq-api-url").addEventListener("change", toggleCustomQqUrl);

// 酷狗音乐自定义URL切换
function toggleCustomKugouUrl() {
    const useCustom = document.getElementById("use-custom-kugou-api-url").checked;
    const customInput = document.getElementById("kugou-api-url-input");
    const builtinSection = document.getElementById("kugou-builtin-section");

    if (useCustom) {
        customInput.disabled = false;
        customInput.required = true;
        customInput.focus();
        builtinSection.style.opacity = "0.5";
        builtinSection.style.pointerEvents = "none";
    } else {
        customInput.disabled = true;
        customInput.required = false;
        customInput.value = "";
        builtinSection.style.opacity = "";
        builtinSection.style.pointerEvents = "";
    }
}

document.getElementById("use-custom-kugou-api-url").addEventListener("change", toggleCustomKugouUrl);

// 初始化
loadSettings();

// ======================================================================
// 网易云API服务状态轮询与启停
// ======================================================================
const ncmStatusBadge = document.getElementById("ncm-status-badge");
const ncmStatusDetail = document.getElementById("ncm-status-detail");

function renderNcmStatus(data) {
    // 自定义URL模式下显示特殊状态
    const useCustom = document.getElementById("use-custom-api-url").checked;
    const portInput = document.querySelector('[name="ncm_api_port"]');
    if (useCustom) {
        ncmStatusBadge.className = "badge bg-info";
        ncmStatusBadge.textContent = "自定义URL";
        ncmStatusDetail.textContent = "当前使用自定义API服务URL，内置服务已禁用";
        return;
    }
    if (!data) {
        ncmStatusBadge.className = "badge bg-secondary";
        ncmStatusBadge.textContent = "未知";
        ncmStatusDetail.textContent = "";
        return;
    }
    if (data.running) {
        ncmStatusBadge.className = "badge bg-success";
        ncmStatusBadge.textContent = "运行中";
        // 运行中禁止修改端口
        portInput.disabled = true;
        portInput.title = "请先停止API服务再修改端口";
    } else {
        ncmStatusBadge.className = "badge bg-danger";
        ncmStatusBadge.textContent = "已停止";
        // 停止后允许修改端口
        portInput.disabled = false;
        portInput.title = "";
    }
    const parts = [];
    if (data.port) parts.push(`端口 ${data.port}`);
    if (data.preferred_port && data.preferred_port !== data.port) parts.push(`配置端口 ${data.preferred_port}`);
    if (data.pid) parts.push(`PID ${data.pid}`);
    if (data.bin_exists === false) parts.push("未找到二进制");
    ncmStatusDetail.textContent = parts.join(" · ");
}

async function refreshNcmStatus() {
    try {
        const resp = await api("/api/ncm/status", { timeout: 5000 });
        renderNcmStatus(resp.data);
    } catch (e) {
        // 轮询失败保持上次状态即可，不打断操作
    }
}

document.getElementById("btn-ncm-start").addEventListener("click", async function() {
    const useCustom = document.getElementById("use-custom-api-url").checked;
    if (useCustom) {
        showToast("自定义URL模式下不可操作内置服务", "提示");
        return;
    }
    const btn = this;
    btn.disabled = true;
    ncmStatusBadge.className = "badge bg-warning";
    ncmStatusBadge.textContent = "启动中";
    try {
        // 首次启动最长可达 60s，覆盖默认 15s 超时
        const resp = await api("/api/ncm/start", { method: "POST", timeout: 70000 });
        showToast(resp.msg, "成功");
    } catch (e) {
        showToast(e.message, "错误");
    } finally {
        btn.disabled = false;
        refreshNcmStatus();
    }
});

document.getElementById("btn-ncm-stop").addEventListener("click", async function() {
    const useCustom = document.getElementById("use-custom-api-url").checked;
    if (useCustom) {
        showToast("自定义URL模式下不可操作内置服务", "提示");
        return;
    }
    const btn = this;
    btn.disabled = true;
    try {
        const resp = await api("/api/ncm/stop", { method: "POST" });
        showToast(resp.msg, "成功");
    } catch (e) {
        showToast(e.message, "错误");
    } finally {
        btn.disabled = false;
        refreshNcmStatus();
    }
});

// 3s 轮询状态
refreshNcmStatus();
setInterval(refreshNcmStatus, 3000);

// ======================================================================
// QQ音乐API服务状态轮询与启停
// ======================================================================
const qqStatusBadge = document.getElementById("qq-status-badge");
const qqStatusDetail = document.getElementById("qq-status-detail");

function renderQqStatus(data) {
    // 自定义URL模式下显示特殊状态
    const useCustom = document.getElementById("use-custom-qq-api-url").checked;
    const portInput = document.querySelector('[name="qq_api_port"]');
    if (useCustom) {
        qqStatusBadge.className = "badge bg-info";
        qqStatusBadge.textContent = "自定义URL";
        qqStatusDetail.textContent = "当前使用自定义API服务URL，内置服务已禁用";
        return;
    }
    if (!data) {
        qqStatusBadge.className = "badge bg-secondary";
        qqStatusBadge.textContent = "未知";
        qqStatusDetail.textContent = "";
        return;
    }
    if (data.running) {
        qqStatusBadge.className = "badge bg-success";
        qqStatusBadge.textContent = "运行中";
        // 运行中禁止修改端口
        portInput.disabled = true;
        portInput.title = "请先停止API服务再修改端口";
    } else {
        qqStatusBadge.className = "badge bg-danger";
        qqStatusBadge.textContent = "已停止";
        // 停止后允许修改端口
        portInput.disabled = false;
        portInput.title = "";
    }
    const parts = [];
    if (data.port) parts.push(`端口 ${data.port}`);
    if (data.preferred_port && data.preferred_port !== data.port) parts.push(`配置端口 ${data.preferred_port}`);
    if (data.pid) parts.push(`PID ${data.pid}`);
    if (data.bin_exists === false) parts.push("未找到二进制");
    qqStatusDetail.textContent = parts.join(" · ");
}

async function refreshQqStatus() {
    try {
        const resp = await api("/api/qq/status", { timeout: 5000 });
        renderQqStatus(resp.data);
    } catch (e) {
        // 轮询失败保持上次状态即可，不打断操作
    }
}

document.getElementById("btn-qq-start").addEventListener("click", async function() {
    const useCustom = document.getElementById("use-custom-qq-api-url").checked;
    if (useCustom) {
        showToast("自定义URL模式下不可操作内置服务", "提示");
        return;
    }
    const btn = this;
    btn.disabled = true;
    qqStatusBadge.className = "badge bg-warning";
    qqStatusBadge.textContent = "启动中";
    try {
        // 首次启动需自解压，最长可达 60s，覆盖默认 15s 超时
        const resp = await api("/api/qq/start", { method: "POST", timeout: 70000 });
        showToast(resp.msg, "成功");
    } catch (e) {
        showToast(e.message, "错误");
    } finally {
        btn.disabled = false;
        refreshQqStatus();
    }
});

document.getElementById("btn-qq-stop").addEventListener("click", async function() {
    const useCustom = document.getElementById("use-custom-qq-api-url").checked;
    if (useCustom) {
        showToast("自定义URL模式下不可操作内置服务", "提示");
        return;
    }
    const btn = this;
    btn.disabled = true;
    try {
        const resp = await api("/api/qq/stop", { method: "POST" });
        showToast(resp.msg, "成功");
    } catch (e) {
        showToast(e.message, "错误");
    } finally {
        btn.disabled = false;
        refreshQqStatus();
    }
});

// 3s 轮询状态
refreshQqStatus();
setInterval(refreshQqStatus, 3000);

// ======================================================================
// 酷狗音乐API服务状态轮询与启停
// ======================================================================
const kugouStatusBadge = document.getElementById("kugou-status-badge");
const kugouStatusDetail = document.getElementById("kugou-status-detail");

function renderKugouStatus(data) {
    // 自定义URL模式下显示特殊状态
    const useCustom = document.getElementById("use-custom-kugou-api-url").checked;
    const portInput = document.querySelector('[name="kugou_api_port"]');
    if (useCustom) {
        kugouStatusBadge.className = "badge bg-info";
        kugouStatusBadge.textContent = "自定义URL";
        kugouStatusDetail.textContent = "当前使用自定义API服务URL，内置服务已禁用";
        return;
    }
    if (!data) {
        kugouStatusBadge.className = "badge bg-secondary";
        kugouStatusBadge.textContent = "未知";
        kugouStatusDetail.textContent = "";
        return;
    }
    if (data.running) {
        kugouStatusBadge.className = "badge bg-success";
        kugouStatusBadge.textContent = "运行中";
        // 运行中禁止修改端口
        portInput.disabled = true;
        portInput.title = "请先停止API服务再修改端口";
    } else {
        kugouStatusBadge.className = "badge bg-danger";
        kugouStatusBadge.textContent = "已停止";
        // 停止后允许修改端口
        portInput.disabled = false;
        portInput.title = "";
    }
    const parts = [];
    if (data.port) parts.push(`端口 ${data.port}`);
    if (data.preferred_port && data.preferred_port !== data.port) parts.push(`配置端口 ${data.preferred_port}`);
    if (data.pid) parts.push(`PID ${data.pid}`);
    if (data.bin_exists === false) parts.push("未找到二进制");
    kugouStatusDetail.textContent = parts.join(" · ");
}

async function refreshKugouStatus() {
    try {
        const resp = await api("/api/kugou/status", { timeout: 5000 });
        renderKugouStatus(resp.data);
    } catch (e) {
        // 轮询失败保持上次状态即可，不打断操作
    }
}

document.getElementById("btn-kugou-start").addEventListener("click", async function() {
    const useCustom = document.getElementById("use-custom-kugou-api-url").checked;
    if (useCustom) {
        showToast("自定义URL模式下不可操作内置服务", "提示");
        return;
    }
    const btn = this;
    btn.disabled = true;
    kugouStatusBadge.className = "badge bg-warning";
    kugouStatusBadge.textContent = "启动中";
    try {
        // 首次启动最长可达 60s，覆盖默认 15s 超时
        const resp = await api("/api/kugou/start", { method: "POST", timeout: 70000 });
        showToast(resp.msg, "成功");
    } catch (e) {
        showToast(e.message, "错误");
    } finally {
        btn.disabled = false;
        refreshKugouStatus();
    }
});

document.getElementById("btn-kugou-stop").addEventListener("click", async function() {
    const useCustom = document.getElementById("use-custom-kugou-api-url").checked;
    if (useCustom) {
        showToast("自定义URL模式下不可操作内置服务", "提示");
        return;
    }
    const btn = this;
    btn.disabled = true;
    try {
        const resp = await api("/api/kugou/stop", { method: "POST" });
        showToast(resp.msg, "成功");
    } catch (e) {
        showToast(e.message, "错误");
    } finally {
        btn.disabled = false;
        refreshKugouStatus();
    }
});

// 3s 轮询状态
refreshKugouStatus();
setInterval(refreshKugouStatus, 3000);
