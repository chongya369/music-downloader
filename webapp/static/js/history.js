// 下载页逻辑（下载任务 + 下载历史）

let currentPage = 1;
let currentSubTab = "tasks";

// 子标签页切换
document.querySelectorAll("#download-tabs .nav-link").forEach(el => {
    el.addEventListener("click", function() {
        currentSubTab = this.dataset.tab;
        const tabTasks = document.getElementById("tab-tasks");
        const tabHistory = document.getElementById("tab-history");
        if (currentSubTab === "tasks") {
            tabTasks.style.display = "";
            tabHistory.style.display = "none";
            loadTasks();
        } else {
            tabTasks.style.display = "none";
            tabHistory.style.display = "";
            loadSongs(1);
        }
    });
});

// 加载任务列表
async function loadTasks() {
    try {
        const data = await api("/api/tasks");
        const tasks = data.data;
        const list = document.getElementById("task-list");
        const countBadge = document.getElementById("task-count-badge");

        if (!tasks || tasks.length === 0) {
            list.innerHTML = '<p class="text-muted text-center mb-0">暂无下载任务</p>';
            countBadge.textContent = "0";
            return;
        }

        countBadge.textContent = tasks.length;

        list.innerHTML = tasks.map(t => {
            const pct = t.progress || 0;
            const status = t.status === "downloading" ? "下载中" : "等待中";
            return `
                <div class="task-item mb-2">
                    <div class="d-flex justify-content-between align-items-center mb-1">
                        <span>${t.artists} - ${t.song_name}</span>
                        <span class="badge ${t.status === 'downloading' ? 'bg-primary' : 'bg-info'}">${status}</span>
                    </div>
                    <div class="progress">
                        <div class="progress-bar" style="width: ${pct}%">${pct}%</div>
                    </div>
                </div>
            `;
        }).join("");
    } catch (e) {
        console.error("加载任务失败:", e);
    }
}

// 加载下载历史
async function loadSongs(page = 1) {
    currentPage = page;
    const status = document.getElementById("filter-status").value;
    const keyword = document.getElementById("filter-keyword").value.trim();
    const perPage = document.getElementById("filter-perpage").value;

    const params = new URLSearchParams({ page, per_page: perPage });
    if (status) params.set("status", status);
    if (keyword) params.set("keyword", keyword);

    try {
        const data = await api("/api/songs?" + params.toString());
        const tbody = document.getElementById("song-tbody");
        const list = data.data;

        const retryBar = document.getElementById("retry-bar");
        if (status === "failed") {
            retryBar.classList.remove("d-none");
        } else {
            retryBar.classList.add("d-none");
        }

        if (!list || list.length === 0) {
            tbody.innerHTML = '<tr><td colspan="9" class="text-center text-muted">无记录</td></tr>';
            renderPagination(0, 1);
            return;
        }

        // 平台样式映射
        const platformStyles = {
            'netease': 'background-color: #C20C0C; color: white;',
            'qq': 'background-color: #31C27C; color: white;',
            'kugou': 'background-color: #0062FF; color: white;',
        };
        const platformNames = {
            'netease': '网易云',
            'qq': 'QQ音乐',
            'kugou': '酷狗音乐',
        };

        tbody.innerHTML = list.map(s => {
            const time = s.downloaded_at || "--";
            const size = formatSize(s.file_size);
            
            // 平台信息
            const platform = s.platform || 'netease';
            const platformName = s.platform_name || platformNames[platform] || platform;
            const platformStyle = platformStyles[platform] || 'background-color: #6c757d; color: white;';
            
            const actions = [];
            let statusCell;
            if (s.status === "failed") {
                const reason = s.error_msg || "未知原因";
                statusCell = `<button class="badge btn btn-danger btn-show-fail"
                    data-name="${escapeHtml(s.name)}"
                    data-artists="${escapeHtml(s.artists)}"
                    data-reason="${escapeHtml(reason)}"
                    title="点击查看失败原因">
                    <i class="bi bi-exclamation-triangle"></i> 失败
                </button>`;
            } else {
                statusCell = statusBadge(s.status);
            }
            if (s.status === "failed") {
                actions.push(`<button class="btn btn-sm btn-outline-warning btn-retry" data-id="${s.id}"><i class="bi bi-arrow-clockwise"></i> 重试</button>`);
            }
            actions.push(`<button class="btn btn-sm btn-outline-danger btn-delete-song" data-id="${s.pk}"><i class="bi bi-trash"></i></button>`);
            return `
                <tr>
                    <td><span class="badge" style="${platformStyle}">${escapeHtml(platformName)}</span></td>
                    <td>${escapeHtml(s.name)}</td>
                    <td>${escapeHtml(s.artists)}</td>
                    <td><small class="text-muted">${escapeHtml(s.playlist_name || '--')}</small></td>
                    <td>${s.quality || '--'}</td>
                    <td>${size}</td>
                    <td><small>${time}</small></td>
                    <td>${statusCell}</td>
                    <td>${actions.join(" ")}</td>
                </tr>
            `;
        }).join("");

        renderPagination(data.total, data.pages);
        bindSongEvents();
    } catch (e) {
        showToast(e.message, "错误");
    }
}

