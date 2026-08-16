// 全局工具函数

// 显示 toast 提示
function showToast(msg, title = "提示") {
    const toastEl = document.getElementById("global-toast");
    document.getElementById("toast-title").textContent = title;
    document.getElementById("toast-body").textContent = msg;
    const toast = bootstrap.Toast.getOrCreateInstance(toastEl, { delay: 3000 });
    toast.show();
}

// API 请求封装（默认 15 秒超时，401 自动跳转登录；options.timeout 可覆盖）
async function api(url, options = {}) {
    const controller = new AbortController();
    const timeoutMs = options.timeout || 15000;
    const timer = setTimeout(() => controller.abort(), timeoutMs);
    try {
        const resp = await fetch(url, {
            headers: { "Content-Type": "application/json" },
            ...options,
            signal: controller.signal,
        });
        // 401 未登录：跳转登录页
        if (resp.status === 401) {
            window.location.href = "/login";
            return new Promise(() => {});  // 永不 resolve，避免后续逻辑报错
        }
        // 403 无权限：抛错提示
        if (resp.status === 403) {
            throw new Error("无权限执行此操作");
        }
        const data = await resp.json();
        // 业务层 401 也跳转登录（兼容后端返回 200+code:401 的情况）
        if (data.code === 401) {
            window.location.href = "/login";
            return new Promise(() => {});
        }
        if (data.code !== 0) {
            throw new Error(data.msg || "请求失败");
        }
        return data;
    } catch (e) {
        if (e.name === "AbortError") {
            throw new Error("请求超时，请检查网络或服务状态");
        }
        throw e;
    } finally {
        clearTimeout(timer);
    }
}

// 文件大小格式化
function formatSize(bytes) {
    if (!bytes) return "0 B";
    const units = ["B", "KB", "MB", "GB"];
    let i = 0;
    while (bytes >= 1024 && i < units.length - 1) {
        bytes /= 1024;
        i++;
    }
    return bytes.toFixed(1) + " " + units[i];
}

// 时长格式化
function formatDuration(ms) {
    if (!ms) return "--";
    const s = Math.floor(ms / 1000);
    const m = Math.floor(s / 60);
    return m + ":" + String(s % 60).padStart(2, "0");
}

// 状态徽章
function statusBadge(status) {
    const map = {
        success: '<span class="badge bg-success">成功</span>',
        failed: '<span class="badge bg-danger">失败</span>',
        skipped: '<span class="badge bg-info">已下载</span>',
        pending: '<span class="badge bg-info">等待</span>',
        downloading: '<span class="badge bg-primary">下载中</span>',
        done: '<span class="badge bg-success">完成</span>',
    };
    return map[status] || '<span class="badge bg-secondary">' + status + "</span>";
}

// 更新同步指示器
function updateSyncIndicator(active) {
    const el = document.getElementById("sync-indicator");
    if (!el) return;
    if (active) {
        el.innerHTML = '<span class="badge bg-success sync-active">下载中...</span>';
    } else {
        el.innerHTML = '<span class="badge bg-secondary">空闲</span>';
    }
}

// ============================================================
// 全局下载状态轮询（所有页面通用）
// ============================================================
// 任何页面都会显示导航栏的"下载中/空闲"指示器，
// 因此轮询放在全局 app.js 中，而不是只在 dashboard.js 里。
async function refreshGlobalTaskStatus() {
    try {
        const data = await api("/api/tasks");
        const tasks = data.data || [];
        updateSyncIndicator(tasks.length > 0);
    } catch (e) {
        // 静默失败：指示器是辅助信息，不应弹错提示
        console.error("刷新任务状态失败:", e);
    }
}

// DOM 就绪后启动轮询（兼容已加载完和未加载完两种情况）
function startGlobalStatusPolling() {
    // 立即刷新一次，避免页面初始的"加载中..."停留过久
    refreshGlobalTaskStatus();
    // 每 2 秒刷新一次（与原 dashboard.js 间隔一致）
    setInterval(refreshGlobalTaskStatus, 2000);
}

if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", startGlobalStatusPolling);
} else {
    startGlobalStatusPolling();
}
