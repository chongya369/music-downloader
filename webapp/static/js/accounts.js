// 账号管理页逻辑

// 当前选中的平台（'all' / 'netease' / 'qq' / 'kugou'）
let currentPlatform = "all";
// 全量账号缓存（用于 Tab 切换时过滤）
let allAccounts = [];

// 平台中文名映射
const PLATFORM_NAMES = { netease: "网易云", qq: "QQ音乐", kugou: "酷狗音乐" };
const PLATFORM_COLORS = { netease: "#C20C0C", qq: "#31C27C", kugou: "#0062FF" };

// 导出账号信息（含 cookie，敏感；按钮仅管理员可见，需判存在性——
// 普通用户页面无此元素，无条件绑定会使整个 accounts.js 崩溃）
const exportBtn = document.getElementById("btn-export-accounts");
if (exportBtn) exportBtn.addEventListener("click", function() {
    if (!confirm("导出文件包含 Cookie 等敏感信息，请妥善保管。是否继续？")) return;
    window.location.href = "/api/accounts/export";
});

// 导入账号信息
document.getElementById("btn-import-accounts").addEventListener("click", function() {
    document.getElementById("import-file-input").click();
});

document.getElementById("import-file-input").addEventListener("change", async function(e) {
    const file = e.target.files[0];
    if (!file) return;
    try {
        const text = await file.text();
        const payload = JSON.parse(text);
        const count = (payload.accounts || []).length;
        if (count === 0) {
            showToast("文件中没有可导入的账号");
            this.value = "";
            return;
        }
        if (!confirm(`即将导入 ${count} 个账号（同平台+同名账号将自动跳过）。是否继续？`)) {
            this.value = "";
            return;
        }
        const data = await api("/api/accounts/import", {
            method: "POST",
            body: JSON.stringify(payload),
        });
        showToast(data.msg, "导入结果");
        loadAccounts();
    } catch (err) {
        showToast("导入失败：" + err.message, "错误");
    } finally {
        this.value = "";
    }
});

// 绑定平台 Tab 切换事件
document.querySelectorAll("#platform-tabs .nav-link").forEach(el => {
    el.addEventListener("click", function() {
        currentPlatform = this.dataset.platform;
        renderAccounts(allAccounts);
    });
});

// 加载账号列表
async function loadAccounts() {
    try {
        const data = await api("/api/accounts");
        allAccounts = data.data || [];

        // 更新各平台 Tab 上的计数
        const counts = { all: allAccounts.length, netease: 0, qq: 0, kugou: 0 };
        allAccounts.forEach(a => {
            if (counts[a.platform] !== undefined) counts[a.platform]++;
        });
        document.getElementById("count-all").textContent = counts.all;
        document.getElementById("count-netease").textContent = counts.netease;
        document.getElementById("count-qq").textContent = counts.qq;
        document.getElementById("count-kugou").textContent = counts.kugou;

        renderAccounts(allAccounts);
    } catch (e) {
        showToast(e.message, "错误");
    }
}

// 渲染账号列表（按 currentPlatform 过滤）
function renderAccounts(list) {
    const isAll = currentPlatform === "all";
    const filtered = isAll
        ? list
        : list.filter(a => a.platform === currentPlatform);

    const cardView = document.getElementById("accounts-card-view");
    const tableView = document.getElementById("accounts-table-view");

    if (!cardView || !tableView) return;

    if (isAll) {
        cardView.style.display = "";
        tableView.style.display = "none";
        renderCards(filtered);
    } else {
        cardView.style.display = "none";
        tableView.style.display = "";
        renderTable(filtered);
    }
}

