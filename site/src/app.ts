/**
 * 靜態展示網站的前端邏輯。
 *
 * ## 重點：這裡跑的是真的偵測，不是預錄的假資料
 *
 * 偵測、遮蔽、配號三件事全部 import 自 `extension/src/`——也就是瀏覽器擴充
 * 正在用的同一份 TypeScript 實作，不是為了展示另外寫一套。因此這個頁面上
 * 看到的結果，與使用者實際安裝擴充後得到的結果是同一套程式算出來的，
 * 而那份實作又有 `extension/tests/parity.test.ts` 在把關它與 Python 版一致。
 *
 * 另外寫一份「展示用的簡化版」會讓這個頁面變成行銷素材而不是證據，
 * 那正好違背本專題要證明的事。
 *
 * ## 為什麼沒有後端
 *
 * 本作品的核心主張是「資料不出裝置」。展示頁若把使用者貼進來的文字送到
 * 伺服器去掃描，主張當場破功。所以這頁沒有任何 fetch/XHR，
 * 打包後是一個可以離線開啟的 HTML 檔。
 *
 * ## 語意層（L2）與組合風險（L3）不在這裡
 *
 * 語意層需要下載 NER 模型權重（數百 MB），不適合放進網頁；
 * 組合風險的 TypeScript 移植尚未併入主線。頁面上已明確標示這件事，
 * 不假裝這裡展示的是完整系統。
 */

import { detectAll, type Span } from '../../extension/src/core';
import { maskText, riskLevel, typeLabel } from '../../extension/src/masking';
import { PlaceholderAllocator } from '../../extension/src/placeholder';

// ────────────────────────────────────────────────────────────
// 範例資料
//
// 身分證與統編的檢核碼**必須是有效的**，否則偵測器會正確地不理它、
// 展示反而失敗（「驗檢核碼」正是本作品與其他工具的差別所在）。
//
// 但這一頁是公開網頁，而本專案的 .gitignore 明文規定：checksum 驗得過的
// 合成號碼不進公開 repo——「格式與真號無異，等同散布一份可直接使用的
// 身分證號清單，且不排除隨機撞到真人號碼」。
//
// 因此這裡**不用隨機產生的號碼**，一律採用早已通行的教科書測試值：
// A123456789 / B123456780（各種教學文件的標準範例）、
// 12345675 / 98765438（順號與疊號，一眼看得出是假的）、
// 4111111111111111（Visa 官方公告的測試卡號）。
// 它們本來就是公開的測試值，不構成「新散布一批可用號碼」。
// ────────────────────────────────────────────────────────────
const SAMPLES: Record<string, string> = {
  service: `客服通話紀錄 #20261008-042
來電者：王大明，身分證 A123456789，手機 0912345678
公司統編 12345675，市話 02-27208889
電子郵件 wang.daming@example.com.tw
問題：上個月的信用卡 4111111111111111 重複扣款兩次，要求退費。
請幫我草擬一封道歉信並說明退費流程。`,

  resume: `姓名：陳美玲
聯絡電話：0912345678
Email：meiling.chen@example.com
身分證字號：B123456780
現職：新竹某半導體公司 資深後端工程師
希望應徵貴公司（統編 98765438）的技術主管職位。

請幫我看看這份履歷還有什麼可以加強的地方。`,

  code: `# .env
OPENAI_API_KEY=sk-proj-aB3dE5fG7hJ9kL1mN3pQ5rS7tU9vW1xY3zA5bC7dE9fG1hJ3

# customers.csv 第 1 筆
姓名,身分證,電話,信箱
王大明,A123456789,0912345678,wang@example.com

這段匯出程式有 bug，幫我看一下為什麼客戶資料會重複。`,
};

const $ = <T extends HTMLElement>(id: string): T =>
  document.getElementById(id) as T;

