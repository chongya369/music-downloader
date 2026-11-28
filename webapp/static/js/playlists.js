// 歌单管理页逻辑（我的歌单 + 三平台共用的歌曲搜索）

let defaultPlaylistLimit = 50;  // 添加歌单时的默认下载数量（来自设置）

// 平台列表（模板注入的 window._AVAILABLE_PLATFORMS，配置驱动）
const _PLATFORMS = Array.isArray(window._AVAILABLE_PLATFORMS) ? window._AVAILABLE_PLATFORMS : [];

function platformName(key) {
    const p = _PLATFORMS.find(x => x.key === key);
    return p ? p.name : key;
}

// 加载默认下载数量配置
async function loadDefaultPlaylistLimit() {
    try {
        const data = await api("/api/settings");
        const val = parseInt(data.data.default_playlist_limit);
        if (!isNaN(val) && val > 0) {
            defaultPlaylistLimit = val;
        }
    } catch (e) {
        console.error("加载默认下载数量失败:", e);
    }
}

// 弹窗打开时把默认下载数量填入输入框
document.getElementById("add-modal").addEventListener("show.bs.modal", function() {
    const input = document.getElementById("add-limit");
    input.value = defaultPlaylistLimit;
});

// ==================================================================
// 标签页切换（我的歌单 / 歌曲搜索）
// ==================================================================
document.querySelectorAll("#playlist-tabs .nav-link").forEach(el => {
    el.addEventListener("click", function(e) {
        e.preventDefault();
        const isMine = this.dataset.tab === "mine";
        document.querySelectorAll("#playlist-tabs .nav-link").forEach(n => n.classList.remove("active"));
        this.classList.add("active");
        document.getElementById("tab-mine").classList.toggle("d-none", !isMine);
        document.getElementById("tab-search").classList.toggle("d-none", isMine);
        // 切回「我的歌单」时重新拉取，保证弹窗新添加的歌单立即可见；
        // 「歌曲搜索」只切换显示、不重发请求，保留上次结果
        if (isMine) {
            loadPlaylists();
        }
    });
});

// ==================================================================
// 我的歌单
// ==================================================================
async function loadPlaylists() {
    try {
        const data = await api("/api/playlists");
        const tbody = document.getElementById("playlist-tbody");
        const list = data.data;

        if (!list || list.length === 0) {
            tbody.innerHTML = '<tr><td colspan="7" class="text-center text-muted">暂无关注的歌单，点击右上角添加</td></tr>';
            return;
        }

        tbody.innerHTML = list.map(p => {
            const typeText = p.type === "official" ? "官方榜单" : "自定义";
            const checked = p.enabled ? "checked" : "";
            const syncTime = p.last_synced_at || "从未同步";
            const platformBadge = p.platform_name
                ? `<span class="badge-platform badge ${p.platform === 'qq' ? 'bg-info' : 'bg-primary'}">${escapeHtml(p.platform_name)}</span> `
                : "";
            return `
                <tr>
                    <td>
                        <div class="form-check form-switch">
                            <input class="form-check-input toggle-enabled" type="checkbox" ${checked} data-id="${p.id}" data-platform="${escapeHtml(p.platform)}">
                        </div>
                    </td>
                    <td>${platformBadge}${escapeHtml(p.name)}</td>
                    <td><span class="badge ${p.type === 'official' ? 'bg-info' : 'bg-secondary'}">${typeText}</span></td>
                    <td>
                        <input type="number" class="form-control form-control-sm limit-input" value="${p.limit_count}" data-id="${p.id}" data-platform="${escapeHtml(p.platform)}" min="1" max="9999" style="width:70px">
                    </td>
                    <td>${p.track_count || 0}</td>
                    <td><small class="text-muted">${syncTime}</small></td>
                    <td>
                        <button class="btn btn-sm btn-outline-primary btn-sync" data-id="${p.id}">
                            <i class="bi bi-arrow-repeat"></i> 同步
                        </button>
                        <button class="btn btn-sm btn-outline-danger btn-delete" data-id="${p.id}" data-name="${escapeHtml(p.name)}" data-platform="${escapeHtml(p.platform)}">
                            <i class="bi bi-trash"></i>
                        </button>
                    </td>
                </tr>
            `;
        }).join("");

        bindPlaylistEvents();
    } catch (e) {
        showToast(e.message, "错误");
    }
}