// 卡片视图（全部标签页，按平台分组显示）
function renderCards(list) {
    const container = document.getElementById("accounts-cards");
    if (!container) return;

    if (!list || list.length === 0) {
        container.innerHTML = '<div class="text-center text-muted py-3">暂无账号，点击右上角添加</div>';
        return;
    }

    const PLATFORM_ORDER = ["netease", "qq", "kugou"];
    const grouped = {};
    list.forEach(a => {
        if (!grouped[a.platform]) grouped[a.platform] = [];
        grouped[a.platform].push(a);
    });

    container.innerHTML = PLATFORM_ORDER.filter(p => grouped[p]).map(platform => {
        const items = grouped[platform];
        const platformName = PLATFORM_NAMES[platform] || platform;
        const platformColor = PLATFORM_COLORS[platform] || "#6c757d";

        const cardsHtml = items.map(a => {
            const enabled = a.enabled;
            const quotaText = a.quota_limit > 0
                ? `${a.monthly_downloaded} / ${a.quota_limit}`
                : `${a.monthly_downloaded}（不限）`;
            const quotaColor = a.quota_limit > 0 && a.monthly_downloaded >= a.quota_limit
                ? "color:#dc3545;" : "color:#198754;";

            let expireText;
            if (a.vip_type > 0) {
                if (a.vip_expire_at) {
                    const d = a.vip_expire_at.split(" ")[0];
                    const expireDate = new Date(d);
                    const today = new Date();
                    today.setHours(0, 0, 0, 0);
                    const expired = expireDate < today;
                    expireText = `<span style="color:${expired ? '#dc3545' : '#198754'}">${d}</span>`;
                } else {
                    expireText = '<span style="color:#6c757d;">未知</span>';
                }
            } else {
                expireText = '<span style="color:#6c757d;">—</span>';
            }

            return `
                <div class="account-card-col">
                    <div class="card account-card">
                        <div class="card-body">
                            <div class="d-flex align-items-center gap-2 mb-2">
                                <span class="badge" style="background-color:${platformColor};color:#ffffff;font-size:0.75rem;">${platformName}</span>
                                <span class="fw-semibold text-truncate" style="font-size:0.875rem;" title="${escapeHtml(a.name)}">${escapeHtml(a.name)}</span>
                            </div>
                            <hr class="my-1" style="opacity:0.5;">
                            <div class="d-flex align-items-center gap-2 mb-1" style="font-size:0.8rem;">
                                <span class="badge ${a.vip_type > 0 ? 'bg-warning' : 'bg-secondary'}" style="font-size:0.7rem;">${a.vip_text}</span>
                                <span style="color:#6c757d;">到期</span>
                                ${expireText}
                            </div>
                            <div class="d-flex align-items-center gap-2" style="font-size:0.8rem;">
                                <span style="color:${enabled ? '#198754' : '#6c757d'};">${enabled ? '● 已启用' : '○ 已禁用'}</span>
                                <span style="color:#dee2e6;">|</span>
                                <span style="${quotaColor};">${quotaText}</span>
                            </div>
                        </div>
                    </div>
                </div>
            `;
        }).join("");

        return `
            <div class="platform-group mb-3">
                <div class="d-flex align-items-center gap-2 mb-2 px-1">
                    <span class="fw-bold" style="font-size:0.95rem;">${platformName}</span>
                    <span class="text-muted" style="font-size:0.8rem;">共 ${items.length} 个账号</span>
                </div>
                <div class="row g-2">${cardsHtml}</div>
            </div>
        `;
    }).join("");
}

