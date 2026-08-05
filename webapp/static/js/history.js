// 下载历史页逻辑

let currentPage = 1;

// 加载列表
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

        // 失败重试栏
        const retryBar = document.getElementById("retry-bar");
        if (status === "failed") {
            retryBar.classList.remove("d-none");
        } else {
            retryBar.classList.add("d-none");
        }

        if (!list || list.length === 0) {
            tbody.innerHTML = '<tr><td colspan="8" class="text-center text-muted">无记录</td></tr>';
            renderPagination(0, 1);
            return;
        }

        tbody.innerHTML = list.map(s => {
            const time = s.downloaded_at || "--";
            const size = formatSize(s.file_size);
            const actions = [];
            // 状态徽章：失败状态渲染为可点击按钮
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

        // 绑定按钮事件
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
    // 上一页
    html += `<li class="page-item ${currentPage <= 1 ? 'disabled' : ''}">
        <a class="page-link" href="#" onclick="loadSongs(${currentPage - 1});return false;">&laquo;</a></li>`;
    // 页码
    for (let i = 1; i <= pages; i++) {
        html += `<li class="page-item ${i === currentPage ? 'active' : ''}">
            <a class="page-link" href="#" onclick="loadSongs(${i});return false;">${i}</a></li>`;
    }
    // 下一页
    html += `<li class="page-item ${currentPage >= pages ? 'disabled' : ''}">
        <a class="page-link" href="#" onclick="loadSongs(${currentPage + 1});return false;">&raquo;</a></li>`;
    el.innerHTML = html;
}

function bindSongEvents() {
    // 单首重试
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

    // 删除记录
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

    // 点击失败徽章查看失败原因
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

// 初始化
loadSongs();
