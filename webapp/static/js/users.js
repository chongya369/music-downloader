// 用户管理页逻辑

// 加载用户列表
async function loadUsers() {
    try {
        const data = await api("/api/users");
        const tbody = document.getElementById("user-tbody");
        const list = data.data;

        if (!list || list.length === 0) {
            tbody.innerHTML = '<tr><td colspan="8" class="text-center text-muted">暂无用户</td></tr>';
            return;
        }

        tbody.innerHTML = list.map(u => {
            const roleText = u.is_admin
                ? '<span class="badge bg-warning">管理员</span>'
                : '<span class="badge bg-secondary">普通用户</span>';
            const statusText = u.enabled
                ? '<span class="badge bg-success">启用</span>'
                : '<span class="badge bg-danger">禁用</span>';
            const createTime = u.created_at || "—";
            const loginTime = u.last_login_at || "从未登录";
            const fnosUid = u.fnos_uid || "";
            // 飞牛绑定列：仅网关模式提供操作入口（TCP 模式无法验证身份，接口也会 403）
            const fnosCell = !window.GATEWAY_MODE
                ? '<span class="text-muted small">—</span>'
                : (fnosUid
                    ? `<span class="badge bg-success">UID ${escapeHtml(fnosUid)}</span>
                       <button class="btn btn-sm btn-link text-danger p-0 ms-1 btn-unbind-fnos"
                               data-id="${u.id}" data-username="${escapeHtml(u.username)}"
                               title="解除绑定"><i class="bi bi-x-circle"></i></button>`
                    : `<span class="text-muted small me-1">未绑定</span>
                       <button class="btn btn-sm btn-outline-primary btn-bind-fnos"
                               data-id="${u.id}" data-username="${escapeHtml(u.username)}">
                           <i class="bi bi-link-45deg"></i> 绑定
                       </button>`);
            return `
                <tr>
                    <td>${u.id}</td>
                    <td><strong>${escapeHtml(u.username)}</strong></td>
                    <td>${roleText}</td>
                    <td>${statusText}</td>
                    <td><small class="text-muted">${createTime}</small></td>
                    <td><small class="text-muted">${loginTime}</small></td>
                    <td>${fnosCell}</td>
                    <td>
                        <button class="btn btn-sm btn-outline-primary btn-rename-user" data-id="${u.id}" data-username="${escapeHtml(u.username)}" data-fnos-uid="${escapeHtml(fnosUid)}">
                            <i class="bi bi-pencil-square"></i> 改名
                        </button>
                        <button class="btn btn-sm btn-outline-warning btn-reset-pwd" data-id="${u.id}" data-username="${escapeHtml(u.username)}">
                            <i class="bi bi-key"></i> 重置密码
                        </button>
                        <button class="btn btn-sm btn-outline-${u.enabled ? 'secondary' : 'success'} btn-toggle-enabled" data-id="${u.id}" data-enabled="${u.enabled}">
                            <i class="bi bi-${u.enabled ? 'pause' : 'play'}"></i> ${u.enabled ? '禁用' : '启用'}
                        </button>
                        <button class="btn btn-sm btn-outline-danger btn-delete-user" data-id="${u.id}" data-username="${escapeHtml(u.username)}">
                            <i class="bi bi-trash"></i>
                        </button>
                    </td>
                </tr>
            `;
        }).join("");

        bindEvents();
    } catch (e) {
        showToast(e.message, "错误");
    }
}