// 表格视图（平台标签页，可编辑）
function renderTable(list) {
    const tbody = document.getElementById("account-tbody");

    if (!list || list.length === 0) {
        tbody.innerHTML = '<tr><td colspan="10" class="text-center text-muted">暂无账号，点击右上角添加</td></tr>';
        bindEvents();
        return;
    }

    tbody.innerHTML = list.map((a, idx) => {
        const checked = a.enabled ? "checked" : "";
        const checkTime = a.last_check_at || "从未校验";
        const platformName = a.platform_name || PLATFORM_NAMES[a.platform] || a.platform;
        const platformColor = PLATFORM_COLORS[a.platform] || "#6c757d";
        const quotaText = a.quota_limit > 0
            ? `${a.monthly_downloaded} / ${a.quota_limit}`
            : `${a.monthly_downloaded}（不限）`;
        const quotaColor = a.quota_limit > 0 && a.monthly_downloaded >= a.quota_limit
            ? "text-danger" : "text-success";

        let expireText;
        if (a.vip_type > 0) {
            if (a.vip_expire_at) {
                const d = a.vip_expire_at.split(" ")[0];
                const expireDate = new Date(d);
                const today = new Date();
                today.setHours(0, 0, 0, 0);
                const expired = expireDate < today;
                expireText = `<span class="${expired ? 'text-danger' : 'text-success'}">${d}</span>`;
            } else {
                expireText = '<span class="text-muted">未知</span>';
            }
        } else {
            expireText = '<span class="text-muted">—</span>';
        }

        const isFirst = idx === 0;
        const isLast = idx === list.length - 1;
        const testable = a.platform === "netease" || a.platform === "qq";
        const testBtn = testable
            ? `<button class="btn btn-sm btn-outline-success btn-test" data-id="${a.id}">
                   <i class="bi bi-check2-all"></i> 测试
               </button>`
            : `<button class="btn btn-sm btn-outline-secondary btn-test disabled" data-id="${a.id}" title="该平台暂不支持">
                   <i class="bi bi-check2-all"></i> 测试
               </button>`;
        return `
            <tr>
                <td>
                    <div class="btn-group btn-group-sm">
                        <button class="btn btn-outline-secondary btn-move" data-id="${a.id}" data-direction="up" ${isFirst ? 'disabled' : ''} title="上移">
                            <i class="bi bi-arrow-up"></i>
                        </button>
                        <button class="btn btn-outline-secondary btn-move" data-id="${a.id}" data-direction="down" ${isLast ? 'disabled' : ''} title="下移">
                            <i class="bi bi-arrow-down"></i>
                        </button>
                    </div>
                </td>
                <td>
                    <div class="form-check form-switch">
                        <input class="form-check-input toggle-enabled" type="checkbox" ${checked} data-id="${a.id}">
                    </div>
                </td>
                <td><span class="badge" style="background-color:${platformColor};color:#ffffff;">${platformName}</span></td>
                <td>${escapeHtml(a.name)}</td>
                <td>${escapeHtml(a.nickname) || '<span class="text-muted">未获取</span>'}</td>
                <td><span class="badge ${a.vip_type > 0 ? 'bg-warning' : 'bg-secondary'}">${escapeHtml(a.vip_text)}</span></td>
                <td><small>${expireText}</small></td>
                <td><strong class="${quotaColor}">${quotaText}</strong></td>
                <td><small class="text-muted">${checkTime}</small></td>
                <td>
                    ${testBtn}
                    <button class="btn btn-sm btn-outline-primary btn-edit" data-id="${a.id}">
                        <i class="bi bi-pencil"></i> 编辑
                    </button>
                    <button class="btn btn-sm btn-outline-danger btn-delete" data-id="${a.id}" data-name="${escapeHtml(a.name)}">
                        <i class="bi bi-trash"></i>
                    </button>
                </td>
            </tr>
        `;
    }).join("");

    bindEvents();
}

function bindEvents() {
    // 排序：上移/下移
    document.querySelectorAll(".btn-move").forEach(el => {
        el.addEventListener("click", async function() {
            if (this.disabled) return;
            const id = this.dataset.id;
            const direction = this.dataset.direction;
            try {
                await api(`/api/accounts/${id}/move`, {
                    method: "POST",
                    body: JSON.stringify({ direction }),
                });
                loadAccounts();
            } catch (e) {
                showToast(e.message, "错误");
            }
        });
    });
    // 启用/禁用
    document.querySelectorAll(".toggle-enabled").forEach(el => {
        el.addEventListener("change", async function() {
            const id = this.dataset.id;
            try {
                await api(`/api/accounts/${id}`, {
                    method: "PUT",
                    body: JSON.stringify({ enabled: this.checked }),
                });
                showToast(this.checked ? "已启用" : "已禁用");
            } catch (e) {
                showToast(e.message, "错误");
                loadAccounts();
            }
        });
    });

    // 测试登录
    document.querySelectorAll(".btn-test").forEach(el => {
        el.addEventListener("click", async function() {
            if (this.classList.contains("disabled")) return;
            const id = this.dataset.id;
            this.disabled = true;
            this.innerHTML = '<span class="loading-spinner"></span>';
            try {
                const data = await api(`/api/accounts/${id}/test`, { method: "POST" });
                showToast(data.msg, "登录测试");
                loadAccounts();
            } catch (e) {
                showToast(e.message, "错误");
            } finally {
                this.disabled = false;
                this.innerHTML = '<i class="bi bi-check2-all"></i> 测试';
            }
        });
    });

    // 编辑
    document.querySelectorAll(".btn-edit").forEach(el => {
        el.addEventListener("click", async function() {
            const id = this.dataset.id;
            try {
                const data = await api("/api/accounts");
                const acc = data.data.find(x => x.id == id);
                if (!acc) return;
                document.getElementById("edit-id").value = acc.id;
                document.getElementById("edit-platform").value = acc.platform_name || PLATFORM_NAMES[acc.platform] || acc.platform;
                document.getElementById("edit-name").value = acc.name;
                document.getElementById("edit-cookie").value = "";
                document.getElementById("edit-quota").value = acc.quota_limit;
                bootstrap.Modal.getOrCreateInstance(document.getElementById("edit-account-modal")).show();
            } catch (e) {
                showToast(e.message, "错误");
            }
        });
    });

    // 删除
    document.querySelectorAll(".btn-delete").forEach(el => {
        el.addEventListener("click", async function() {
            const id = this.dataset.id;
            const name = this.dataset.name;
            if (!confirm(`确定删除账号「${name}」吗？（已下载的歌曲不受影响）`)) return;
            try {
                await api(`/api/accounts/${id}`, { method: "DELETE" });
                showToast("已删除");
                loadAccounts();
            } catch (e) {
                showToast(e.message, "错误");
            }
        });
    });
}

