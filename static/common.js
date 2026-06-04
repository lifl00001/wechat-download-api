// WeChat Download API — 共享前端组件
// 所有管理页面引入后自动获得：导航栏、Toast、空状态、用户信息

(function() {
    'use strict';

    // 页面配置
    var NAV_PAGES = [
        { path: '/admin.html',       label: '管理面板', icon: '📊' },
        { path: '/rss.html',         label: '订阅公众号',  icon: '📡' },
        { path: '/news.html',        label: '新闻搜索', icon: '🔍' },
        { path: '/blacklist.html',   label: '黑名单',   icon: '🚫' },
        { path: '/history.html',     label: '历史文章', icon: '📜' },
        { path: '/articles.html',    label: '文章库',   icon: '📚' },
    ];

    // ── 注入导航栏 ───────────────────────────────────────

    function injectNav() {
        if (document.getElementById('top-nav')) { loadUserInfo(); return; }

        var currentPath = window.location.pathname;
        var linksHtml = '';
        NAV_PAGES.forEach(function(p) {
            var active = (currentPath === p.path || (p.path === '/admin.html' && currentPath === '/')) ? 'active' : '';
            linksHtml += '<a href="' + p.path + '" class="' + active + '">' + p.icon + ' ' + p.label + '</a>';
        });

        var nav = document.createElement('div');
        nav.id = 'top-nav';
        nav.className = 'top-nav';
        nav.innerHTML =
            '<div class="top-nav-left">' +
                '<div class="top-nav-logo">W</div>' +
                '<div class="top-nav-title">WeChat Download API</div>' +
                '<button class="top-nav-menu-btn" onclick="window.__toggleNavMenu()" aria-label="菜单">' +
                    '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="3" y1="6" x2="21" y2="6"/><line x1="3" y1="12" x2="21" y2="12"/><line x1="3" y1="18" x2="21" y2="18"/></svg>' +
                '</button>' +
            '</div>' +
            '<div class="top-nav-links" id="top-nav-links">' + linksHtml + '</div>' +
            '<div class="top-nav-right">' +
                '<span class="top-nav-user" id="nav-user-name"></span>' +
                '<button class="top-nav-logout" onclick="window.__doNavLogout()">退出</button>' +
            '</div>';

        document.body.insertBefore(nav, document.body.firstChild);
        document.body.classList.add('has-top-nav');

        // 加载用户信息
        loadUserInfo();
    }

    window.__toggleNavMenu = function() {
        document.getElementById('top-nav-links').classList.toggle('open');
    };

    window.__doNavLogout = function() {
        if (!confirm('确定退出登录？')) return;
        window.location.href = '/login.html';
    };

    // ── 加载用户信息 ─────────────────────────────────────

    function loadUserInfo() {
        fetch('/api/auth/me')
          .then(function(r) { return r.json(); })
          .then(function(data) {
            if (data.user_name) {
                var el = document.getElementById('nav-user-name');
                if (el) el.textContent = data.user_name;
            }
        }).catch(function(){});
    }

    // ── 带认证的 API 请求 ───────────────────────────────

    window.apiFetch = function(url, options) {
        options = options || {};
        return fetch(url, options);
    };

    // ── Toast 通知 ───────────────────────────────────────

    function ensureToastContainer() {
        var c = document.getElementById('toast-container');
        if (!c) {
            c = document.createElement('div');
            c.id = 'toast-container';
            document.body.appendChild(c);
        }
        return c;
    }

    window.showToast = function(message, type) {
        type = type || 'info';
        var container = ensureToastContainer();
        var toast = document.createElement('div');
        toast.className = 'toast ' + type;

        var icons = {
            success: '✓',
            error: '✕',
            info: 'ℹ'
        };
        toast.innerHTML = '<span>' + (icons[type] || '•') + '</span><span>' + message + '</span>';
        container.appendChild(toast);

        setTimeout(function() {
            toast.style.opacity = '0';
            toast.style.transform = 'translateX(20px)';
            toast.style.transition = 'all 0.3s';
            setTimeout(function() { toast.remove(); }, 300);
        }, 3000);
    };

    // ── 空状态渲染 ───────────────────────────────────────

    window.renderEmptyState = function(containerSelector, options) {
        options = options || {};
        var container = typeof containerSelector === 'string'
            ? document.querySelector(containerSelector)
            : containerSelector;
        if (!container) return;

        var icon = options.icon || '📭';
        var title = options.title || '暂无数据';
        var desc = options.desc || '';
        var actionHtml = '';
        if (options.actionText && options.actionHref) {
            actionHtml = '<a href="' + options.actionHref + '" class="empty-state-action">' + options.actionText + '</a>';
        }

        container.innerHTML =
            '<div class="empty-state">' +
                '<div class="empty-state-icon">' + icon + '</div>' +
                '<div class="empty-state-title">' + title + '</div>' +
                (desc ? '<div class="empty-state-desc">' + desc + '</div>' : '') +
                actionHtml +
            '</div>';
    };

    // ── 页面使用说明数据 ─────────────────────────────────

    window.PAGE_GUIDES = {
        '/admin.html': {
            title: '管理面板',
            desc: '查看微信登录状态、管理系统配置、测试文章解析接口。首次使用请先完成微信扫码登录。',
            tip: '💡 提示：点击「系统配置」可实时修改限频、RSS轮询间隔、Webhook等参数，无需重启服务。'
        },
        '/rss.html': {
            title: '订阅公众号管理',
            desc: '搜索公众号并添加 订阅公众号，后台会自动定时拉取最新文章。订阅后可获取 RSS 链接，导入到任意 RSS 阅读器。',
            tip: '💡 提示：添加订阅后，可点击「手动触发轮询」立即获取文章。历史文章需通过「历史文章」页面单独拉取。'
        },

        '/blacklist.html': {
            title: '黑名单管理',
            desc: '查看因频繁触发验证码而被自动加入黑名单的公众号。黑名单中的公众号将暂停 RSS 轮询。',
            tip: '💡 提示：移除黑名单后，该公众号将在下一次轮询周期恢复自动拉取。'
        },
        '/history.html': {
            title: '历史文章获取',
            desc: '选择已订阅的公众号，批量获取其历史文章（订阅前发布的文章）。获取的文章会标记为「历史文章」，与常规 RSS 分离。',
            tip: '⚠️ 注意：大量获取历史文章可能触发微信风控，建议每次不超过 100 篇，间隔操作。'
        },
        '/articles.html': {
            title: '文章库',
            desc: '浏览本地数据库中已缓存的所有文章，支持搜索、筛选、查看详情。',
            tip: '💡 提示：文章正文中图片已自动代理，可直接在 RSS 阅读器中显示。'
        },
        '/news.html': {
            title: '新闻搜索',
            desc: '配置百度+Tavily双引擎新闻搜索源，定时采集投资日报所需的新闻数据。',
            tip: '💡 提示：百度擅长中文财经新闻，Tavily擅长全球英文新闻+全文。两者互补使用效果最佳。'
        }
    };

    // ── 注入页面头部（面包屑 + 标题 + 说明）────────────────

    window.injectPageHeader = function(containerSelector) {
        var guide = window.PAGE_GUIDES[window.location.pathname];
        if (!guide) guide = window.PAGE_GUIDES['/admin.html'];

        var container = typeof containerSelector === 'string'
            ? document.querySelector(containerSelector)
            : containerSelector;
        if (!container) return;

        var html =
            '<div class="page-header">' +
                '<div class="page-breadcrumb">' +
                    '<a href="/admin.html">首页</a> / <span>' + guide.title + '</span>' +
                '</div>' +
                '<div class="page-title">' + guide.title + '</div>' +
                '<div class="page-desc">' + guide.desc + '</div>' +
            '</div>';

        if (guide.tip) {
            var tipType = guide.tip.indexOf('⚠️') >= 0 ? 'warning' : '';
            html += '<div class="help-tip ' + tipType + '">' + guide.tip + '</div>';
        }

        container.insertAdjacentHTML('afterbegin', html);
    };

    // ── 初始化 ───────────────────────────────────────────

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', function() { injectNav(); });
    } else {
        injectNav();
    }
})();
