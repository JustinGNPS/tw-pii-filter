/**
 * 右下角的小提示：貼上的內容裡有「格式像個資、但被檢核碼擋下」的號碼。
 *
 * 不擋操作、不需要按任何東西，幾秒後自動消失 —— 貼上已經完成了，
 * 這只是告訴使用者「我們看到了，而且判斷它不是真的號碼」。
 * Shadow DOM 隔離的理由同 panel.ts。
 */

import type { NearMiss } from '../explain';

const SHOW_MS = 8000;
const MAX_ITEMS = 3;

const STYLE = `
  :host { all: initial; }
  .toast {
    position: fixed; right: 16px; bottom: 140px; z-index: 2147483646;
    width: min(360px, calc(100vw - 32px));
    background: #fff; color: #0f172a; border: 1px solid #e2e8f0; border-left: 4px solid #2563eb;
    border-radius: 10px; box-shadow: 0 10px 30px rgba(15, 23, 42, .18);
    padding: 11px 14px 10px; font: 12.5px/1.6 -apple-system, "Segoe UI", "Microsoft JhengHei", sans-serif;
    animation: in .18s ease-out;
  }
  @keyframes in { from { opacity: 0; transform: translateY(6px); } }
  .title { font-weight: 650; margin-bottom: 4px; display: flex; gap: 8px; align-items: baseline; }
  .close { margin-left: auto; cursor: pointer; color: #94a3b8; background: none; border: none; font-size: 15px; line-height: 1; }
  ul { margin: 2px 0 6px; padding-left: 18px; }
  li { margin: 2px 0; }
  code { font-family: ui-monospace, Consolas, monospace; background: #fef2f2; color: #b91c1c; padding: 0 4px; border-radius: 3px; }
  .foot { color: #64748b; font-size: 11.5px; }
`;

let current: HTMLElement | null = null;

function escapeHtml(value: string): string {
  return value.replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]!);
}

export function showNearMissHint(items: NearMiss[]): void {
  if (items.length === 0) return;
  current?.remove();

  const host = document.createElement('tw-pii-hint');
  const root = host.attachShadow({ mode: 'closed' });
  const shown = items.slice(0, MAX_ITEMS);
  const more = items.length - shown.length;
  root.innerHTML = `
    <style>${STYLE}</style>
    <div class="toast" role="status">
      <div class="title">🛡 已照原文貼上：這些不是有效的個資號碼 <button class="close" aria-label="關閉">×</button></div>
      <ul>${shown.map((i) => `<li><code>${escapeHtml(i.value)}</code> ${escapeHtml(i.reason)}</li>`).join('')}
        ${more > 0 ? `<li>……還有 ${more} 個</li>` : ''}</ul>
      <div class="foot">我們會驗證檢核碼，格式像但不合法的號碼不會被誤判成個資。</div>
    </div>`;
  root.querySelector('.close')?.addEventListener('click', () => host.remove());
  document.documentElement.appendChild(host);
  current = host;
  window.setTimeout(() => {
    if (current === host) current = null;
    host.remove();
  }, SHOW_MS);
}