const input = $<HTMLTextAreaElement>('input');
const reply = $<HTMLTextAreaElement>('reply');
const outOrig = $<HTMLDivElement>('out-orig');
const outMasked = $<HTMLDivElement>('out-masked');
const outRestored = $<HTMLDivElement>('out-restored');
const detailTable = $<HTMLTableElement>('detail-table');
const detailBody = $<HTMLTableSectionElement>('detail-body');
const detailEmpty = $<HTMLDivElement>('detail-empty');

/** 目前這段輸入的對照表（佔位符 → 真值）。只存在記憶體，重新輸入就重建。 */
let mapping = new Map<string, string>();

function escapeHtml(s: string): string {
  return s
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;');
}

/**
 * 把原文中偵測到的片段標起來。
 *
 * 由後往前插入標記，理由與遮蔽完全相同：先動前面會讓後面 span 的
 * 座標失效（docs/B_design.md 決定 4）。這是整個專題最容易踩的坑，
 * 展示頁自己也躲不掉。
 */
function highlight(text: string, spans: Span[]): string {
  const sorted = [...spans].sort((a, b) => a.start - b.start);
  let html = '';
  let cursor = 0;
  for (const span of sorted) {
    html += escapeHtml(text.slice(cursor, span.start));
    html +=
      `<mark class="r-${riskLevel(span.type)}" title="${escapeHtml(typeLabel(span.type))}">` +
      `${escapeHtml(span.text)}</mark>`;
    cursor = span.end;
  }
  html += escapeHtml(text.slice(cursor));
  return html;
}

/** 把遮蔽後文字裡的佔位符標成藍色，讓「雲端看到什麼」一眼可辨。 */
function highlightTokens(masked: string): string {
  return escapeHtml(masked).replace(
    /\[[A-Z][A-Z_]*_\d+\]/g,
    (token) => `<span class="tok">${token}</span>`,
  );
}

/** 依目前的對照表組一段「像那麼回事」的 AI 回覆，讓使用者不必自己想。 */
function buildFakeReply(): string {
  const tokens = [...mapping.keys()];
  if (tokens.length === 0) return '';
  const lines = [
    '我看過這份資料了，以下是我的建議：',
    '',
    ...tokens
      .slice(0, 5)
      .map((t, i) => `${i + 1}. ${t} 屬於敏感欄位，不應以明文儲存或轉傳。`),
  ];
  if (tokens.length > 5) {
    lines.push(`${6}. 其餘 ${tokens.length - 5} 筆欄位同理。`);
  }
  lines.push('', `整體而言，建議將 ${tokens[0]} 這類欄位改為雜湊後儲存。`);
  return lines.join('\n');
}