function bindPlaylistEvents() {
    document.querySelectorAll(".toggle-enabled").forEach(el => {
        el.addEventListener("change", async function() {
            const id = this.dataset.id;
            try {
                await api(`/api/playlists/${id}`, {
                    method: "PUT",
                    body: JSON.stringify({ enabled: this.checked, platform: this.dataset.platform }),
                });
                showToast(this.checked ? "已启用" : "已禁用");
            } catch (e) {
                showToast(e.message, "错误");
                loadPlaylists();
            }
        });
    });

    document.querySelectorAll(".limit-input").forEach(el => {
        el.addEventListener("change", async function() {
            const id = this.dataset.id;
            try {
                await api(`/api/playlists/${id}`, {
                    method: "PUT",
                    body: JSON.stringify({ limit_count: parseInt(this.value), platform: this.dataset.platform }),
                });
                showToast("已更新下载数量");
            } catch (e) {
                showToast(e.message, "错误");
            }
        });
    });

    document.querySelectorAll(".btn-sync").forEach(el => {
        el.addEventListener("click", async function() {
            const id = this.dataset.id;
            this.disabled = true;
            this.innerHTML = '<span class="loading-spinner"></span>';
            try {
                const data = await api(`/api/sync/${id}`, { method: "POST" });
                showToast(data.msg, "同步结果");
            } catch (e) {
                showToast(e.message, "错误");
            } finally {
                this.disabled = false;
                this.innerHTML = '<i class="bi bi-arrow-repeat"></i> 同步';
            }
        });
    });

    document.querySelectorAll(".btn-delete").forEach(el => {
        el.addEventListener("click", async function() {
            const id = this.dataset.id;
            const name = this.dataset.name;
            if (!confirm(`确定取消关注「${name}」吗？（已下载的歌曲不受影响）`)) return;
            try {
                await api(`/api/playlists/${id}?platform=${encodeURIComponent(this.dataset.platform)}`, { method: "DELETE" });
                showToast("已删除");
                loadPlaylists();
            } catch (e) {
                showToast(e.message, "错误");
            }
        });
    });
}

// 添加歌单（我的歌单弹窗使用）
async function addPlaylist(source, type, limit, platform = "netease") {
    return await api("/api/playlists", {
        method: "POST",
        body: JSON.stringify({ source, type, limit, platform }),
    });
}

document.getElementById("btn-add-confirm").addEventListener("click", async function() {
    const source = document.getElementById("add-source").value.trim();
    const type = document.getElementById("add-type").value;
    const limit = parseInt(document.getElementById("add-limit").value) || defaultPlaylistLimit;
    const platform = document.getElementById("add-platform").value;
    if (!source) { showToast("请输入歌单 ID 或链接"); return; }

    const btn = this;
    btn.disabled = true;
    btn.innerHTML = '<span class="loading-spinner"></span> 添加中...';
    try {
        const data = await addPlaylist(source, type, limit, platform);
        showToast(data.msg, "添加成功");
        bootstrap.Modal.getInstance(document.getElementById("add-modal")).hide();
        document.getElementById("add-source").value = "";
        loadPlaylists();
    } catch (e) {
        showToast(e.message, "添加失败");
    } finally {
        btn.disabled = false;
        btn.textContent = "添加";
    }
});

// ==================================================================
// 歌曲搜索（三平台共用一套界面，平台由下拉框选择）
// ==================================================================

// 搜索状态：表单即状态（平台/关键词/类型/每页共享一份，不再按平台记忆）
let _searchState = {
    platform: document.getElementById("search-platform").value || "netease",
    keyword: "",
    type: "song",     // song | artist | album
    limit: 50,
    page: 1,
};

// 请求序号：切换平台/重复搜索时递增，响应回来若序号已变则丢弃（防旧平台
// 的慢响应覆盖新平台结果，酷狗聚合搜索尤其慢）
let _searchSeq = 0;
// 请求在途标记：期间禁用搜索/全部下载/翻页，避免重复提交
let _searchBusy = false;
// 最近一次分页数据（单一数据源：忙碌态切换后据此重算按钮禁用态，
// 避免「请求结束时 updateSearchPagination 已按忙碌态算过一次」导致翻页永久禁用）
let _pageState = { curPage: 1, pages: 0, total: 0 };

