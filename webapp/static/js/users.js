// 用户管理页逻辑

// 加载用户列表
async function loadUsers() {
    try {
        const data = await api("/api/users");
        const tbody = document.getElementById("user-tbody");
        const list = data.data;

        if (!list || list.length === 0) {
            tbody.innerHTML = '<tr><td colspan="7" class="text-center text-muted">暂无用户</td></tr>';
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
            return `
                <tr>
                    <td>${u.id}</td>
                    <td><strong>${escapeHtml(u.username)}</strong></td>
                    <td>${roleText}</td>
                    <td>${statusText}</td>
                    <td><small class="text-muted">${createTime}</small></td>
                    <td><small class="text-muted">${loginTime}</small></td>
                    <td>
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

// escapeHtml 已收敛至全局 app.js（L8），此处不再定义本地副本。

// 初始化
loadUsers();
