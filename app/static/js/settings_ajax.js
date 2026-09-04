/* 系统配置页 - AJAX 无刷新操作
 *
 * 思路: 服务端写路由在检测到 X-Requested-With 时返回 JSON
 *   { ok, msg, sections: { name: html, ... } }
 * 前端用事件委托拦截 form[data-api] 提交, 成功后按 sections 整段替换对应
 * <section data-section="name"> 的 outerHTML, 页面不刷新、滚动位置不变。
 */
(function () {
  "use strict";

  // ---- toast ----
  function ensureToastBox() {
    var box = document.getElementById("settings-toast-box");
    if (!box) {
      box = document.createElement("div");
      box.id = "settings-toast-box";
      box.className = "settings-toast-box";
      document.body.appendChild(box);
    }
    return box;
  }

  function toast(msg, kind) {
    var box = ensureToastBox();
    var item = document.createElement("div");
    item.className = "settings-toast " + (kind === "error" ? "is-error" : "is-ok");
    item.textContent = msg;
    box.appendChild(item);
    // 入场动画
    requestAnimationFrame(function () { item.classList.add("show"); });
    setTimeout(function () {
      item.classList.remove("show");
      setTimeout(function () { if (item.parentNode) item.parentNode.removeChild(item); }, 300);
    }, 3600);
  }

  // ---- CSRF ----
  function csrfToken() {
    var meta = document.querySelector('meta[name="csrf-token"]');
    return meta ? meta.getAttribute("content") : "";
  }

  // ---- 分片替换 ----
  function swapSections(sections) {
    Object.keys(sections || {}).forEach(function (name) {
      var el = document.querySelector('[data-section="' + name + '"]');
      if (!el) return;
      var holder = document.createElement("div");
      holder.innerHTML = String(sections[name]).trim();
      var next = holder.firstElementChild;
      if (!next) return;
      // 记住替换前后的滚动锚点: 保持视口稳定
      el.replaceWith(next);
    });
  }

  // ---- 公式校验 (从内联脚本迁移, 全局可用) ----
  // 公式文本为描述性表达, 仅用于展示 (实际计算采用 services/formula.py 里
  // 硬编码的双视角逻辑, 不解析此文本); 因此后端 update_formula 也只校验非空。
  // 这里不再强制每个 token 必须是已存在的账目类型名 —— 否则默认描述性公式
  // (含分号/圆括号修饰/逗号/语义标签如 "本月结余" "其他支出") 会被误判为非法。
  function validateFormula() {
    var input = document.getElementById("formula-input");
    if (!input) return true;
    var raw = input.value.trim();
    if (!raw) { toast("公式不能为空", "error"); return false; }
    return true;
  }
  // 暴露给可能的内联调用
  window.validateFormula = validateFormula;

  // ---- 设置按钮加载态 ----
  function setLoading(btn, loading) {
    if (!btn) return;
    if (loading) {
      btn.dataset.origText = btn.textContent;
      btn.disabled = true;
      btn.classList.add("btn-loading");
      btn.textContent = "处理中…";
    } else {
      if (btn.dataset.origText !== undefined) btn.textContent = btn.dataset.origText;
      btn.disabled = false;
      btn.classList.remove("btn-loading");
    }
  }

  // ---- 表单提交委托 ----
  document.addEventListener("submit", function (ev) {
    var form = ev.target;
    if (!form || form.tagName !== "FORM" || !form.dataset.api) return;
    ev.preventDefault();

    // 二次确认
    if (form.dataset.confirm) {
      if (!window.confirm(form.dataset.confirm)) return;
    }

    // 公式表单: 先校验
    if (form.dataset.validateFormula && !validateFormula()) return;

    var btn = form.querySelector('button[type="submit"]') ||
              form.querySelector('button:not([type="button"])');
    setLoading(btn, true);

    var fd = new FormData(form);
    fetch(form.action, {
      method: "POST",
      body: fd,
      headers: { "X-Requested-With": "XMLHttpRequest" },
      credentials: "same-origin",
    }).then(function (res) {
      // 即使 4xx 也尝试解析 JSON
      var ct = res.headers.get("content-type") || "";
      if (ct.indexOf("application/json") >= 0) return res.json().then(function (j) {
        return { ok: res.ok, json: j };
      });
      // 非 JSON (如重定向后的 HTML) - 视为需整页刷新
      return res.text().then(function () { return { ok: res.ok, json: null, fallback: true }; });
    }).then(function (r) {
      setLoading(btn, false);
      if (r.fallback) {
        // 服务端返回了非 JSON (例如管理员门禁重定向到登录页) - 按传统跳转
        window.location.reload();
        return;
      }
      var j = r.json || {};
      if (j.ok) {
        swapSections(j.sections || {});
        // 含文件输入的表单成功后清空, 避免误重复导入
        if (form.querySelector('input[type="file"]')) form.reset();
        toast(j.msg || "操作成功", "ok");
        // 服务端要求整页刷新 (如改用户名后顶栏需更新)
        if (j.reload) {
          setTimeout(function () { window.location.reload(); }, 900);
        }
      } else {
        toast(j.msg || "操作失败", "error");
      }
    }).catch(function (err) {
      setLoading(btn, false);
      toast("请求失败: " + (err && err.message ? err.message : err), "error");
    });
  }, true);

  // ---- 公式 chip 点击委托 (光标处插入) ----
  document.addEventListener("click", function (ev) {
    var btn = ev.target.closest && ev.target.closest(".chip-formula");
    if (!btn) return;
    var input = document.getElementById("formula-input");
    if (!input) return;
    ev.preventDefault();
    var txt = btn.getAttribute("data-insert") || "";
    var s = input.selectionStart != null ? input.selectionStart : input.value.length;
    var e = input.selectionEnd != null ? input.selectionEnd : s;
    input.value = input.value.slice(0, s) + txt + input.value.slice(e);
    var pos = s + txt.length;
    input.focus();
    try { input.setSelectionRange(pos, pos); } catch (ex) {}
  });
})();