function _setSearchBusy(busy) {
    _searchBusy = busy;
    document.getElementById("btn-search").disabled = busy;
    document.getElementById("btn-search-download-all").disabled = busy;
    const prev = document.getElementById("btn-search-prev");
    const next = document.getElementById("btn-search-next");
    prev.disabled = busy || _pageState.curPage <= 1;
    next.disabled = busy || _pageState.curPage >= _pageState.pages || _pageState.pages === 0;
}

// 清空结果区（同步执行，不等新响应）：切换平台/类型时立即调用，
// 避免「下拉已切到 B 平台、表格还是 A 平台数据」时点下载导致错平台入队
function _resetSearchView(text = "输入关键词后点击搜索") {
    const row = (colspan) => `<tr><td colspan="${colspan}" class="text-center text-muted">${text}</td></tr>`;
    document.getElementById("search-tbody-song").innerHTML = row(7);
    document.getElementById("search-tbody-album").innerHTML = row(6);
    document.getElementById("search-title").textContent = "搜索结果";
    document.getElementById("search-title-album").textContent = "搜索结果";
    updateSearchPagination(1, 0, 0);
}

// 作废在途请求（递增序号 + 解除忙碌态），供切换平台时使用
function _invalidateSearch() {
    _searchSeq++;
    _setSearchBusy(false);
}

// 切换平台：作废旧请求 → 清空结果 → 有关键词则按新平台重搜
document.getElementById("search-platform").addEventListener("change", function() {
    const platform = this.value;
    if (platform === _searchState.platform) return;
    _searchState.platform = platform;
    _searchState.page = 1;
    _invalidateSearch();
    _resetSearchView();
    document.getElementById("search-title").textContent = `搜索结果（${platformName(platform)}）`;
    if (_searchState.keyword) {
        loadSearchResults();
    }
});

// 同步每页数量下拉框到状态
document.getElementById("search-limit").addEventListener("change", function() {
    _searchState.limit = parseInt(this.value) || 50;
    _searchState.page = 1;
});

// 搜索按钮（以表单当前值提交）
document.getElementById("btn-search").addEventListener("click", () => {
    if (_searchBusy) return;
    const kw = document.getElementById("search-keyword").value.trim();
    if (!kw) { showToast("请输入搜索关键词"); return; }
    _searchState.platform = document.getElementById("search-platform").value;
    _searchState.type = document.getElementById("search-type").value;
    _searchState.limit = parseInt(document.getElementById("search-limit").value) || 50;
    _searchState.keyword = kw;
    _searchState.page = 1;
    loadSearchResults();
});

// 回车搜索
document.getElementById("search-keyword").addEventListener("keypress", function(e) {
    if (e.key === "Enter") {
        e.preventDefault();
        document.getElementById("btn-search").click();
    }
});

// 切换搜索类型：重置页码、清空结果、切换表格显示
document.getElementById("search-type").addEventListener("change", function() {
    _searchState.type = this.value;
    _searchState.page = 1;
    const isAlbum = this.value === "album";
    document.getElementById("search-result-song").classList.toggle("d-none", isAlbum);
    document.getElementById("search-result-album").classList.toggle("d-none", !isAlbum);
    // "全部下载"按钮仅歌曲/歌手模式显示
    document.getElementById("btn-search-download-all").style.display = isAlbum ? "none" : "";
    _invalidateSearch();
    _resetSearchView();
});

// 全部下载（应用排除过滤，仅歌曲/歌手模式，下载当前页）
document.getElementById("btn-search-download-all").addEventListener("click", async function() {
    const kw = _searchState.keyword;
    if (!kw) { showToast("请先输入搜索关键词"); return; }
    if (_searchBusy) return;
    const platform = _searchState.platform;
    const limit = _searchState.limit;
    const offset = (_searchState.page - 1) * limit;
    if (!confirm(`将在【${platformName(platform)}】下载当前页搜索结果（第 ${_searchState.page} 页），并应用排除关键字过滤。确定继续？`)) return;
    const btn = this;
    btn.disabled = true;
    btn.innerHTML = '<span class="loading-spinner"></span> 下载中...';
    try {
        const data = await api("/api/discover/search-download", {
            method: "POST",
            body: JSON.stringify({ keyword: kw, limit, offset, platform }),
        });
        showToast(data.msg, "下载结果");
        // 下载后刷新当前页状态（等刷新结束，避免按钮文案与忙碌态错位）
        await loadSearchResults();
    } catch (e) {
        showToast(e.message, "错误");
    } finally {
        // 成功/失败都要恢复文案：否则会一直停在 spinner + 「下载中...」
        btn.innerHTML = '<i class="bi bi-cloud-arrow-down"></i> 全部下载';
        btn.disabled = _searchBusy;   // 刷新仍在途时保持禁用
    }
});

