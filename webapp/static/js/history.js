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

// 任务项骨架（名称/歌手静态渲染一次，动态字段由 updateTaskItem 更新）
function buildTaskItem(t) {
    const div = document.createElement("div");
    div.className = "task-item mb-2";
    div.dataset.pk = t.pk;
    // 操作按钮一次性建好，状态/文案由 updateTaskItem 按需刷新；
    // 点击事件统一走 #task-list 上的事件委托（见文件末尾），
    // 避免增量更新后重复绑定导致一次点击触发多次请求
    div.innerHTML = `
        <div class="d-flex justify-content-between align-items-center mb-1">
            <span class="text-truncate me-2">${escapeHtml(t.artists)} - ${escapeHtml(t.song_name)}</span>
            <span class="d-flex align-items-center gap-2 flex-shrink-0">
                <span class="badge task-status"></span>
                <button class="btn btn-sm btn-outline-secondary task-toggle" data-action="pause">
                    <i class="bi bi-pause-fill"></i> 暂停
                </button>
                <button class="btn btn-sm btn-outline-danger task-del" data-action="delete" title="删除任务">
                    <i class="bi bi-trash"></i>
                </button>
            </span>
        </div>
        <div class="task-error"></div>
        <div class="progress">
            <div class="progress-bar" style="width: 0%">0%</div>
        </div>
    `;
    return div;
}

// 任务状态 -> 徽章样式（paused 为 0.7.0 新增：用户暂停）
const TASK_STATUS_META = {
    downloading: { text: "下载中", cls: "bg-primary" },
    pending: { text: "等待中", cls: "bg-info" },
    paused: { text: "已暂停", cls: "bg-secondary" },
};

// 更新任务项动态字段（状态徽章/错误信息/进度条/操作按钮）
function updateTaskItem(el, t) {
    const pct = t.progress || 0;
    const meta = TASK_STATUS_META[t.status] || { text: t.status, cls: "bg-secondary" };
    const badge = el.querySelector(".task-status");
    badge.className = `badge task-status ${meta.cls}`;
    badge.textContent = meta.text;
    el.querySelector(".task-error").innerHTML = t.error_msg
        ? `<small class="text-warning d-block mb-1">${escapeHtml(t.error_msg)}</small>`
        : "";

    // 暂停/继续按钮随状态切换。状态可能由「暂停全部」或另一个浏览器页签改变，
    // 故每次轮询都按服务端状态校正；仅在确实变化时改 DOM，避免轮询抖动
    const toggle = el.querySelector(".task-toggle");
    const paused = t.status === "paused";
    const action = paused ? "resume" : "pause";
    if (toggle.dataset.action !== action) {
        toggle.dataset.action = action;
        toggle.innerHTML = paused
            ? '<i class="bi bi-play-fill"></i> 继续'
            : '<i class="bi bi-pause-fill"></i> 暂停';
        toggle.classList.toggle("btn-outline-success", paused);
        toggle.classList.toggle("btn-outline-secondary", !paused);
    }

    const bar = el.querySelector(".progress-bar");
    bar.style.width = pct + "%";
    bar.textContent = pct + "%";
}

// 请求进行中禁用该任务项的所有按钮，避免重复点击产生并发请求
function setTaskItemBusy(el, busy) {
    el.querySelectorAll("button").forEach(b => { b.disabled = busy; });
    el.classList.toggle("opacity-50", busy);
}

