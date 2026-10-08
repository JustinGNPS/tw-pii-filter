/**
 * 確認面板：偵測到敏感資訊時跳出，讓使用者逐項決定要不要遮蔽。
 *
 * 設計重點：
 *  - **Shadow DOM**：面板注入在別人的網頁上，宿主頁面的 CSS 極具侵略性
 *    （ChatGPT / Gemini 都有大量 global style）。用 shadow root 隔離，
 *    樣式才不會被洗掉，也不會反過來汙染宿主頁面。
 *  - **不做還原**：依專題規劃第一版只做「偵測 + 警告 + 遮蔽」，
 *    還原留到第二版，避免佔位符被 AI 改寫的風險擋住進度。
 *  - **使用者有最終決定權**：每一項都可以取消勾選；偵測是輔助，不是強制。
 */

import type { CombinationRisk, Span } from '../core';
import { isKnownType, riskLevel, typeLabel } from '../masking';

/** 使用者在面板上做出的決定。 */
export type PanelDecision =
  | { action: 'mask'; spans: Span[] } // 遮蔽勾選的項目後貼上
  | { action: 'raw' } // 不遮蔽，直接貼上原文
  | { action: 'cancel' }; // 不貼上

const RISK_TEXT: Record<string, string> = { high: '高', medium: '中', low: '低' };

const STYLE = `
  :host { all: initial; }
  * { box-sizing: border-box; font-family: -apple-system, "Segoe UI", "Microsoft JhengHei", sans-serif; }
  .backdrop {
    position: fixed; inset: 0; z-index: 2147483647;
    background: rgba(15, 23, 42, .45);
    display: flex; align-items: center; justify-content: center;
    padding: 24px;
  }
  .card {
    width: min(560px, 100%); max-height: min(640px, 90vh);
    display: flex; flex-direction: column;
    background: #fff; color: #0f172a;
    border-radius: 14px; box-shadow: 0 20px 50px rgba(0,0,0,.3);
    overflow: hidden;
  }
  header { padding: 18px 20px 14px; border-bottom: 1px solid #e2e8f0; }
  h1 { margin: 0 0 4px; font-size: 16px; font-weight: 650; letter-spacing: .01em; }
  .sub { margin: 0; font-size: 12.5px; color: #64748b; line-height: 1.5; }
  .list { overflow-y: auto; padding: 8px 12px; flex: 1; }
  .row {
    display: flex; align-items: flex-start; gap: 10px;
    padding: 10px; border-radius: 9px;
  }
  .row + .row { border-top: 1px solid #f1f5f9; }
  .row:hover { background: #f8fafc; }
  input[type=checkbox] { margin-top: 3px; width: 15px; height: 15px; accent-color: #2563eb; cursor: pointer; flex: none; }
  .body { flex: 1; min-width: 0; }
  .line1 { display: flex; align-items: center; gap: 7px; flex-wrap: wrap; margin-bottom: 3px; }
  .type { font-size: 13px; font-weight: 600; }
  .badge { font-size: 10.5px; font-weight: 600; padding: 1.5px 6px; border-radius: 999px; letter-spacing: .03em; }
  .badge.high { background: #fee2e2; color: #b91c1c; }
  .badge.medium { background: #fef3c7; color: #b45309; }
  .badge.low { background: #e0e7ff; color: #4338ca; }
  .conf { font-size: 11px; color: #94a3b8; }
  .conf.low-conf { color: #b45309; font-weight: 600; }
  .line2 { font-family: ui-monospace, "Cascadia Mono", Consolas, monospace; font-size: 12px; color: #475569; word-break: break-all; }
  .arrow { color: #94a3b8; margin: 0 5px; }
  .ph { color: #2563eb; font-weight: 600; }
  footer {
    display: flex; align-items: center; gap: 8px;
    padding: 13px 20px; border-top: 1px solid #e2e8f0; background: #f8fafc;
  }
  .spacer { flex: 1; }
  button {
    font-size: 13px; font-weight: 600; padding: 8px 15px;
    border-radius: 8px; border: 1px solid transparent; cursor: pointer;
  }
  .primary { background: #2563eb; color: #fff; }
  .primary:hover { background: #1d4ed8; }
  .ghost { background: #fff; color: #334155; border-color: #cbd5e1; }
  .ghost:hover { background: #f1f5f9; }
  .link { background: none; color: #64748b; padding: 8px 4px; }
  .link:hover { color: #0f172a; text-decoration: underline; }
  .note { font-size: 11.5px; color: #94a3b8; padding: 0 20px 12px; line-height: 1.5; }

  /* 組合風險提示：逐項清單看不出「單項都不算個資、合起來能指認」這件事 */
  .risk {
    margin: 10px 12px 2px; padding: 11px 13px;
    border-radius: 9px; border: 1px solid #fcd34d; background: #fffbeb;
  }
  .risk.high { border-color: #fca5a5; background: #fef2f2; }
  .risk-head { display: flex; align-items: center; gap: 7px; margin-bottom: 5px; }
  .risk-title { font-size: 12.5px; font-weight: 650; color: #92400e; }
  .risk.high .risk-title { color: #b91c1c; }
  .risk-score { font-size: 11px; color: #a16207; margin-left: auto; font-variant-numeric: tabular-nums; }
  .risk.high .risk-score { color: #dc2626; }
  .risk-body { font-size: 11.5px; color: #78350f; line-height: 1.6; }
  .risk.high .risk-body { color: #7f1d1d; }
  .risk-body ul { margin: 5px 0 0; padding-left: 17px; }
  .risk-body li { margin: 2px 0; }

  @media (prefers-color-scheme: dark) {
    .risk { border-color: #a16207; background: #2a2110; }
    .risk.high { border-color: #b91c1c; background: #2c1618; }
    .risk-title { color: #fcd34d; }
    .risk.high .risk-title { color: #fca5a5; }
    .risk-body { color: #fde68a; }
    .risk.high .risk-body { color: #fecaca; }
    .risk-score { color: #d1a54a; }
  }

  @media (prefers-color-scheme: dark) {
    .card { background: #1e293b; color: #e2e8f0; }
    header, footer { border-color: #334155; }
    footer { background: #172033; }
    .row + .row { border-color: #263449; }
    .row:hover { background: #263449; }
    .line2 { color: #94a3b8; }
    .ghost { background: #1e293b; color: #cbd5e1; border-color: #475569; }
    .ghost:hover { background: #334155; }
  }
`;

