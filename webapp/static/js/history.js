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
            const errMsg = t.error_msg
                ? `<small class="text-warning d-block mb-1">${escapeHtml(t.error_msg)}</small>`
                : "";
            return `
                <div class="task-item mb-2">
                    <div class="d-flex justify-content-between align-items-center mb-1">
                        <span>${escapeHtml(t.artists)} - ${escapeHtml(t.song_name)}</span>
                        <span class="badge ${t.status === 'downloading' ? 'bg-primary' : 'bg-info'}">${status}</span>
                    </div>
                    ${errMsg}
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
        // retry-bar 显示逻辑：只要数据库存在失败记录就显示（不依赖当前筛选状态），
        // 让用户在任何视图下都能看到并清除失败记录
        try {
            const failedData = await api("/api/songs?status=failed&per_page=1");
            if (failedData.total > 0) {
                retryBar.classList.remove("d-none");
            } else {
                retryBar.classList.add("d-none");
            }
        } catch (e) {
            // 失败记录查询失败不影响主列表展示，默认隐藏
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
            actions.push(`<button class="btn btn-sm btn-outline-danger btn-delete-song" data-id="${s.pk}"
                data-status="${s.status}"
                data-name="${escapeHtml(s.name)}"
                data-artists="${escapeHtml(s.artists)}"><i class="bi bi-trash"></i></button>`);
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
    const cur = currentPage;
    // 收集待渲染的分页项：{ p, label, type }
    //   type: "num" 可点击页码 | "prev"/"next" 上下页 | "gap" 省略号
    const items = [];
    const push = (p, label, type) => items.push({ p, label, type });

    push(cur - 1, "«", "prev");

    // 首页
    push(1, "1", "num");
    // 前省略号：当前页左侧距离首页超过一定范围时显示
    if (cur - 3 > 2) push(null, "…", "gap");

    // 当前页前后各 2 个页码（夹在首页与末页之间）
    const start = Math.max(2, cur - 2);
    const end = Math.min(pages - 1, cur + 2);
    for (let i = start; i <= end; i++) push(i, String(i), "num");

    // 后省略号：当前页右侧距离末页超过一定范围时显示
    if (cur + 3 < pages - 1) push(null, "…", "gap");

    // 末页
    if (pages > 1) push(pages, String(pages), "num");

    push(cur + 1, "»", "next");

    el.innerHTML = items.map(it => {
        if (it.type === "gap") {
            return `<li class="page-item disabled"><span class="page-link">${it.label}</span></li>`;
        }
        const disabled = (it.type === "prev" && cur <= 1) || (it.type === "next" && cur >= pages);
        if (disabled) {
            return `<li class="page-item disabled"><span class="page-link">${it.label}</span></li>`;
        }
        const active = it.p === cur ? "active" : "";
        return `<li class="page-item ${active}"><a class="page-link" href="#" onclick="loadSongs(${it.p});return false;">${it.label}</a></li>`;
    }).join("");
}

function bindSongEvents() {
    document.querySelectorAll(".btn-retry").forEach(el => {
        el.addEventListener("click", async function() {
            // song_id 字符串透传（QQ songmid 为非数字字符串，parseInt 会截断）
            const id = this.dataset.id;
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
        el.addEventListener("click", function() {
            const id = this.dataset.id;
            if (this.dataset.status === "success") {
                // 成功记录：弹窗询问是否同时删除音乐文件
                document.getElementById("delete-song-name").textContent = this.dataset.name || "";
                document.getElementById("delete-song-artists").textContent = this.dataset.artists || "";
                deleteTarget = { id };
                bootstrap.Modal.getOrCreateInstance(document.getElementById("delete-song-modal")).show();
            } else {
                // 失败/跳过记录：无有效音乐文件，直接确认删除
                if (!confirm("确定删除这条记录？")) return;
                handleDeleteSong(id, false);
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

// 删除下载记录（deleteFile=true 时同时删除本地音乐文件）
let deleteTarget = null; // 待删除记录 {id}

async function handleDeleteSong(id, deleteFile) {
    try {
        const url = deleteFile ? `/api/songs/${id}?delete_file=1` : `/api/songs/${id}`;
        const data = await api(url, { method: "DELETE" });
        showToast(data.msg || "已删除", "删除");
        loadSongs(currentPage);
    } catch (e) {
        showToast(e.message, "错误");
    }
}

// 删除确认弹窗按钮（弹窗为静态节点，绑定一次即可）
document.getElementById("btn-delete-record-only").addEventListener("click", function() {
    bootstrap.Modal.getOrCreateInstance(document.getElementById("delete-song-modal")).hide();
    if (deleteTarget) handleDeleteSong(deleteTarget.id, false);
    deleteTarget = null;
});
document.getElementById("btn-delete-with-file").addEventListener("click", function() {
    bootstrap.Modal.getOrCreateInstance(document.getElementById("delete-song-modal")).hide();
    if (deleteTarget) handleDeleteSong(deleteTarget.id, true);
    deleteTarget = null;
});

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

// 清除所有失败记录
document.getElementById("btn-clear-failed").addEventListener("click", async function() {
    if (!confirm("确定清除所有失败记录？此操作不可恢复，清除后这些歌曲可重新下载。")) return;
    const btn = this;
    btn.disabled = true;
    try {
        const data = await api("/api/songs/failed", { method: "DELETE" });
        showToast(data.msg, "清除");
        loadSongs(currentPage);
    } catch (e) {
        showToast(e.message, "错误");
    } finally {
        btn.disabled = false;
    }
});

// 回车搜索
document.getElementById("filter-keyword").addEventListener("keypress", e => {
    if (e.key === "Enter") loadSongs(1);
});

// escapeHtml / formatSize / statusBadge 已收敛至全局 app.js（L8），
// 此处不再定义本地副本。

// 初始化
loadTasks();
loadSongs();

// 每 2 秒刷新任务（仅在任务标签页时）
setInterval(() => {
    if (currentSubTab === "tasks") {
        loadTasks();
    }
}, 2000);