// 平台切换时更新 Cookie 提示文案
document.getElementById("add-platform").addEventListener("change", function() {
    const platform = this.value;
    const hint = document.getElementById("add-cookie-hint");
    if (platform === "netease") {
        hint.textContent = "网易云：浏览器登录 music.163.com → F12 → Application → Cookies → 复制 MUSIC_U";
    } else if (platform === "qq") {
        hint.textContent = "QQ音乐：浏览器登录 y.qq.com → F12 → Network → 任选 y.qq.com 请求 → 复制完整 Cookie（须含 uin，昵称还需 eas_sid）";
    } else {
        hint.textContent = "酷狗音乐：预留平台，暂不支持添加";
    }
});

// 添加账号
document.getElementById("btn-add-account").addEventListener("click", async function() {
    const platform = document.getElementById("add-platform").value;
    const name = document.getElementById("add-name").value.trim();
    const cookie = document.getElementById("add-cookie").value.trim();
    const quota = parseInt(document.getElementById("add-quota").value) || 0;

    if (!name) { showToast("请填写账号别名"); return; }
    if (!cookie) { showToast("请填写 Cookie"); return; }

    const btn = this;
    btn.disabled = true;
    btn.innerHTML = '<span class="loading-spinner"></span> 添加中...';
    try {
        const data = await api("/api/accounts", {
            method: "POST",
            body: JSON.stringify({ platform, name, cookie, quota_limit: quota }),
        });
        showToast(data.msg, "添加成功");
        bootstrap.Modal.getInstance(document.getElementById("add-account-modal")).hide();
        document.getElementById("add-name").value = "";
        document.getElementById("add-cookie").value = "";
        document.getElementById("add-quota").value = "0";
        loadAccounts();
    } catch (e) {
        showToast(e.message, "添加失败");
    } finally {
        btn.disabled = false;
        btn.textContent = "添加";
    }
});

// 添加弹窗打开时，自动选中当前 Tab 的平台
document.getElementById("add-account-modal").addEventListener("show.bs.modal", function() {
    if (currentPlatform !== "all") {
        document.getElementById("add-platform").value = currentPlatform;
    }
});

// 编辑账号保存
document.getElementById("btn-edit-account").addEventListener("click", async function() {
    const id = document.getElementById("edit-id").value;
    const name = document.getElementById("edit-name").value.trim();
    const cookie = document.getElementById("edit-cookie").value.trim();
    const quota = parseInt(document.getElementById("edit-quota").value) || 0;

    if (!name) { showToast("请填写账号别名"); return; }

    const payload = { name, quota_limit: quota };
    if (cookie) payload.cookie = cookie;

    const btn = this;
    btn.disabled = true;
    try {
        await api(`/api/accounts/${id}`, {
            method: "PUT",
            body: JSON.stringify(payload),
        });
        showToast("已保存");
        bootstrap.Modal.getInstance(document.getElementById("edit-account-modal")).hide();
        loadAccounts();
    } catch (e) {
        showToast(e.message, "错误");
    } finally {
        btn.disabled = false;
    }
});

// 初始化
loadAccounts();