function bindEvents() {
    // 改名
    document.querySelectorAll(".btn-rename-user").forEach(el => {
        el.addEventListener("click", function() {
            const modalEl = document.getElementById("rename-user-modal");
            document.getElementById("rename-old-username").value = this.dataset.username;
            const newInput = document.getElementById("rename-new-username");
            newInput.value = this.dataset.username;
            // 用 data 属性暂存目标用户 ID
            document.getElementById("btn-rename-user").dataset.userId = this.dataset.id;
            // 已绑定飞牛 UID 的账号改名不影响免密登录，提示文案要跟着变
            const boundUid = this.dataset.fnosUid || "";
            const warnUnbound = document.getElementById("rename-warn-unbound");
            const warnBound = document.getElementById("rename-warn-bound");
            if (warnUnbound && warnBound) {
                warnUnbound.classList.toggle("d-none", !!boundUid);
                warnBound.classList.toggle("d-none", !boundUid);
                const uidEl = document.getElementById("rename-bound-uid");
                if (uidEl) uidEl.textContent = boundUid;
            }
            bootstrap.Modal.getOrCreateInstance(modalEl).show();
        });
    });

    // 重置密码
    document.querySelectorAll(".btn-reset-pwd").forEach(el => {
        el.addEventListener("click", function() {
            document.getElementById("reset-username").value = this.dataset.username;
            document.getElementById("reset-password").value = "";
            // 用 data 属性暂存目标用户 ID
            document.getElementById("btn-reset-pwd").dataset.userId = this.dataset.id;
            bootstrap.Modal.getOrCreateInstance(document.getElementById("reset-pwd-modal")).show();
        });
    });

    // 启用/禁用切换
    document.querySelectorAll(".btn-toggle-enabled").forEach(el => {
        el.addEventListener("click", async function() {
            const id = this.dataset.id;
            const newEnabled = this.dataset.enabled !== "true";  // 翻转
            try {
                await api(`/api/users/${id}`, {
                    method: "PUT",
                    body: JSON.stringify({ enabled: newEnabled }),
                });
                showToast(newEnabled ? "已启用" : "已禁用");
                loadUsers();
            } catch (e) {
                showToast(e.message, "错误");
            }
        });
    });

    // 绑定飞牛账号（预填当前访问身份的 UID，可改为手工输入）
    document.querySelectorAll(".btn-bind-fnos").forEach(el => {
        el.addEventListener("click", function() {
            document.getElementById("bind-username").value = this.dataset.username;
            const ident = window.GATEWAY_IDENTITY || {};
            document.getElementById("bind-fnos-uid").value = ident.uid || "";
            document.getElementById("btn-bind-fnos").dataset.userId = this.dataset.id;
            bootstrap.Modal.getOrCreateInstance(document.getElementById("bind-fnos-modal")).show();
        });
    });

    // 解除飞牛绑定
    document.querySelectorAll(".btn-unbind-fnos").forEach(el => {
        el.addEventListener("click", async function() {
            const id = this.dataset.id;
            const username = this.dataset.username;
            if (!confirm(`确定解除「${username}」与飞牛账号的绑定吗？解除后该账号需手动输入密码登录。`)) return;
            try {
                await api(`/api/users/${id}/bind-fnos`, { method: "DELETE" });
                showToast("已解除飞牛绑定");
                loadUsers();
            } catch (e) {
                showToast(e.message, "错误");
            }
        });
    });

    // 删除用户
    document.querySelectorAll(".btn-delete-user").forEach(el => {
        el.addEventListener("click", async function() {
            const id = this.dataset.id;
            const username = this.dataset.username;
            if (!confirm(`确定删除用户「${username}」吗？此操作不可撤销`)) return;
            try {
                await api(`/api/users/${id}`, { method: "DELETE" });
                showToast("已删除");
                loadUsers();
            } catch (e) {
                showToast(e.message, "错误");
            }
        });
    });
}