// 分页：上一页
document.getElementById("btn-search-prev").addEventListener("click", () => {
    if (_searchBusy) return;
    if (_searchState.page > 1) {
        _searchState.page--;
        loadSearchResults();
    }
});

// 分页：下一页
document.getElementById("btn-search-next").addEventListener("click", () => {
    if (_searchBusy) return;
    _searchState.page++;
    loadSearchResults();
});

// 加载搜索结果（根据 _searchState.type 渲染不同表格）
async function loadSearchResults() {
    const keyword = _searchState.keyword;
    const type = _searchState.type;
    const limit = _searchState.limit;
    const platform = _searchState.platform;
    const page = _searchState.page;
    const offset = (page - 1) * limit;
    const isAlbum = type === "album";
    const seq = ++_searchSeq;

    // 同步下拉框状态（防止外部调用未同步）
    document.getElementById("search-type").value = type;
    document.getElementById("search-result-song").classList.toggle("d-none", isAlbum);
    document.getElementById("search-result-album").classList.toggle("d-none", !isAlbum);
    document.getElementById("btn-search-download-all").style.display = isAlbum ? "none" : "";

    const tbody = document.getElementById(isAlbum ? "search-tbody-album" : "search-tbody-song");
    const titleEl = document.getElementById(isAlbum ? "search-title-album" : "search-title");
    titleEl.textContent = `搜索结果: ${keyword}（${platformName(platform)} · 第 ${page} 页）`;
    const colspan = isAlbum ? 6 : 7;
    tbody.innerHTML = `<tr><td colspan="${colspan}" class="text-center text-muted"><span class="loading-spinner"></span> 搜索中...</td></tr>`;
    _setSearchBusy(true);

    try {
        const data = await api("/api/discover/search", {
            method: "POST",
            body: JSON.stringify({ keyword, type, limit, offset, platform }),
        });
        if (seq !== _searchSeq) return;   // 过期响应：丢弃，不写 DOM
        const d = data.data || {};
        const list = d.items || [];
        const total = d.total || 0;
        const pages = d.pages || 0;
        const curPage = d.page || 1;
        _searchState.page = curPage;  // 同步后端返回的实际页码

        if (list.length === 0) {
            tbody.innerHTML = `<tr><td colspan="${colspan}" class="text-center text-muted">无搜索结果</td></tr>`;
            updateSearchPagination(curPage, pages, total);
            return;
        }

        if (isAlbum) {
            tbody.innerHTML = list.map((a, idx) => {
                const pubTime = a.publish_time
                    ? new Date(a.publish_time).toLocaleDateString("zh-CN")
                    : "—";
                return `
                    <tr>
                        <td>${(curPage - 1) * limit + idx + 1}</td>
                        <td>${escapeHtml(a.artist)}</td>
                        <td>${escapeHtml(a.name)}</td>
                        <td>${a.size || "—"}</td>
                        <td><small class="text-muted">${pubTime}</small></td>
                        <td>
                            <button class="btn btn-sm btn-success btn-dl-album"
                                data-id="${a.id}" data-name="${escapeHtml(a.name)}">
                                <i class="bi bi-cloud-arrow-down"></i> 下载专辑
                            </button>
                        </td>
                    </tr>
                `;
            }).join("");
            bindAlbumDownload(platform);
        } else {
            tbody.innerHTML = list.map((s, idx) => {
                const feeText = s.fee === 1
                    ? '<span class="badge bg-warning">VIP</span>'
                    : '<span class="badge bg-success">免费</span>';
                const statusText = s.downloaded
                    ? '<span class="badge bg-secondary">已下载</span>'
                    : '<span class="badge bg-light text-dark">未下载</span>';
                const btnDisabled = s.downloaded ? "disabled" : "";
                return `
                    <tr>
                        <td>${(curPage - 1) * limit + idx + 1}</td>
                        <td>${escapeHtml(s.name)}</td>
                        <td>${escapeHtml(s.artists)}</td>
                        <td><small class="text-muted">${escapeHtml(s.album || "—")}</small></td>
                        <td>${feeText}</td>
                        <td>${statusText}</td>
                        <td>
                            <button class="btn btn-sm btn-outline-primary btn-dl-single" ${btnDisabled}
                                data-id="${s.id}" data-name="${escapeHtml(s.name)}"
                                data-artists="${escapeHtml(s.artists)}" data-fee="${s.fee}">
                                <i class="bi bi-download"></i>
                            </button>
                        </td>
                    </tr>
                `;
            }).join("");
            bindSingleDownload(platform);
        }
        updateSearchPagination(curPage, pages, total);
    } catch (e) {
        if (seq !== _searchSeq) return;   // 过期请求的错误同样忽略
        tbody.innerHTML = `<tr><td colspan="${colspan}" class="text-center text-danger">搜索失败: ${escapeHtml(e.message)}</td></tr>`;
        updateSearchPagination(1, 0, 0);
    } finally {
        if (seq === _searchSeq) _setSearchBusy(false);
    }
}