function renderPagination(total, pages) {
    const el = document.getElementById("pagination");
    if (pages <= 1) {
        el.innerHTML = "";
        return;
    }
    let html = "";
    html += `<li class="page-item ${currentPage <= 1 ? 'disabled' : ''}">
        <a class="page-link" href="#" onclick="loadSongs(${currentPage - 1});return false;">&laquo;</a></li>`;
    for (let i = 1; i <= pages; i++) {
        html += `<li class="page-item ${i === currentPage ? 'active' : ''}">
            <a class="page-link" href="#" onclick="loadSongs(${i});return false;">${i}</a></li>`;
    }
    html += `<li class="page-item ${currentPage >= pages ? 'disabled' : ''}">
        <a class="page-link" href="#" onclick="loadSongs(${currentPage + 1});return false;">&raquo;</a></li>`;
    el.innerHTML = html;
}

function bindSongEvents() {
    document.querySelectorAll(".btn-retry").forEach(el => {
        el.addEventListener("click", async function() {
            const id = parseInt(this.dataset.id);
            try {
                const data = await api("/api/retry", {
                    method: "POST",
                    body: JSON.stringify({ song_ids: [id] }),
                });
                showToast(data.msg, "重试");
                loadSongs(currentPage);
            } catch (e) {
                showToast(e.message, "错误");
            }
        });
    });

    document.querySelectorAll(".btn-delete-song").forEach(el => {
        el.addEventListener("click", async function() {
            const id = this.dataset.id;
            if (!confirm("确定删除这条记录？（不删除文件）")) return;
            try {
                await api(`/api/songs/${id}`, { method: "DELETE" });
                showToast("已删除");
                loadSongs(currentPage);
            } catch (e) {
                showToast(e.message, "错误");
            }
        });
    });

    document.querySelectorAll(".btn-show-fail").forEach(el => {
        el.addEventListener("click", function() {
            document.getElementById("fail-song-name").textContent = this.dataset.name;
            document.getElementById("fail-song-artists").textContent = this.dataset.artists;
            document.getElementById("fail-reason-text").textContent = this.dataset.reason;
            bootstrap.Modal.getOrCreateInstance(document.getElementById("fail-reason-modal")).show();
        });
    });
}

// 全部重试
document.getElementById("btn-retry-all").addEventListener("click", async function() {
    if (!confirm("确定重试所有失败歌曲？")) return;
    const btn = this;
    btn.disabled = true;
    try {
        const data = await api("/api/retry", { method: "POST", body: JSON.stringify({}) });
        showToast(data.msg, "重试");
    } catch (e) {
        showToast(e.message, "错误");
    } finally {
        btn.disabled = false;
    }
});

// 查询按钮
document.getElementById("btn-search").addEventListener("click", () => loadSongs(1));

// 回车搜索
document.getElementById("filter-keyword").addEventListener("keypress", e => {
    if (e.key === "Enter") loadSongs(1);
});

// 简单 HTML 转义
function escapeHtml(str) {
    if (str === null || str === undefined) return "";
    return String(str)
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;")
        .replace(/'/g, "&#39;");
}

// 格式化大小
function formatSize(bytes) {
    if (!bytes) return "--";
    if (bytes < 1024) return bytes + " B";
    if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + " KB";
    if (bytes < 1024 * 1024 * 1024) return (bytes / 1024 / 1024).toFixed(1) + " MB";
    return (bytes / 1024 / 1024 / 1024).toFixed(2) + " GB";
}

// 状态徽章
function statusBadge(status) {
    const map = {
        success: '<span class="badge bg-success">成功</span>',
        failed: '<span class="badge bg-danger">失败</span>',
        skipped: '<span class="badge bg-secondary">已下载</span>',
        pending: '<span class="badge bg-warning">等待中</span>',
        downloading: '<span class="badge bg-primary">下载中</span>',
    };
    return map[status] || '<span class="badge bg-secondary">' + status + '</span>';
}

// 初始化
loadTasks();
loadSongs();

// 每 2 秒刷新任务（仅在任务标签页时）
setInterval(() => {
    if (currentSubTab === "tasks") {
        loadTasks();
    }
}, 2000);
