/* 家庭记账单 - 前端交互 */
function fmtMoney(v) {
  v = Number(v || 0);
  return v.toLocaleString("zh-CN", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

function fmtPct(v) {
  v = Number(v || 0);
  return v.toFixed(2) + "%";
}

// CSRF token: 从 meta 标签读取, 用于 AJAX 请求
function getCSRFToken() {
  const meta = document.querySelector('meta[name="csrf-token"]');
  return meta ? meta.getAttribute("content") : "";
}

// 通用: 单条 AJAX 保存 (带错误处理 + 加载状态)
async function quickSaveEntry(year, month, itemId, value, note, btnEl) {
  if (btnEl) { btnEl.disabled = true; btnEl.dataset.originalText = btnEl.textContent; btnEl.textContent = "保存中…"; }
  try {
    const res = await fetch("/entries/quick", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-CSRFToken": getCSRFToken(),
      },
      body: JSON.stringify({ year, month, item_id: itemId, value, note: note || "" }),
    });
    if (!res.ok) throw new Error("HTTP " + res.status);
    return await res.json();
  } catch (err) {
    console.error("保存失败:", err);
    alert("保存失败, 请检查网络后重试");
    return { ok: false, error: String(err) };
  } finally {
    if (btnEl) { btnEl.disabled = false; btnEl.textContent = btnEl.dataset.originalText || "保存"; }
  }
}

// 删除按钮二次确认 (formId 可为空, 退化为全局确认)
function confirmDelete(formId, message) {
  if (!confirm(message || "确认删除?")) return false;
  return true;
}

// 月份选择器: 自动随 query 提交
document.addEventListener("DOMContentLoaded", () => {
  document.querySelectorAll("form.auto-submit select").forEach((sel) => {
    sel.addEventListener("change", (e) => e.target.form.submit());
  });
});

// 侧边栏: 根据当前 URL 自动展开对应子菜单分支并高亮
// 解决 <details> 在页面跳转后回到收起状态的问题
document.addEventListener("DOMContentLoaded", () => {
  const normUrl = (rawUrl) => {
    // 统一比较 pathname + 排序后的 query, 规避参数顺序与中文编码差异
    let u;
    try { u = new URL(rawUrl, location.origin); }
    catch (e) { return ""; }
    const sp = Array.from(u.searchParams.entries())
      .sort((a, b) => a[0].localeCompare(b[0]))
      .map(([k, v]) => `${k}=${v}`)
      .join("&");
    return u.pathname + (sp ? "?" + sp : "");
  };

  const curKey = normUrl(location.href);
  if (!curKey) return;

  let activeLink = null;
  document.querySelectorAll(".tree-leaf-link").forEach((link) => {
    const href = link.getAttribute("href") || "";
    if (!href || href === "#" || href.startsWith("javascript:")) return;
    if (normUrl(link.href) === curKey) {
      activeLink = link;
    }
  });

  if (!activeLink) return;

  // 高亮当前叶子链接
  activeLink.classList.add("active");

  // 展开所有祖先 <details.tree-det>, 使当前分支保持展开
  let el = activeLink.parentElement;
  while (el && el !== document) {
    if (el.tagName === "DETAILS" && el.classList.contains("tree-det")) {
      el.open = true;
    }
    el = el.parentElement;
  }
});