// 更新搜索分页控件（同时记录分页数据，供忙碌态切换后重算）
function updateSearchPagination(curPage, totalPages, total) {
    _pageState = { curPage, pages: totalPages, total };
    const info = document.getElementById("search-page-info");
    const prev = document.getElementById("btn-search-prev");
    const next = document.getElementById("btn-search-next");
    info.textContent = `第 ${curPage} 页 / 共 ${totalPages} 页（总 ${total} 个）`;
    prev.disabled = _searchBusy || curPage <= 1;
    next.disabled = _searchBusy || curPage >= totalPages || totalPages === 0;
}

// 单首下载（不受排除过滤限制）；platform 取自渲染这批结果的平台，
// 不读当前下拉值，避免「切换平台后点旧行」造成错平台入队
function bindSingleDownload(platform) {
    document.querySelectorAll(".btn-dl-single").forEach(el => {
        el.addEventListener("click", async function() {
            if (this.disabled) return;
            const songId = this.dataset.id;
            const name = this.dataset.name;
            const artists = this.dataset.artists;
            const fee = parseInt(this.dataset.fee) || 0;
            this.disabled = true;
            this.innerHTML = '<span class="loading-spinner"></span>';
            try {
                const data = await api("/api/discover/download-song", {
                    method: "POST",
                    body: JSON.stringify({ song_id: songId, name, artists, fee, platform }),
                });
                showToast(data.msg, "下载");
                // 更新按钮状态
                this.className = "btn btn-sm btn-secondary btn-dl-single";
                this.innerHTML = '<i class="bi bi-check2"></i>';
                this.disabled = true;
                // 更新状态徽章（歌曲模式：第 5 列为状态）
                const statusCell = this.closest("tr").children[5];
                statusCell.innerHTML = '<span class="badge bg-info">下载中</span>';
            } catch (e) {
                showToast(e.message, "错误");
                this.disabled = false;
                this.innerHTML = '<i class="bi bi-download"></i>';
            }
        });
    });
}

// 专辑下载（下载整张专辑，应用排除过滤）
function bindAlbumDownload(platform) {
    document.querySelectorAll(".btn-dl-album").forEach(el => {
        el.addEventListener("click", async function() {
            if (this.disabled) return;
            const albumId = this.dataset.id;
            const albumName = this.dataset.name;
            if (!confirm(`确定下载专辑「${albumName}」内的全部歌曲吗？\n（将应用排除关键字过滤）`)) return;
            this.disabled = true;
            const originalHtml = this.innerHTML;
            this.innerHTML = '<span class="loading-spinner"></span> 下载中...';
            try {
                const data = await api("/api/discover/album-download", {
                    method: "POST",
                    body: JSON.stringify({ album_id: albumId, album_name: albumName, platform }),
                });
                showToast(data.msg, "专辑下载");
                this.className = "btn btn-sm btn-secondary btn-dl-album";
                this.innerHTML = '<i class="bi bi-check2"></i> 已入队';
                this.disabled = true;
            } catch (e) {
                showToast(e.message, "错误");
                this.disabled = false;
                this.innerHTML = originalHtml;
            }
        });
    });
}

// 初始化：加载我的歌单 + 默认下载数量
loadPlaylists();
loadDefaultPlaylistLimit();
