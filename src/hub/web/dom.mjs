function append(parent, child) {
  if (child === null || child === undefined || child === false) return;
  if (Array.isArray(child)) {
    child.forEach((item) => append(parent, item));
    return;
  }
  parent.appendChild(
    typeof child === "string" || typeof child === "number"
      ? document.createTextNode(String(child))
      : child,
  );
}

export function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [name, value] of Object.entries(attrs || {})) {
    if (value === null || value === undefined || value === false) continue;
    if (/^on[A-Z]/.test(name) && typeof value === "function") {
      node.addEventListener(name.slice(2).toLowerCase(), value);
    } else if (name.startsWith("aria-") || name.startsWith("data-") || name === "role") {
      node.setAttribute(name, String(value));
    } else if (name in node) {
      node[name] = value;
    } else {
      node.setAttribute(name, String(value));
    }
  }
  children.forEach((child) => append(node, child));
  return node;
}

export function button(label, fn, attrs = {}) {
  return el("button", { type: "button", ...attrs, onClick: fn }, label);
}

