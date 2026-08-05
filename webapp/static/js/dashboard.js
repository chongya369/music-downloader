// 总览页逻辑

// 加载统计数据
async function loadStats() {
    try {
        const data = await api("/api/stats");
        const s = data.data;
        document.getElementById("stat-total").textContent = s.total;
        document.getElementById("stat-success").textContent = s.success;
        document.getElementById("stat-failed").textContent = s.failed;
        document.getElementById("stat-today").textContent = s.today;
        document.getElementById("stat-active-playlists").textContent = s.active_playlists;
        document.getElementById("stat-total-playlists").textContent = s.total_playlists;
    } catch (e) {
        console.error("加载统计失败:", e);
    }
}

// 加载任务列表
async function loadTasks() {
    try {
        const data = await api("/api/tasks");
        const tasks = data.data;
        const list = document.getElementById("task-list");
        const countEl = document.getElementById("task-count");

        if (!tasks || tasks.length === 0) {
            list.innerHTML = '<p class="text-muted text-center mb-0">暂无下载任务</p>';
            countEl.textContent = "0 个任务";
            // 注意：导航栏的同步指示器由 app.js 的全局轮询负责更新
            return;
        }

        countEl.textContent = tasks.length + " 个任务";
        // 注意：导航栏的同步指示器由 app.js 的全局轮询负责更新

        list.innerHTML = tasks.map(t => {
            const pct = t.progress || 0;
            const status = t.status === "downloading" ? "下载中" : "等待中";
            return `
                <div class="task-item">
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

// 同步全部
document.getElementById("btn-sync-all").addEventListener("click", async function() {
    const btn = this;
    btn.disabled = true;
    btn.innerHTML = '<span class="loading-spinner"></span> 同步中...';
    try {
        const data = await api("/api/sync-all", { method: "POST" });
        showToast(data.msg, "同步结果");
    } catch (e) {
        showToast(e.message, "错误");
    } finally {
        btn.disabled = false;
        btn.innerHTML = '<i class="bi bi-cloud-arrow-down"></i> 立即同步';
    }
});

// 加载账号状态
async function loadAccountsStats() {
    try {
        const data = await api("/api/accounts/stats");
        const list = data.data || [];
        const container = document.getElementById("accounts-stats");
        if (list.length === 0) {
            container.innerHTML = '<div class="col-12 text-center text-muted">暂无启用账号，请到「账号管理」添加</div>';
            return;
        }
        container.innerHTML = list.map(a => {
            const quotaText = a.unlimited
                ? `${a.monthly_downloaded} 首（不限）`
                : `${a.monthly_downloaded} / ${a.quota_limit} 首`;
            const pct = a.unlimited ? 0 : (a.quota_limit > 0 ? Math.min(100, a.monthly_downloaded * 100 / a.quota_limit) : 0);
            const barColor = a.unlimited ? "bg-success" : (pct >= 100 ? "bg-danger" : (pct >= 80 ? "bg-warning" : "bg-success"));
            return `
                <div class="col-md-4 col-sm-6">
                    <div class="card border-light">
                        <div class="card-body p-3">
                            <div class="d-flex justify-content-between align-items-center mb-1">
                                <strong>${a.name}</strong>
                                <span class="badge ${a.vip_type > 0 ? 'bg-warning' : 'bg-secondary'}">${a.vip_text}</span>
                            </div>
                            <small class="text-muted d-block mb-2">${a.nickname || '未获取昵称'}</small>
                            <div class="d-flex justify-content-between mb-1">
                                <small>本月已下载</small>
                                <small class="fw-bold">${quotaText}</small>
                            </div>
                            ${a.unlimited ? '' : `<div class="progress" style="height:6px"><div class="progress-bar ${barColor}" style="width:${pct}%"></div></div>`}
                        </div>
                    </div>
                </div>
            `;
        }).join("");
    } catch (e) {
        console.error("加载账号状态失败:", e);
    }
}

// 同步全部
document.getElementById("btn-sync-all").addEventListener("click", async function() {
    const btn = this;
    btn.disabled = true;
    btn.innerHTML = '<span class="loading-spinner"></span> 同步中...';
    try {
        const data = await api("/api/sync-all", { method: "POST" });
        showToast(data.msg, "同步结果");
    } catch (e) {
        showToast(e.message, "错误");
    } finally {
        btn.disabled = false;
        btn.innerHTML = '<i class="bi bi-cloud-arrow-down"></i> 立即同步';
    }
});

// 初始化
loadStats();
loadAccountsStats();
loadTasks();
// 每 2 秒刷新任务
setInterval(loadTasks, 2000);
// 每 30 秒刷新统计和账号状态
setInterval(loadStats, 30000);
setInterval(loadAccountsStats, 30000);