// 加载任务列表（增量 DOM 更新：高频轮询下避免全量重建导致闪烁卡顿）
let _loadingTasks = false;
async function loadTasks() {
    if (_loadingTasks) return;  // 防重入：上一次请求未返回时跳过本轮
    _loadingTasks = true;
    try {
        const data = await api("/api/tasks");
        const tasks = data.data || [];
        const list = document.getElementById("task-list");
        const countBadge = document.getElementById("task-count-badge");

        countBadge.textContent = tasks.length;

        // 全局按钮可用性：无"可暂停"任务时禁用暂停全部；有已暂停任务或
        // 处于全局暂停态时启用继续全部（便于清除全局标记）
        const hasRunnable = tasks.some(t => t.status !== "paused");
        const hasPaused = tasks.some(t => t.status === "paused");
        document.getElementById("btn-pause-all").disabled = !hasRunnable;
        document.getElementById("btn-resume-all").disabled = !(hasPaused || data.paused_all);

        if (tasks.length === 0) {
            list.innerHTML = '<p class="text-muted text-center mb-0">暂无下载任务</p>';
            return;
        }

        // 清掉空态占位符（p 标签不是任务项）
        if (!list.querySelector(".task-item")) list.innerHTML = "";

        // 已渲染任务项索引：pk -> 元素
        const existing = new Map();
        list.querySelectorAll(".task-item").forEach(el => existing.set(el.dataset.pk, el));

        // 移除已结束（消失）的任务项
        const keep = new Set(tasks.map(t => String(t.pk)));
        existing.forEach((el, pk) => {
            if (!keep.has(pk)) el.remove();
        });

        // 更新已有项 / 追加新项（保持后端返回顺序）
        tasks.forEach(t => {
            let el = existing.get(String(t.pk));
            if (!el) {
                el = buildTaskItem(t);
                list.appendChild(el);
            }
            updateTaskItem(el, t);
        });
    } catch (e) {
        console.error("加载任务失败:", e);
    } finally {
        _loadingTasks = false;
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
                // 带平台提交：Song 是 (id, platform) 复合主键，同 id 双平台并存时
                // 仅按 id 重试会误命中另一平台的同号歌
                actions.push(`<button class="btn btn-sm btn-outline-warning btn-retry" data-id="${s.id}" data-platform="${escapeHtml(platform)}"><i class="bi bi-arrow-clockwise"></i> 重试</button>`);
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
            // platform 一并提交（空串 = 全部平台，与旧行为一致）
            const platform = this.dataset.platform || "";
            try {
                const data = await api("/api/retry", {
                    method: "POST",
                    body: JSON.stringify({ song_ids: [id], platform }),
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

// ============================================================
// 任务控制：暂停 / 继续 / 删除（单任务 + 批量）
// ============================================================
// 事件委托：任务项由轮询增量创建/销毁，逐个绑定会重复或者丢失；
// 统一挂在容器上，一次绑定长期有效
document.getElementById("task-list").addEventListener("click", async e => {
    const btn = e.target.closest("[data-action]");
    if (!btn || btn.disabled) return;
    const item = btn.closest(".task-item");
    if (!item) return;

    const pk = item.dataset.pk;
    const action = btn.dataset.action;
    if (action === "delete") {
        if (!confirm("确定删除该下载任务？\n正在下载的任务会立即停止，已下载的临时文件会被清理。")) return;
    }

    setTaskItemBusy(item, true);
    try {
        const data = action === "delete"
            ? await api(`/api/tasks/${pk}`, { method: "DELETE" })
            : await api(`/api/tasks/${pk}/${action}`, { method: "POST" });
        showToast(data.msg, "下载任务");
        await loadTasks();
    } catch (err) {
        showToast(err.message, "错误");
    } finally {
        // loadTasks 可能已移除该元素（删除成功），hasAttribute 判断避免操作脱离节点
        if (item.isConnected) setTaskItemBusy(item, false);
    }
});

// 暂停全部：当前下载中的会立即停止，已下载部分保留（继续时断点续传）
document.getElementById("btn-pause-all").addEventListener("click", async function() {
    if (!confirm("确定暂停全部下载任务？\n当前正在下载的任务会立即停止，已下载部分保留，继续时可断点续传。")) return;
    this.disabled = true;
    try {
        const data = await api("/api/tasks/pause-all", { method: "POST" });
        showToast(data.msg, "暂停");
        await loadTasks();          // 由 loadTasks 重算按钮可用性
    } catch (e) {
        showToast(e.message, "错误");
        this.disabled = false;      // 出错时恢复可点击，交由下次轮询校正
    }
});

// 继续全部
document.getElementById("btn-resume-all").addEventListener("click", async function() {
    this.disabled = true;
    try {
        const data = await api("/api/tasks/resume-all", { method: "POST" });
        showToast(data.msg, "继续");
        await loadTasks();
    } catch (e) {
        showToast(e.message, "错误");
        this.disabled = false;
    }
});

// 初始化
loadTasks();
loadSongs();

// 每 0.5 秒刷新任务（仅在任务标签页时；增量更新避免闪烁）
setInterval(() => {
    if (document.hidden) return;   // 页面在后台时不发无谓请求
    if (currentSubTab === "tasks") {
        loadTasks();
    }
}, 500);
