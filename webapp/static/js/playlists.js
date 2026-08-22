// 歌单管理页逻辑（我的歌单 + 多平台发现）

let myPlaylistIds = new Set();  // 已关注歌单 ID 集合，用于发现页标记"已添加"
let defaultPlaylistLimit = 50;  // 添加歌单时的默认下载数量（来自设置）

// ==================================================================
// 平台状态管理（配置驱动，与模板 window._AVAILABLE_PLATFORMS 对应）
// ==================================================================
let _currentPlatform = "netease";
let _platformState = {};

function getPlatformState(platform) {
    if (!_platformState[platform]) {
        _platformState[platform] = {
            subtab: "toplists",
            hotPage: 1,
            searchState: { keyword: "", type: "song", limit: 50, page: 1 },
        };
    }
    return _platformState[platform];
}

function _saveCurrentPlatformState() {
    const state = getPlatformState(_currentPlatform);
    state.subtab = _discoverState.subtab;
    state.hotPage = _discoverState.hotPage;
    state.searchState = { ..._searchState };
}

function _restorePlatformState(platform) {
    const state = getPlatformState(platform);
    _discoverState.subtab = state.subtab;
    _discoverState.hotPage = state.hotPage;
    _searchState = { ...state.searchState };

    // 切换子标签激活态
    document.querySelectorAll("#discover-subtabs .nav-link").forEach(n => {
        n.classList.toggle("active", n.dataset.subtab === state.subtab);
    });
    document.querySelectorAll("#tab-discover [id^='subtab-']").forEach(el => el.classList.add("d-none"));
    document.getElementById("subtab-" + state.subtab).classList.remove("d-none");

    // 同步搜索框与表格显示状态
    document.getElementById("search-type").value = _searchState.type;
    document.getElementById("search-keyword").value = _searchState.keyword;
    const isAlbum = _searchState.type === "album";
    document.getElementById("search-result-song").classList.toggle("d-none", isAlbum);
    document.getElementById("search-result-album").classList.toggle("d-none", !isAlbum);
    document.getElementById("btn-search-download-all").style.display = isAlbum ? "none" : "";

    // 加载当前子标签内容
    if (state.subtab === "toplists") {
        loadToplists();
    } else if (state.subtab === "hot") {
        loadCategories();
        loadHotPlaylists();
    } else if (state.subtab === "search") {
        loadSearchResults();
    }
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
// 标签页切换
// ==================================================================
document.querySelectorAll("#playlist-tabs .nav-link").forEach(el => {
    el.addEventListener("click", function(e) {
        e.preventDefault();
        const tab = this.dataset.tab;
        // 切换激活态
        document.querySelectorAll("#playlist-tabs .nav-link").forEach(n => n.classList.remove("active"));
        this.classList.add("active");
        if (tab === "mine") {
            document.getElementById("tab-mine").classList.remove("d-none");
            document.getElementById("tab-discover").classList.add("d-none");
            return;
        }
        // 平台 tab
        const platform = this.dataset.platform;
        if (platform) {
            const showingDiscover = document.getElementById("tab-mine").classList.contains("d-none");
            // 已在当前平台的发现页则无需重复加载
            if (platform === _currentPlatform && showingDiscover) {
                return;
            }
            // 保存当前平台状态
            _saveCurrentPlatformState();
            _currentPlatform = platform;
            // 隐藏 mine，显示 discover
            document.getElementById("tab-mine").classList.add("d-none");
            document.getElementById("tab-discover").classList.remove("d-none");
            // 恢复该平台状态
            _restorePlatformState(platform);
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

        myPlaylistIds = new Set((list || []).map(p => p.id));

        if (!list || list.length === 0) {
            tbody.innerHTML = '<tr><td colspan="7" class="text-center text-muted">暂无关注的歌单，点击右上角添加</td></tr>';
            return;
        }

        tbody.innerHTML = list.map(p => {
            const typeText = p.type === "official" ? "官方榜单" : "自定义";
            const checked = p.enabled ? "checked" : "";
            const syncTime = p.last_synced_at || "从未同步";
            const platformBadge = p.platform_name
                ? `<span class="badge-platform badge ${p.platform === 'qq' ? 'bg-info' : 'bg-primary'}">${p.platform_name}</span> `
                : "";
            return `
                <tr>
                    <td>
                        <div class="form-check form-switch">
                            <input class="form-check-input toggle-enabled" type="checkbox" ${checked} data-id="${p.id}">
                        </div>
                    </td>
                    <td>${platformBadge}${p.name}</td>
                    <td><span class="badge ${p.type === 'official' ? 'bg-info' : 'bg-secondary'}">${typeText}</span></td>
                    <td>
                        <input type="number" class="form-control form-control-sm limit-input" value="${p.limit_count}" data-id="${p.id}" min="1" max="1000" style="width:70px">
                    </td>
                    <td>${p.track_count || 0}</td>
                    <td><small class="text-muted">${syncTime}</small></td>
                    <td>
                        <button class="btn btn-sm btn-outline-primary btn-sync" data-id="${p.id}">
                            <i class="bi bi-arrow-repeat"></i> 同步
                        </button>
                        <button class="btn btn-sm btn-outline-danger btn-delete" data-id="${p.id}" data-name="${p.name}">
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
                    body: JSON.stringify({ enabled: this.checked }),
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
                    body: JSON.stringify({ limit_count: parseInt(this.value) }),
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
                await api(`/api/playlists/${id}`, { method: "DELETE" });
                showToast("已删除");
                loadPlaylists();
            } catch (e) {
                showToast(e.message, "错误");
            }
        });
    });
}

// 添加歌单（我的歌单弹窗 + 发现页共用）
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
// 发现页
// ==================================================================

// 发现页状态：当前子标签、当前页码
let _discoverState = {
    subtab: "toplists",   // toplists / hot
    hotPage: 1,           // 热门歌单当前页
};

// 子标签切换
document.querySelectorAll("#discover-subtabs .nav-link").forEach(el => {
    el.addEventListener("click", function(e) {
        e.preventDefault();
        const subtab = this.dataset.subtab;
        _discoverState.subtab = subtab;
        document.querySelectorAll("#discover-subtabs .nav-link").forEach(n => n.classList.remove("active"));
        this.classList.add("active");
        document.getElementById("subtab-toplists").classList.add("d-none");
        document.getElementById("subtab-hot").classList.add("d-none");
        document.getElementById("subtab-search").classList.add("d-none");
        document.getElementById("subtab-" + subtab).classList.remove("d-none");
        // 首次切到排行榜时加载
        if (subtab === "toplists" && !window._toplistsLoaded) {
            window._toplistsLoaded = true;
            loadToplists();
        }
        // 首次切到热门歌单时加载分类并加载第一页
        if (subtab === "hot" && !window._hotLoaded) {
            window._hotLoaded = true;
            loadCategories();
            loadHotPlaylists();
        }
    });
});

async function loadCategories() {
    try {
        const data = await api("/api/discover/categories?platform=" + _currentPlatform);
        const sel = document.getElementById("discover-cat");
        const cats = data.data || [];
        sel.innerHTML = cats.map(c => `<option value="${c}">${c}</option>`).join("");
    } catch (e) {
        console.error("加载分类失败:", e);
    }
}

async function loadToplists() {
    const grid = document.getElementById("toplist-grid");
    grid.innerHTML = '<div class="col-12 text-center text-muted"><span class="loading-spinner"></span> 加载中...</div>';
    try {
        const data = await api("/api/discover/toplists?platform=" + _currentPlatform);
        const list = data.data || [];
        if (list.length === 0) {
            grid.innerHTML = '<div class="col-12 text-center text-muted">暂无数据</div>';
            return;
        }
        grid.innerHTML = list.map(t => renderCard(t, "official", "toplist")).join("");
        bindAddButtons();
    } catch (e) {
        grid.innerHTML = `<div class="col-12 text-center text-danger">加载失败: ${e.message}</div>`;
    }
}

async function loadHotPlaylists() {
    const grid = document.getElementById("hot-grid");
    const cat = document.getElementById("discover-cat").value;
    const limit = parseInt(document.getElementById("discover-limit").value);
    const order = document.getElementById("discover-order").value;
    const page = _discoverState.hotPage;
    document.getElementById("hot-title").textContent = (cat === "全部" ? "热门歌单" : cat + "歌单") + "（" + (order === "hot" ? "热门" : "最新") + "）";

    grid.innerHTML = '<div class="col-12 text-center text-muted"><span class="loading-spinner"></span> 加载中...</div>';
    try {
        const data = await api(`/api/discover/playlists?cat=${encodeURIComponent(cat)}&limit=${limit}&order=${order}&page=${page}&platform=${_currentPlatform}`);
        const list = data.data || [];
        const total = data.total || 0;
        const pages = data.pages || 0;
        const curPage = data.page || 1;

        if (list.length === 0) {
            grid.innerHTML = '<div class="col-12 text-center text-muted">暂无数据</div>';
        } else {
            grid.innerHTML = list.map(p => renderCard(p, "user", "hot")).join("");
            bindAddButtons();
        }
        // 更新翻页控件
        updatePagination(curPage, pages, total);
    } catch (e) {
        grid.innerHTML = `<div class="col-12 text-center text-danger">加载失败: ${e.message}</div>`;
        updatePagination(1, 0, 0);
    }
}

function updatePagination(curPage, totalPages, total) {
    const info = document.getElementById("hot-page-info");
    const prev = document.getElementById("btn-hot-prev");
    const next = document.getElementById("btn-hot-next");
    info.textContent = `第 ${curPage} 页 / 共 ${totalPages} 页（总 ${total} 个）`;
    prev.disabled = curPage <= 1;
    next.disabled = curPage >= totalPages || totalPages === 0;
}

function renderCard(item, type, source) {
    const added = myPlaylistIds.has(item.id);
    const playCount = item.play_count ? formatPlayCount(item.play_count) : "";
    const cover = item.cover_img_url
        ? `<img src="${item.cover_img_url}" class="card-img-top discover-cover" alt="${item.name}">`
        : `<div class="discover-cover-placeholder"><i class="bi bi-music-note-beamed"></i></div>`;
    const meta = source === "toplist"
        ? (item.update_frequency || "排行榜")
        : (playCount ? "播放 " + playCount : "歌单");
    return `
        <div class="col-lg-2 col-md-3 col-sm-4 col-6">
            <div class="card discover-card h-100">
                ${cover}
                <div class="card-body p-2">
                    <h6 class="card-title text-truncate mb-1" title="${item.name}">${item.name}</h6>
                    <small class="text-muted d-block text-truncate">${meta}</small>
                </div>
                <div class="card-footer p-2 text-center">
                    <button class="btn btn-sm ${added ? 'btn-secondary' : 'btn-outline-primary'} w-100 btn-add-discover"
                        data-id="${item.id}" data-name="${item.name}" data-type="${type}" ${added ? 'disabled' : ''}>
                        ${added ? '<i class="bi bi-check2"></i> 已添加' : '<i class="bi bi-plus"></i> 添加'}
                    </button>
                </div>
            </div>
        </div>
    `;
}

function formatPlayCount(n) {
    if (n >= 100000000) return (n / 100000000).toFixed(1) + "亿";
    if (n >= 10000) return (n / 10000).toFixed(1) + "万";
    return String(n);
}

function bindAddButtons() {
    document.querySelectorAll(".btn-add-discover").forEach(el => {
        el.addEventListener("click", async function() {
            if (this.disabled) return;
            const id = this.dataset.id;
            const name = this.dataset.name;
            const type = this.dataset.type;
            this.disabled = true;
            this.innerHTML = '<span class="loading-spinner"></span>';
            try {
                const data = await addPlaylist(id, type, defaultPlaylistLimit, _currentPlatform);
                showToast(data.msg, "添加成功");
                this.className = "btn btn-sm btn-secondary w-100";
                this.innerHTML = '<i class="bi bi-check2"></i> 已添加';
                myPlaylistIds.add(parseInt(id));
            } catch (e) {
                showToast(e.message, "添加失败");
                this.disabled = false;
                this.innerHTML = '<i class="bi bi-plus"></i> 添加';
            }
        });
    });
}

// 筛选/数量变更时回到第 1 页
document.getElementById("discover-cat").addEventListener("change", () => { _discoverState.hotPage = 1; });
document.getElementById("discover-order").addEventListener("change", () => { _discoverState.hotPage = 1; });
document.getElementById("discover-limit").addEventListener("change", () => { _discoverState.hotPage = 1; loadHotPlaylists(); });

// 查询按钮：回到第 1 页重新查询
document.getElementById("btn-discover-search").addEventListener("click", () => {
    _discoverState.hotPage = 1;
    loadHotPlaylists();
});

// 翻页按钮
document.getElementById("btn-hot-prev").addEventListener("click", () => {
    if (_discoverState.hotPage > 1) {
        _discoverState.hotPage--;
        loadHotPlaylists();
    }
});
document.getElementById("btn-hot-next").addEventListener("click", () => {
    _discoverState.hotPage++;
    loadHotPlaylists();
});

// 初始化：加载我的歌单 + 默认下载数量
loadPlaylists();
loadDefaultPlaylistLimit();

// ==================================================================
// 搜索歌手
// ==================================================================

// 搜索状态：当前关键词、类型、每页数量、页码
let _searchState = {
    keyword: "",
    type: "song",     // song | artist | album
    limit: 50,
    page: 1,
};

// 同步每页数量下拉框到状态
document.getElementById("search-limit").addEventListener("change", function() {
    _searchState.limit = parseInt(this.value) || 50;
    _searchState.page = 1;
});

// 搜索按钮
document.getElementById("btn-search").addEventListener("click", () => {
    const kw = document.getElementById("search-keyword").value.trim();
    if (!kw) { showToast("请输入搜索关键词"); return; }
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
    // 切换表格显示
    const isAlbum = this.value === "album";
    document.getElementById("search-result-song").classList.toggle("d-none", isAlbum);
    document.getElementById("search-result-album").classList.toggle("d-none", !isAlbum);
    // "全部下载"按钮仅歌曲模式显示
    document.getElementById("btn-search-download-all").style.display = isAlbum ? "none" : "";
    // 重置分页
    updateSearchPagination(1, 0, 0);
    // 清空结果
    document.getElementById("search-tbody-song").innerHTML = '<tr><td colspan="7" class="text-center text-muted">输入关键词后点击搜索</td></tr>';
    document.getElementById("search-tbody-album").innerHTML = '<tr><td colspan="6" class="text-center text-muted">输入关键词后点击搜索</td></tr>';
});

// 全部下载（应用排除过滤，仅歌曲模式，下载当前页）
document.getElementById("btn-search-download-all").addEventListener("click", async function() {
    const kw = _searchState.keyword;
    if (!kw) { showToast("请先输入搜索关键词"); return; }
    const limit = _searchState.limit;
    const offset = (_searchState.page - 1) * limit;
    const btn = this;
    btn.disabled = true;
    btn.innerHTML = '<span class="loading-spinner"></span> 下载中...';
    try {
        const data = await api("/api/discover/search-download", {
            method: "POST",
            body: JSON.stringify({ keyword: kw, limit, offset, platform: _currentPlatform }),
        });
        showToast(data.msg, "下载结果");
        // 下载后刷新当前页状态
        loadSearchResults();
    } catch (e) {
        showToast(e.message, "错误");
    } finally {
        btn.disabled = false;
        btn.innerHTML = '<i class="bi bi-cloud-arrow-down"></i> 全部下载';
    }
});

// 分页：上一页
document.getElementById("btn-search-prev").addEventListener("click", () => {
    if (_searchState.page > 1) {
        _searchState.page--;
        loadSearchResults();
    }
});

// 分页：下一页
document.getElementById("btn-search-next").addEventListener("click", () => {
    _searchState.page++;
    loadSearchResults();
});

// 加载搜索结果（根据 _searchState.type 渲染不同表格）
async function loadSearchResults() {
    const keyword = _searchState.keyword;
    const type = _searchState.type;
    const limit = _searchState.limit;
    const page = _searchState.page;
    const offset = (page - 1) * limit;
    const isAlbum = type === "album";

    // 同步下拉框状态（防止外部调用未同步）
    document.getElementById("search-type").value = type;
    document.getElementById("search-result-song").classList.toggle("d-none", isAlbum);
    document.getElementById("search-result-album").classList.toggle("d-none", !isAlbum);
    document.getElementById("btn-search-download-all").style.display = isAlbum ? "none" : "";

    const tbody = document.getElementById(isAlbum ? "search-tbody-album" : "search-tbody-song");
    const titleEl = document.getElementById(isAlbum ? "search-title-album" : "search-title");
    titleEl.textContent = `搜索结果: ${keyword}（第 ${page} 页）`;
    const colspan = isAlbum ? 6 : 7;
    tbody.innerHTML = `<tr><td colspan="${colspan}" class="text-center text-muted"><span class="loading-spinner"></span> 搜索中...</td></tr>`;

    try {
        const data = await api("/api/discover/search", {
            method: "POST",
            body: JSON.stringify({ keyword, type, limit, offset, platform: _currentPlatform }),
        });
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
                        <td>${a.size || 0}</td>
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
            bindAlbumDownload();
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
            bindSingleDownload();
        }
        updateSearchPagination(curPage, pages, total);
    } catch (e) {
        tbody.innerHTML = `<tr><td colspan="${colspan}" class="text-center text-danger">搜索失败: ${escapeHtml(e.message)}</td></tr>`;
        updateSearchPagination(1, 0, 0);
    }
}

// 更新搜索分页控件
function updateSearchPagination(curPage, totalPages, total) {
    const info = document.getElementById("search-page-info");
    const prev = document.getElementById("btn-search-prev");
    const next = document.getElementById("btn-search-next");
    info.textContent = `第 ${curPage} 页 / 共 ${totalPages} 页（总 ${total} 个）`;
    prev.disabled = curPage <= 1;
    next.disabled = curPage >= totalPages || totalPages === 0;
}

// 单首下载（不受排除过滤限制）
function bindSingleDownload() {
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
                    body: JSON.stringify({ song_id: parseInt(songId), name, artists, fee, platform: _currentPlatform }),
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
function bindAlbumDownload() {
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
                    body: JSON.stringify({ album_id: parseInt(albumId), album_name: albumName, platform: _currentPlatform }),
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

// 简单 HTML 转义（防 XSS）
function escapeHtml(str) {
    if (!str) return "";
    return String(str)
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;")
        .replace(/'/g, "&#39;");
}
