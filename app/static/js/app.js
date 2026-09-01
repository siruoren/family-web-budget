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
