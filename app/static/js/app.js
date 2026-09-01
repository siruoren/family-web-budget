/* 家庭记账单 - 前端交互 */
function fmtMoney(v) {
  v = Number(v || 0);
  return v.toLocaleString("zh-CN", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

function fmtPct(v) {
  v = Number(v || 0);
  return v.toFixed(2) + "%";
}

// 通用: 单条 AJAX 保存
async function quickSaveEntry(year, month, itemId, value, note) {
  const res = await fetch("/entries/quick", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ year, month, item_id: itemId, value, note: note || "" }),
  });
  return res.json();
}

// 删除按钮二次确认 (避免误删, 不抖动)
function confirmDelete(formId, message) {
  if (!confirm(message || "确认删除?")) return false;
  return true;
}

// 月份选择器: 自动随 query 提交
document.addEventListener("DOMContentLoaded", () => {
  // 给所有 .auto-submit 表单绑定 change 事件
  document.querySelectorAll("form.auto-submit select").forEach((sel) => {
    sel.addEventListener("change", (e) => e.target.form.submit());
  });
});