/**
 * 顯示確認面板，回傳使用者的決定。
 *
 * @param spans        `detectAll` 產出的、互不重疊的 spans
 * @param placeholders 每個 span 預計會被換成的佔位符（與 spans 同索引）。
 *                     刻意由呼叫端傳入而不是用 `span.replacement`——後者每次
 *                     偵測都會重新編號，顯示出來的號碼會跟實際送出的對不上。
 * @param risk         Layer 3 組合風險評分，`null` 表示沒有組合風險。
 *                     逐項清單天生看不出「單項都不算個資、組合起來能指認個人」，
 *                     這一塊就是為了補上那個盲點（見專題報告 4.3）。
 */
export function showPanel(
  spans: Span[],
  placeholders: string[],
  risk: CombinationRisk | null = null,
): Promise<PanelDecision> {
  return new Promise((resolve) => {
    const host = document.createElement('div');
    host.style.cssText = 'all: initial; position: fixed; z-index: 2147483647;';
    const shadow = host.attachShadow({ mode: 'closed' });

    const style = document.createElement('style');
    style.textContent = STYLE;
    shadow.append(style);

    // 一個 span 都沒有、只有組合風險時，標題不能寫「偵測到 0 項」——
    // 那會讓使用者以為沒事，正好抵消掉組合風險提示要傳達的訊息。
    const onlyRisk = spans.length === 0 && risk !== null;
    const heading = onlyRisk ? '這段文字可能可以指認出特定個人' : `偵測到 ${spans.length} 項敏感資訊`;
    const subtitle = onlyRisk
      ? '沒有偵測到身分證、電話這類明確的個資，但下列資訊組合起來仍有識別風險。'
      : '以下內容將在送出前於本地端替換成佔位符。取消勾選的項目會維持原文。';

    const backdrop = document.createElement('div');
    backdrop.className = 'backdrop';
    backdrop.innerHTML = `
      <div class="card" role="dialog" aria-modal="true" aria-label="敏感資訊確認">
        <header>
          <h1>${heading}</h1>
          <p class="sub">${subtitle}</p>
        </header>
        <div class="risk-slot"></div>
        <div class="list"></div>
        <p class="note">分析全程在你的裝置上完成，原文不會離開這台電腦。</p>
        <footer>
          <button class="link" data-act="cancel">不貼上</button>
          <div class="spacer"></div>
          <button class="ghost" data-act="raw">直接貼上原文</button>
          <button class="primary" data-act="mask">遮蔽後貼上</button>
        </footer>
      </div>
    `;

    // 組合風險提示：只在真的有風險時才出現，避免每次都跳一塊黃色區塊而被無視
    if (risk) {
      const box = document.createElement('div');
      box.className = risk.risk_level === '高' ? 'risk high' : 'risk';

      const head = document.createElement('div');
      head.className = 'risk-head';
      const title = document.createElement('span');
      title.className = 'risk-title';
      title.textContent = `⚠ 組合識別風險${risk.risk_level}`;
      const score = document.createElement('span');
      score.className = 'risk-score';
      score.textContent = `分數 ${risk.score.toFixed(2)}`;
      head.append(title, score);

      const body = document.createElement('div');
      body.className = 'risk-body';
      const labels = risk.contributing_types.map((type) => typeLabel(type)).join('、');
      const lead = document.createElement('div');
      // 講清楚「為什麼」——只給一個 0.82 使用者不知道要做什麼
      lead.textContent = `這段文字同時含有${labels}。單獨看都不算個資，但組合起來可能指認出特定個人，即使其他項目已經遮蔽。`;
      body.append(lead);

      if (risk.suggestions.length > 0) {
        const list = document.createElement('ul');
        for (const suggestion of risk.suggestions) {
          const item = document.createElement('li');
          item.textContent = suggestion;
          list.append(item);
        }
        body.append(list);
      }

      box.append(head, body);
      backdrop.querySelector('.risk-slot')!.append(box);
    }

    const list = backdrop.querySelector('.list')!;
    const checkboxes: HTMLInputElement[] = [];

    spans.forEach((span, index) => {
      const risk = riskLevel(span.type);
      const row = document.createElement('label');
      row.className = 'row';

      const checkbox = document.createElement('input');
      checkbox.type = 'checkbox';
      checkbox.checked = true;
      checkbox.dataset.index = String(index);
      checkboxes.push(checkbox);

      const body = document.createElement('div');
      body.className = 'body';

      const line1 = document.createElement('div');
      line1.className = 'line1';
      const typeEl = document.createElement('span');
      typeEl.className = 'type';
      // 語意層的類別代碼還在變動，遇到沒登記過的就標示出來讓使用者自己判斷
      typeEl.textContent = isKnownType(span.type)
        ? typeLabel(span.type)
        : `${span.type}（未知類別）`;
      const badge = document.createElement('span');
      badge.className = `badge ${risk}`;
      badge.textContent = `風險 ${RISK_TEXT[risk]}`;
      const conf = document.createElement('span');
      // 低信心項目特別標示，提醒使用者這筆可能是誤判、值得自己看一眼
      conf.className = span.confidence < 0.7 ? 'conf low-conf' : 'conf';
      conf.textContent =
        span.confidence < 0.7
          ? `信心 ${span.confidence.toFixed(2)}（偏低，請確認）`
          : `信心 ${span.confidence.toFixed(2)}`;
      line1.append(typeEl, badge, conf);

      const line2 = document.createElement('div');
      line2.className = 'line2';
      const original = document.createElement('span');
      original.textContent = span.text;
      const arrow = document.createElement('span');
      arrow.className = 'arrow';
      arrow.textContent = '→';
      const placeholder = document.createElement('span');
      placeholder.className = 'ph';
      placeholder.textContent = placeholders[index];
      line2.append(original, arrow, placeholder);

      body.append(line1, line2);
      row.append(checkbox, body);
      list.append(row);
    });

    const finish = (decision: PanelDecision) => {
      document.removeEventListener('keydown', onKeydown, true);
      host.remove();
      resolve(decision);
    };

    const onKeydown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        event.preventDefault();
        event.stopPropagation();
        finish({ action: 'cancel' });
      }
    };

    backdrop.addEventListener('click', (event) => {
      const target = event.target as HTMLElement;
      const act = target.dataset?.act;
      if (act === 'cancel') return finish({ action: 'cancel' });
      if (act === 'raw') return finish({ action: 'raw' });
      if (act === 'mask') {
        const selected = checkboxes
          .filter((checkbox) => checkbox.checked)
          .map((checkbox) => spans[Number(checkbox.dataset.index)]);
        return finish({ action: 'mask', spans: selected });
      }
      // 點擊背景（非卡片內）視為取消
      if (target === backdrop) return finish({ action: 'cancel' });
    });

    document.addEventListener('keydown', onKeydown, true);
    shadow.append(backdrop);
    document.documentElement.append(host);

    backdrop.querySelector<HTMLButtonElement>('.primary')?.focus();
  });
}
