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
    // 清理可能残留的浮动弹窗 (AJAX 替换时旧 details 被新 HTML 覆盖,
    // 但 .edit-pop 已被移到 body, 不会被替换, 需主动移除)
    document.querySelectorAll("body > .edit-pop-floating").forEach(function (p) {
      // 若原属 details 已不在文档, 直接移除; 否则移回原位以便 details 正常折叠
      var orig = p._origParent;
      if (orig && document.body.contains(orig)) {
        orig.appendChild(p);
        p.classList.remove("edit-pop-floating");
        p.style.removeProperty("position");
        p.style.removeProperty("top");
        p.style.removeProperty("left");
        p.style.removeProperty("transform");
      } else {
        p.remove();
      }
    });
    document.querySelectorAll(".edit-pop-overlay").forEach(function (o) { o.remove(); });
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

  // ---- 弹窗视口居中 (规避祖先 backdrop-filter 导致 fixed 包含块失效) ----
  // .edit-pop 原为 position:fixed 视口居中, 但设置页祖先 (.admin-sidebar/.card)
  // 有 backdrop-filter:blur, 按 CSS 规范该祖先成为 fixed 后代的包含块, 弹窗
  // 相对卡片(整个内容区)居中而非视口; 且 card 形成独立层叠上下文, z-index
  // 困于其内无法盖外部 nav。修复: 打开 details 时把 .edit-pop 移到 body 末尾
  // (无 backdrop-filter 祖先), fixed 相对视口居中 + 全屏遮罩 + 高 z-index。
  function floatPop(pop, det) {
    if (pop.classList.contains("edit-pop-floating")) return;
    pop._origParent = pop.parentNode;
    pop._origDetails = det;
    document.body.appendChild(pop);
    pop.classList.add("edit-pop-floating");
    pop.style.position = "fixed";
    pop.style.top = "50vh";
    pop.style.left = "50vw";
    pop.style.transform = "translate(-50%, -50%)";
    var ov = document.createElement("div");
    ov.className = "edit-pop-overlay";
    ov.addEventListener("click", function () { unfloatPop(pop); });
    document.body.appendChild(ov);
    pop._overlay = ov;
    pop._escHandler = function (e) {
      if (e.key === "Escape") unfloatPop(pop);
    };
    document.addEventListener("keydown", pop._escHandler);
    var firstInput = pop.querySelector('input:not([type=hidden]), button');
    if (firstInput) setTimeout(function () { firstInput.focus(); }, 0);
  }

  function unfloatPop(pop) {
    if (!pop || !pop.classList.contains("edit-pop-floating")) return;
    var det = pop._origDetails;
    if (det) det.open = false;
    if (pop._overlay) { pop._overlay.remove(); pop._overlay = null; }
    if (pop._origParent && document.body.contains(pop._origParent)) {
      pop._origParent.appendChild(pop);
    } else {
      pop.remove();
    }
    pop.classList.remove("edit-pop-floating");
    pop.style.removeProperty("position");
    pop.style.removeProperty("top");
    pop.style.removeProperty("left");
    pop.style.removeProperty("transform");
    document.removeEventListener("keydown", pop._escHandler);
    pop._origParent = null;
    pop._origDetails = null;
  }
  window.unfloatPop = unfloatPop;

  // click 委托: summary 切换 details.open 后(延时一帧) 移/回弹窗
  document.addEventListener("click", function (ev) {
    var det = ev.target.closest && ev.target.closest("details.inline-edit");
    if (!det) return;
    setTimeout(function () {
      if (det.open) {
        var pop = det.querySelector(":scope > .edit-pop");
        if (pop) floatPop(pop, det);
      } else {
        var all = document.querySelectorAll("body > .edit-pop-floating");
        for (var i = 0; i < all.length; i++) {
          if (all[i]._origDetails === det) { unfloatPop(all[i]); break; }
        }
      }
    }, 0);
  }, true);
})();
