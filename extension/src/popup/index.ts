/**
 * Popup：不需要開任何 AI 網站就能測試偵測核心。
 *
 * 這支的用途有三個：
 *  1. **demo**：簡報時直接打開擴充貼一段文字，三秒看到結果，不必開 ChatGPT
 *  2. **開發驗證**：規則層改動後最快的手動回歸方式
 *  3. **延遲量化**：顯示實際偵測耗時，直接對應風險清單 R2（延遲 > 1 秒）
 */

import { detectAll } from '../core';
import { maskText, riskLevel, typeLabel } from '../masking';
import { requestSemanticScan } from '../semantic';
import {
  configurableTypes,
  filterEnabled,
  loadEnabledTypes,
  loadSemanticEnabled,
  saveEnabledTypes,
  saveSemanticEnabled,
} from '../settings';

const SAMPLE = [
  '病歷摘要：病患王小明，身分證 A123456789，聯絡電話 0912-345-678，',
  '住台北市大安區，信箱 patient@hospital.tw。',
  '目前任職於創新科技股份有限公司，職稱為資安工程師。',
  '負責公司統編 12345675，市話 02-23456789。',
  '系統金鑰 API_KEY=sk-abcdefghijklmnopqrstuvwx1234 請勿外流。',
].join('\n');

const input = document.querySelector<HTMLTextAreaElement>('#input')!;
const result = document.querySelector<HTMLDivElement>('#result')!;
const stat = document.querySelector<HTMLSpanElement>('#stat')!;
const typeBox = document.querySelector<HTMLDivElement>('#types')!;
const semanticToggle = document.querySelector<HTMLInputElement>('#semantic-enabled')!;
const semanticStatus = document.querySelector<HTMLParagraphElement>('#semantic-status')!;

let enabledTypes = new Set<string>();
let semanticEnabled = false;
let scanSequence = 0;

function escapeHtml(value: string): string {
  return value.replace(
    /[&<>"']/g,
    (char) =>
      ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[char]!,
  );
}

function renderScan(text: string, extraSpans: Parameters<typeof detectAll>[1] = undefined, note = ''): void {
  if (text.trim().length === 0) {
    result.innerHTML = '<div class="empty">還沒有輸入文字</div>';
    stat.textContent = '';
    return;
  }

  // 量測偵測耗時，對應風險清單 R2：延遲超過 1 秒使用者就不會用
  const started = performance.now();
  const all = detectAll(text, extraSpans).spans;
  const elapsed = performance.now() - started;
  const spans = filterEnabled(all, enabledTypes);
  const off = all.length - spans.length;

  stat.innerHTML =
    `偵測 <b>${spans.length}</b> 項 · <b>${elapsed.toFixed(1)}</b> ms` +
    (off ? ` · <span title="在下方設定中關閉的類型">略過 ${off}</span>` : '') +
    (note ? ` · ${escapeHtml(note)}` : '');

  if (spans.length === 0) {
    result.innerHTML = '<div class="empty">未偵測到敏感資訊</div>';
    return;
  }

  const { maskedText } = maskText(text, spans);

  const rows = spans
    .map((span) => {
      const risk = riskLevel(span.type);
      const riskText = { high: '高', medium: '中', low: '低' }[risk];
      return `
        <div class="row">
          <span class="badge ${risk}">${riskText}</span>
          <span>${escapeHtml(typeLabel(span.type))}</span>
          <span class="val">${escapeHtml(span.text)}</span>
          <span class="muted">→</span>
          <span class="val ph">${escapeHtml(span.replacement)}</span>
        </div>`;
    })
    .join('');

  result.innerHTML = `
    <h2>偵測項目</h2>
    <div class="rows">${rows}</div>
    <h2>遮蔽後（實際會送給 AI 的內容）</h2>
    <pre>${escapeHtml(maskedText)}</pre>
  `;
}

function scanRulesOnly(): void {
  renderScan(input.value, undefined, semanticEnabled ? '按「掃描」執行語意層' : '規則層');
}

async function scan(): Promise<void> {
  const text = input.value;
  const sequence = ++scanSequence;
  if (!semanticEnabled || !text.trim()) {
    renderScan(text, undefined, '規則層');
    return;
  }
  semanticStatus.classList.remove('error');
  semanticStatus.textContent = '正在連線本機 Proxy 執行語意分析……';
  stat.textContent = '語意分析中…';
  try {
    const semantic = await requestSemanticScan(text);
    if (sequence !== scanSequence) return;
    renderScan(text, semantic.spans, `語意 ${semantic.detectMs.toFixed(0)} ms`);
    semanticStatus.textContent = `語意層正常，這次回傳 ${semantic.spans.length} 項。`;
  } catch (error) {
    if (sequence !== scanSequence) return;
    const message = error instanceof Error ? error.message : '無法連線本機語意服務';
    renderScan(text, undefined, '僅規則層');
    semanticStatus.classList.add('error');
    semanticStatus.textContent = `語意層未使用：${message}`;
  }
}

document.querySelector('#scan')!.addEventListener('click', () => void scan());
document.querySelector('#sample')!.addEventListener('click', () => {
  input.value = SAMPLE;
  void scan();
});

// 邊打邊掃，讓延遲問題在開發階段就無所遁形
input.addEventListener('input', scanRulesOnly);

// ── 設定：要攔截哪些類型 ──
function renderTypes(): void {
  typeBox.innerHTML = configurableTypes()
    .map(
      ({ code, label }) =>
        `<label><input type="checkbox" data-type="${code}" ${enabledTypes.has(code) ? 'checked' : ''}> ${escapeHtml(label)}</label>`,
    )
    .join('');
}

typeBox.addEventListener('change', () => {
  enabledTypes = new Set(
    [...typeBox.querySelectorAll<HTMLInputElement>('input[data-type]')]
      .filter((box) => box.checked)
      .map((box) => box.dataset.type!),
  );
  void saveEnabledTypes(enabledTypes);
  scanRulesOnly();
});

semanticToggle.addEventListener('change', () => {
  semanticEnabled = semanticToggle.checked;
  void saveSemanticEnabled(semanticEnabled);
  semanticStatus.classList.remove('error');
  semanticStatus.textContent = semanticEnabled
    ? '已開啟；按「掃描」或在支援的 AI 網頁貼上時，會呼叫 127.0.0.1。'
    : '已關閉，只使用瀏覽器規則層。';
  scanRulesOnly();
});

void Promise.all([loadEnabledTypes(), loadSemanticEnabled()]).then(([types, semantic]) => {
  enabledTypes = types;
  semanticEnabled = semantic;
  semanticToggle.checked = semanticEnabled;
  renderTypes();
  scanRulesOnly();
});
