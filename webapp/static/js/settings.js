// 设置页逻辑

// 加载设置
async function loadSettings() {
    try {
        const data = await api("/api/settings");
        const s = data.data;
        const form = document.getElementById("settings-form");

        form.api_url.value = s.api_url || "";
        form.web_port.value = s.web_port || "56700";
        form.output_dir.value = s.output_dir || "";
        form.level.value = s.level || "exhigh";
        form.max_retries.value = s.max_retries || "3";
        form.default_playlist_limit.value = s.default_playlist_limit || "50";
        form.exclude_keywords.value = s.exclude_keywords || "";
        // 加载过滤范围（兼容旧版 'both'）
        let scopes = [];
        const rawScope = s.exclude_scope || "playlist,search";
        if (rawScope === "both") {
            scopes = ["playlist", "search"];
        } else {
            scopes = rawScope.split(",").map(item => item.trim()).filter(item => item);
        }
        form.scope_playlist.checked = scopes.includes("playlist");
        form.scope_search.checked = scopes.includes("search");
        form.hourly_limit_per_account.value = s.hourly_limit_per_account || "50";
        form.download_mode.value = s.download_mode || "fallback";
        form.prefer_non_vip.checked = s.prefer_non_vip === "true";
        form.sync_times.value = s.sync_times || "03:00,09:00,21:00";
        form.sync_jitter.value = s.sync_jitter || "600";

        form.write_metadata.checked = s.write_metadata === "true";
        form.write_lyric.checked = s.write_lyric === "true";
        form.auto_sync_enabled.checked = s.auto_sync_enabled === "true";
    } catch (e) {
        showToast(e.message, "错误");
    }
}

// 保存设置
document.getElementById("settings-form").addEventListener("submit", async function(e) {
    e.preventDefault();
    const form = this;
    const payload = {
        api_url: form.api_url.value.trim(),
        web_port: form.web_port.value.trim(),
        output_dir: form.output_dir.value.trim(),
        level: form.level.value,
        max_retries: form.max_retries.value,
        default_playlist_limit: form.default_playlist_limit.value,
        exclude_keywords: form.exclude_keywords.value.trim(),
        // 收集 checkbox 选中项拼接为逗号分隔字符串
        exclude_scope: [
            form.scope_playlist.checked ? "playlist" : "",
            form.scope_search.checked ? "search" : "",
        ].filter(Boolean).join(","),
        hourly_limit_per_account: form.hourly_limit_per_account.value,
        download_mode: form.download_mode.value,
        prefer_non_vip: form.prefer_non_vip.checked ? "true" : "false",
        sync_times: form.sync_times.value.trim(),
        sync_jitter: form.sync_jitter.value,
        write_metadata: form.write_metadata.checked ? "true" : "false",
        write_lyric: form.write_lyric.checked ? "true" : "false",
        auto_sync_enabled: form.auto_sync_enabled.checked ? "true" : "false",
    };

    try {
        const data = await api("/api/settings", {
            method: "PUT",
            body: JSON.stringify(payload),
        });
        showToast(data.msg, "成功");
        loadSettings();
    } catch (e) {
        showToast(e.message, "错误");
    }
});

// 初始化
loadSettings();