function run(): void {
  const text = input.value;

  if (!text.trim()) {
    outOrig.innerHTML = '<span style="color:var(--muted)">（還沒有輸入文字）</span>';
    outMasked.innerHTML = '<span style="color:var(--muted)">（還沒有輸入文字）</span>';
    outRestored.innerHTML = '<span style="color:var(--muted)">（還沒有還原）</span>';
    detailTable.hidden = true;
    detailEmpty.hidden = false;
    reply.value = '';
    mapping = new Map();
    $('s-count').textContent = '0';
    $('s-types').textContent = '0';
    $('s-time').textContent = '0';
    return;
  }

  // 真的量一次時間。數字會因機器而異，但那正是重點：這是你這台電腦的實測值。
  const started = performance.now();
  const spans = detectAll(text).spans;
  const elapsed = performance.now() - started;

  // 每次重跑都用全新的配號器：這個頁面一次只展示一段文字，
  // 沿用舊的會讓號碼從上一次的尾巴接下去，看起來莫名其妙。
  const result = maskText(text, spans, new PlaceholderAllocator());

  mapping = new Map(result.mapping.map((m) => [m.placeholder, m.original]));

  outOrig.innerHTML = highlight(text, spans);
  outMasked.innerHTML = highlightTokens(result.maskedText);

  $('s-count').textContent = String(result.mapping.length);
  $('s-types').textContent = String(new Set(spans.map((s) => s.type)).size);
  $('s-time').textContent = elapsed < 1 ? elapsed.toFixed(2) : elapsed.toFixed(1);

  // 明細表
  detailBody.innerHTML = '';
  const tokenOf = new Map(result.mapping.map((m) => [m.original + '\t' + m.type, m.placeholder]));
  for (const span of [...spans].sort((a, b) => a.start - b.start)) {
    const tr = document.createElement('tr');
    const risk = riskLevel(span.type);
    const token = tokenOf.get(span.text + '\t' + span.type) ?? '';
    tr.innerHTML =
      `<td><b>${escapeHtml(typeLabel(span.type))}</b><br>` +
      `<span style="color:var(--muted);font-size:12px" class="mono">${escapeHtml(span.type)}</span></td>` +
      `<td class="mono">${escapeHtml(span.text)}</td>` +
      `<td class="mono"><span class="tok">${escapeHtml(token)}</span></td>` +
      `<td><span class="pill ${risk}">${risk === 'high' ? '高' : risk === 'medium' ? '中' : '低'}</span></td>` +
      `<td class="mono">${span.start}–${span.end}</td>` +
      `<td>${span.source === 'rule' ? '規則層 · 檢核碼/格式' : '語意層 · 模型'}` +
      `<br><span style="color:var(--muted);font-size:12px">信心 ${span.confidence.toFixed(2)}</span></td>`;
    detailBody.appendChild(tr);
  }
  const has = spans.length > 0;
  detailTable.hidden = !has;
  detailEmpty.hidden = has;
  if (!has) {
    detailEmpty.textContent = '這段文字裡沒有偵測到個資。';
  }

  reply.value = buildFakeReply();
  outRestored.innerHTML =
    '<span style="color:var(--muted)">（按上面的「還原」按鈕）</span>';
}

/**
 * 還原：把佔位符換回真值。
 *
 * 查不到的佔位符**原樣保留、絕不猜測**（docs/B_design.md 決定 5）——
 * 雲端 AI 可能自己編出沒發過的佔位符，猜測等同於憑空捏造一筆個資
 * 塞進使用者的內容裡。這裡額外把它標成紅色，讓這個行為看得見。
 */
function restore(): void {
  const text = reply.value;
  if (!text.trim()) {
    outRestored.innerHTML = '<span style="color:var(--muted)">（上面沒有內容可以還原）</span>';
    return;
  }
  let restored = 0;
  let unknown = 0;
  const html = escapeHtml(text).replace(/\[[A-Z][A-Z_]*_\d+\]/g, (token) => {
    const value = mapping.get(token);
    if (value === undefined) {
      unknown += 1;
      return `<mark title="查不到對應真值，原樣保留">${token}</mark>`;
    }
    restored += 1;
    return `<mark class="r-low" title="由 ${token} 還原">${escapeHtml(value)}</mark>`;
  });
  const note =
    `<div style="color:var(--muted);font-size:12.5px;margin-bottom:10px;font-family:system-ui">` +
    `還原 ${restored} 筆` +
    (unknown > 0 ? ` ／ <b style="color:var(--high)">${unknown} 筆查不到，原樣保留</b>` : '') +
    `</div>`;
  outRestored.innerHTML = note + html;
}

// ── 事件綁定 ──
input.addEventListener('input', run);
$('btn-restore').addEventListener('click', restore);

document.querySelectorAll<HTMLButtonElement>('.chip[data-sample]').forEach((button) => {
  button.addEventListener('click', () => {
    const key = button.dataset.sample ?? '';
    input.value = key === 'clear' ? '' : (SAMPLES[key] ?? '');
    run();
    if (key !== 'clear') {
      input.scrollIntoView({ behavior: 'smooth', block: 'center' });
    }
  });
});

// 進站先載一個範例，讓第一眼就看得到東西在動。
input.value = SAMPLES.service;
run();
