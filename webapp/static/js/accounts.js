// 账号管理页逻辑

// 导出账号信息（含 cookie，敏感）
document.getElementById("btn-export-accounts").addEventListener("click", function() {
    if (!confirm("导出文件包含 Cookie 等敏感信息，请妥善保管。是否继续？")) return;
    window.location.href = "/api/accounts/export";
});

// 导入账号信息（从 v0.4.0+ 导出的 JSON 文件）
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
        if (!confirm(`即将导入 ${count} 个账号（同名账号将自动跳过）。是否继续？`)) {
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
        this.value = "";  // 重置以便重复选择同一文件
    }
});

// 加载账号列表
async function loadAccounts() {
    try {
        const data = await api("/api/accounts");
        const tbody = document.getElementById("account-tbody");
        const list = data.data;

        if (!list || list.length === 0) {
            tbody.innerHTML = '<tr><td colspan="9" class="text-center text-muted">暂无账号，点击右上角添加</td></tr>';
            return;
        }

        tbody.innerHTML = list.map((a, idx) => {
            const checked = a.enabled ? "checked" : "";
            const checkTime = a.last_check_at || "从未校验";
            // 额度显示：不限制显示"不限"，否则显示"已用/总额"
            const quotaText = a.quota_limit > 0
                ? `${a.monthly_downloaded} / ${a.quota_limit}`
                : `${a.monthly_downloaded}（不限）`;
            const quotaColor = a.quota_limit > 0 && a.monthly_downloaded >= a.quota_limit
                ? "text-danger" : "text-success";
            // 会员到期：非会员显示"—"，会员显示日期，有会员类型但无到期显示"未知"
            let expireText;
            if (a.vip_type > 0) {
                if (a.vip_expire_at) {
                    // 只显示日期部分
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
            // 排序按钮：第一个禁用上移，最后一个禁用下移
            const isFirst = idx === 0;
            const isLast = idx === list.length - 1;
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
                    <td>${a.name}</td>
                    <td>${a.nickname || '<span class="text-muted">未获取</span>'}</td>
                    <td><span class="badge ${a.vip_type > 0 ? 'bg-warning' : 'bg-secondary'}">${a.vip_text}</span></td>
                    <td><small>${expireText}</small></td>
                    <td><strong class="${quotaColor}">${quotaText}</strong></td>
                    <td><small class="text-muted">${checkTime}</small></td>
                    <td>
                        <button class="btn btn-sm btn-outline-success btn-test" data-id="${a.id}">
                            <i class="bi bi-check2-all"></i> 测试
                        </button>
                        <button class="btn btn-sm btn-outline-primary btn-edit" data-id="${a.id}">
                            <i class="bi bi-pencil"></i> 编辑
                        </button>
                        <button class="btn btn-sm btn-outline-danger btn-delete" data-id="${a.id}" data-name="${a.name}">
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
            // 拉取最新数据
            try {
                const data = await api("/api/accounts");
                const acc = data.data.find(x => x.id == id);
                if (!acc) return;
                document.getElementById("edit-id").value = acc.id;
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

// 添加账号
document.getElementById("btn-add-account").addEventListener("click", async function() {
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
            body: JSON.stringify({ name, cookie, quota_limit: quota }),
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
