export function el(tag, attrs = {}, children = []) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs || {})) {
    if (key === "class") node.className = value;
    else if (key === "text") node.textContent = value;
    else if (key === "html") node.innerHTML = value;
    else if (key.startsWith("on") && typeof value === "function") node.addEventListener(key.slice(2).toLowerCase(), value);
    else if (value !== false && value !== undefined && value !== null) node.setAttribute(key, String(value));
  }
  for (const child of Array.isArray(children) ? children : [children]) {
    if (child === undefined || child === null) continue;
    node.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return node;
}

export function empty(node) {
  while (node.firstChild) node.firstChild.remove();
  return node;
}

export const asArray = (value) => Array.isArray(value) ? value : [];
export const short = (value, len = 80) => {
  const text = String(value ?? "");
  return text.length > len ? `${text.slice(0, Math.max(0, len - 1))}...` : text;
};
export const pretty = (value) => {
  try { return JSON.stringify(value, null, 2); } catch { return String(value); }
};
export function tone(value) {
  const text = String(value || "").toLowerCase();
  if (["done", "completed", "ready", "success", "healthy", "approved", "已完成", "完成", "就绪", "成功", "健康", "已批准", "已对齐", "在线", "通过"].some((term) => text.includes(term))) return "good";
  if (["failed", "blocked", "error", "expired", "stale", "missing", "violation", "失败", "阻塞", "错误", "过期", "缺失", "违反", "不可达"].some((term) => text.includes(term))) return "bad";
  if (["running", "claimed", "pending", "hold", "warning", "medium", "requires", "needs", "check", "运行", "待处理", "排队", "暂停", "警告", "中", "需要", "检查", "有风险", "可领取"].some((term) => text.includes(term))) return "warn";
  return "info";
}
export const valueOf = (id) => document.getElementById(id)?.value || "";