// 添加用户
document.getElementById("btn-add-user").addEventListener("click", async function() {
    const username = document.getElementById("add-username").value.trim();
    const password = document.getElementById("add-password").value;
    const isAdmin = document.getElementById("add-is-admin").checked;

    if (!username) { showToast("请输入用户名"); return; }
    if (!password || password.length < 6) { showToast("密码至少 6 位"); return; }

    const btn = this;
    btn.disabled = true;
    btn.innerHTML = '<span class="loading-spinner"></span> 添加中...';
    try {
        await api("/api/users", {
            method: "POST",
            body: JSON.stringify({ username, password, is_admin: isAdmin }),
        });
        showToast("用户已创建", "成功");
        bootstrap.Modal.getInstance(document.getElementById("add-user-modal")).hide();
        document.getElementById("add-username").value = "";
        document.getElementById("add-password").value = "";
        document.getElementById("add-is-admin").checked = false;
        loadUsers();
    } catch (e) {
        showToast(e.message, "错误");
    } finally {
        btn.disabled = false;
        btn.textContent = "添加";
    }
});

// 确认重置密码
document.getElementById("btn-reset-pwd").addEventListener("click", async function() {
    const userId = this.dataset.userId;
    const newPwd = document.getElementById("reset-password").value;
    if (!newPwd || newPwd.length < 6) { showToast("密码至少 6 位"); return; }

    const btn = this;
    btn.disabled = true;
    try {
        await api(`/api/users/${userId}`, {
            method: "PUT",
            body: JSON.stringify({ password: newPwd }),
        });
        showToast("密码已重置", "成功");
        bootstrap.Modal.getInstance(document.getElementById("reset-pwd-modal")).hide();
    } catch (e) {
        showToast(e.message, "错误");
    } finally {
        btn.disabled = false;
    }
});

// 确认改名
document.getElementById("btn-rename-user").addEventListener("click", async function() {
    const userId = this.dataset.userId;
    const oldName = document.getElementById("rename-old-username").value;
    const newName = document.getElementById("rename-new-username").value.trim();
    if (!newName) { showToast("请输入新用户名"); return; }
    if (newName.length > 50) { showToast("用户名不能超过 50 个字符"); return; }
    if (newName === oldName) { showToast("新用户名与当前用户名相同"); return; }
    // 二次确认：改名立即影响该用户的登录名，避免误操作
    if (!confirm(`确认将用户名「${oldName}」修改为「${newName}」吗？此操作立即生效。`)) return;

    const btn = this;
    btn.disabled = true;
    try {
        const res = await api(`/api/users/${userId}`, {
            method: "PUT",
            body: JSON.stringify({ username: newName }),
        });
        showToast("用户名已更新", "成功");
        bootstrap.Modal.getInstance(document.getElementById("rename-user-modal")).hide();
        // 改的是自己：顶部用户名由服务端渲染（base.html），必须整页刷新
        if (res && res.data && res.data.is_self) {
            location.reload();
        } else {
            loadUsers();
        }
    } catch (e) {
        showToast(e.message, "错误");
    } finally {
        btn.disabled = false;
    }
});

// 确认绑定飞牛账号
document.getElementById("btn-bind-fnos").addEventListener("click", async function() {
    const userId = this.dataset.userId;
    const uid = document.getElementById("bind-fnos-uid").value.trim();
    const btn = this;
    btn.disabled = true;
    try {
        // UID 留空时由服务端取当前请求的 X-Trim-Userid
        const res = await api(`/api/users/${userId}/bind-fnos`, {
            method: "POST",
            body: JSON.stringify(uid ? { fnos_uid: uid } : {}),
        });
        showToast(res.msg || "已绑定", "成功");
        bootstrap.Modal.getInstance(document.getElementById("bind-fnos-modal")).hide();
        loadUsers();
    } catch (e) {
        showToast(e.message, "错误");
    } finally {
        btn.disabled = false;
    }
});

// escapeHtml 已收敛至全局 app.js（L8），此处不再定义本地副本。

// 改名弹窗展开后聚焦新用户名输入框并全选，便于直接覆盖输入
document.getElementById("rename-user-modal").addEventListener("shown.bs.modal", function() {
    const el = document.getElementById("rename-new-username");
    el.focus();
    el.select();
});

// 初始化
loadUsers();
