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
 * ## 語意層（L2）不在這裡，組合風險（L3）在
 *
 * 語意層需要下載 NER 模型權重（數百 MB），不適合放進網頁，頁面上以標明出處的
 * 實錄呈現。組合風險用的是擴充的 TypeScript 移植（`detectAll()` 回傳的
 * `combination_risk`），但少了語意層，能計入的準識別子只有年齡與性別——
 * 頁面上會說明這一點，不假裝這裡展示的是完整系統。
 */

import {
  detectAll,
  type CombinationRisk,
  isValidTwId,
  isValidTwPhoneM,
  isValidTwTax,
  type Span,
} from '../../extension/src/core';
import { showPanel } from '../../extension/src/content/panel';
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
  service: `客服通話紀錄 #1008-042
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

  // 一個明碼個資都沒有，用來展示組合風險（L3）。
  quasi: `這位患者 32 歲男性，住在新竹市東區，是某科技公司的資深後端工程師，
上週因罕見疾病在本院門診追蹤。

請幫我把這段病歷摘要改寫成給家屬看的說明。`,
};

const $ = <T extends HTMLElement>(id: string): T =>
  document.getElementById(id) as T;

const input = $<HTMLTextAreaElement>('input');
const reply = $<HTMLTextAreaElement>('reply');
const outMasked = $<HTMLDivElement>('out-masked');
const outRestored = $<HTMLDivElement>('out-restored');
const restoreNote = $<HTMLSpanElement>('restore-note');
const detail = $<HTMLDetailsElement>('detail');
const detailBody = $<HTMLTableSectionElement>('detail-body');
const riskBox = $<HTMLDivElement>('risk');

/** 目前這段輸入的對照表（佔位符 → 真值）。只存在記憶體，重新輸入就重建。 */
let mapping = new Map<string, string>();

function escapeHtml(s: string): string {
  return s
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;');
}

const muted = (text: string): string => `<span class="placeholder">${text}</span>`;

/**
 * 把遮蔽後文字裡的佔位符標成藍色，讓「雲端看到什麼」一眼可辨。
 * 滑鼠移上去看得到它原本是什麼（只查本頁記憶體裡的對照表）。
 */
function highlightTokens(masked: string, table?: Map<string, string>): string {
  return escapeHtml(masked).replace(/\[[A-Z][A-Z_]*_\d+\]/g, (token) => {
    const original = table?.get(token);
    const title = original === undefined ? '' : ` title="${escapeHtml(original).replace(/"/g, '&quot;')}"`;
    return `<span class="tok"${title}>${token}</span>`;
  });
}

// ────────────────────────────────────────────────────────────
// 組合風險（L3）
//
// 這一頁沒有語意層，所以 spans 裡不會有 ADDRESS／COMPANY／POSITION，
// 能計入的準識別子只剩文字本身掃得到的 AGE 與 GENDER。分數因此會比
// 完整系統低——照實顯示並說明原因，不另外補一個比較好看的數字。
// ────────────────────────────────────────────────────────────
const QUASI_LABEL: Record<string, string> = {
  AGE: '年齡',
  GENDER: '性別',
  ADDRESS: '地址',
  POSITION: '職稱',
  COMPANY: '公司',
  ORGANIZATION: '機構',
  GOVERNMENT: '政府機關',
  SCENE: '地點',
};

function renderRisk(risk: CombinationRisk | null): void {
  if (!risk || risk.score <= 0) {
    riskBox.hidden = true;
    return;
  }
  const cls = risk.risk_level === '高' ? 'high' : risk.risk_level === '中' ? 'medium' : 'low';
  const types = risk.contributing_types.map((t) => QUASI_LABEL[t] ?? t).join('、');
  riskBox.className = `callout ${cls}`;
  riskBox.innerHTML =
    `<h5><span class="pill ${cls}">組合風險 ${risk.risk_level} · ${risk.score.toFixed(2)}</span>` +
    `${escapeHtml(types)}同時出現，合起來仍可能指認到特定個人</h5>` +
    `<ul>${risk.suggestions.map((s) => `<li>${escapeHtml(s)}</li>`).join('')}</ul>` +
    '<p class="fine">這裡只提示、不強制遮蔽，由你決定要不要改寫。' +
    '這一頁沒有語意層，只計入年齡與性別；完整系統還會計入地址、公司與職稱。</p>';
  riskBox.hidden = false;
}

// ────────────────────────────────────────────────────────────
// 「為什麼沒抓到」
//
// 實測使用者會隨手打一組假號碼（例如 A121313），看到「0 筆」就以為壞了。
// 系統其實是對的——那個號碼只有 7 碼，根本不是身分證格式。但**沉默的正確
// 比錯誤更傷**：看的人不會知道是自己的號碼不合法。
//
// 所以把「被擋下來的候選」也列出來並說明理由。這反而是最有說服力的一段：
// 其他工具不驗檢核碼，任何「1 英文 + 9 數字」都會被當成身分證；我們會驗，
// 所以能排除這類誤報——代價就是你得用真的通得過檢核碼的號碼才看得到效果。
// ────────────────────────────────────────────────────────────
interface Reject {
  cand: string;
  why: string;
  kind: 'reject' | 'info';
}

/**
 * 刻意不用 lookbehind（`(?<!...)`）。那個語法在舊版 Safari 會在**解析階段**
 * 就丟 SyntaxError，整包 bundle 直接不執行——為了一個說明用的小功能
 * 讓整頁在某些瀏覽器白畫面，完全不划算。改成把前置字元納入比對再取 group。
 */
function explainMisses(text: string, spans: Span[]): Reject[] {
  const out: Reject[] = [];
  const seen = new Set<string>();

  /**
   * 候選是否落在某個「已經偵測到」的 span 裡面。
   *
   * 必須比對**座標**而不是字串內容：市話 `02-27208889` 的後 8 碼單獨看
   * 正好是統編的長度，若只比字串相等，就會把一個已經被遮蔽的號碼
   * 再列成「被擋下來的統編候選」——那是錯的，而且剛好出現在自己的範例裡。
   */
  const insideDetected = (start: number, end: number): boolean =>
    spans.some((span) => start < span.end && end > span.start);

  const push = (
    cand: string,
    why: string,
    kind: Reject['kind'] = 'reject',
  ): void => {
    if (seen.has(kind + cand)) return;
    seen.add(kind + cand);
    out.push({ cand, why, kind });
  };

  const scan = (re: RegExp, handle: (value: string) => void): void => {
    for (const match of text.matchAll(re)) {
      const value = match[2];
      if (!value || match.index === undefined) continue;
      const start = match.index + match[1].length;
      if (insideDetected(start, start + value.length)) continue;
      handle(value);
    }
  };

  // 長度對、但檢核碼不過的身分證
  scan(/(^|[^A-Za-z0-9])([A-Za-z][0-9]{9})(?![0-9])/g, (value) => {
    if (!isValidTwId(value)) {
      push(value, '格式像身分證（1 英文字母 + 9 位數字），但<b>檢核碼驗證不通過</b>');
    }
  });

  // 英文字母開頭、數字位數不足
  scan(/(^|[^A-Za-z0-9])([A-Za-z][0-9]{3,8})(?![0-9])/g, (value) => {
    push(
      value,
      `長度不符：身分證是 1 個英文字母 + <b>9</b> 位數字，這個只有 ${value.length - 1} 位`,
    );
  });

  // 8 碼數字、但統編檢核碼不過
  scan(/(^|[^0-9])([0-9]{8})(?![0-9])/g, (value) => {
    if (!isValidTwTax(value)) {
      push(value, '符合統一編號的 8 碼長度，但<b>檢核碼驗證不通過</b>');
    }
  });

  // 09 開頭、但長度不符
  scan(/(^|[^0-9])(09[0-9]{4,12})(?![0-9])/g, (value) => {
    if (!isValidTwPhoneM(value)) {
      push(
        value,
        `09 開頭但長度不符：手機是 09 加 8 位數字（共 <b>10</b> 碼），這個有 ${value.length} 碼`,
      );
    }
  });

  // 有中文、但這頁沒有語意層
  if (/[一-鿿]/.test(text) && !spans.some((s) => s.source === 'model')) {
    push(
      '人名 / 地址 / 公司名',
      '要靠<b>語意層（L2）的 NER 模型</b>才認得出來，這個網頁只跑規則層。' +
        '完整版有做，見下方「怎麼運作」的「語意層」分頁',
      'info',
    );
  }

  return out;
}

function renderRejects(items: Reject[], detectedCount: number): void {
  const box = document.getElementById('rejects');
  if (!box) return;
  if (items.length === 0) {
    box.hidden = true;
    return;
  }
  const rejected = items.some((item) => item.kind === 'reject');
  const title =
    detectedCount === 0
      ? '這段文字沒有偵測到個資，原因如下'
      : rejected
        ? '另外有幾個看起來像、但被擋下來的字串'
        : '這一頁抓不到的部分';

  box.innerHTML =
    `<h5>${title}</h5><ul>` +
    items
      .map(
        (item) =>
          `<li class="${item.kind === 'info' ? 'info' : ''}">` +
          `<span class="cand">${escapeHtml(item.cand)}</span> ` +
          `<span class="why">${item.why}</span></li>`,
      )
      .join('') +
    '</ul>' +
    // 只有真的擋下候選時才講這一段；單純的語意層提示不需要它。
    (rejected
      ? '<p class="fine">其他工具不驗檢核碼，任何「1 英文字母 + 9 位數字」都會被當成身分證。' +
        '我們會驗，所以能排除格式像但不合法的號碼；想看到效果得用通得過檢核碼的公開測試值，上面的範例都是。</p>'
      : '');
  box.hidden = false;
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
    outMasked.innerHTML = muted('還沒有輸入文字');
    detail.hidden = true;
    renderRisk(null);
    renderRejects([], 0);
    reply.value = '';
    mapping = new Map();
    $('s-count').textContent = '0';
    $('s-types').textContent = '0';
    $('s-time').textContent = '0';
    restore();
    return;
  }

  // 真的量一次時間。數字會因機器而異，但那正是重點：這是你這台電腦的實測值。
  const started = performance.now();
  const detection = detectAll(text);
  const elapsed = performance.now() - started;
  const spans = detection.spans;

  // 每次重跑都用全新的配號器：這個頁面一次只展示一段文字，
  // 沿用舊的會讓號碼從上一次的尾巴接下去，看起來莫名其妙。
  const result = maskText(text, spans, new PlaceholderAllocator());

  mapping = new Map(result.mapping.map((m) => [m.placeholder, m.original]));

  outMasked.innerHTML = highlightTokens(result.maskedText, mapping);
  renderRisk(detection.combination_risk ?? null);
  renderRejects(explainMisses(text, spans), result.mapping.length);

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
      `<td><b>${escapeHtml(typeLabel(span.type))}</b>` +
      `<small class="mono">${escapeHtml(span.type)}</small></td>` +
      `<td class="mono">${escapeHtml(span.text)}</td>` +
      `<td class="mono"><span class="tok">${escapeHtml(token)}</span></td>` +
      `<td><span class="pill ${risk}">${risk === 'high' ? '高' : risk === 'medium' ? '中' : '低'}</span></td>` +
      `<td class="mono">${span.start}–${span.end}</td>` +
      `<td>${span.source === 'rule' ? '規則層 · 檢核碼/格式' : '語意層 · 模型'}` +
      `<small>信心 ${span.confidence.toFixed(2)}</small></td>`;
    detailBody.appendChild(tr);
  }
  detail.hidden = spans.length === 0;
  $('detail-count').textContent = String(spans.length);

  reply.value = buildFakeReply();
  restore();
}

/**
 * 還原：把佔位符換回真值。回覆內容一變就重算，不需要另外按按鈕。
 *
 * 查不到的佔位符**原樣保留、絕不猜測**（docs/B_design.md 決定 5）——
 * 雲端 AI 可能自己編出沒發過的佔位符，猜測等同於憑空捏造一筆個資
 * 塞進使用者的內容裡。這裡額外把它標成紅色，讓這個行為看得見。
 */
function restore(): void {
  const text = reply.value;
  if (!text.trim()) {
    outRestored.innerHTML = muted(
      mapping.size === 0 ? '沒有佔位符需要還原' : '左邊沒有內容可以還原',
    );
    restoreNote.textContent = '';
    return;
  }
  let restored = 0;
  let unknown = 0;
  outRestored.innerHTML = escapeHtml(text).replace(/\[[A-Z][A-Z_]*_\d+\]/g, (token) => {
    const value = mapping.get(token);
    if (value === undefined) {
      unknown += 1;
      return `<mark title="查不到對應真值，原樣保留">${token}</mark>`;
    }
    restored += 1;
    return `<mark class="r-low" title="由 ${token} 還原">${escapeHtml(value)}</mark>`;
  });
  restoreNote.innerHTML =
    `還原 <b>${restored}</b> 筆` +
    (unknown > 0 ? ` · <span class="no">${unknown} 筆查不到，原樣保留</span>` : '');
}

// ── 事件綁定 ──
input.addEventListener('input', run);
reply.addEventListener('input', restore);

document.querySelectorAll<HTMLButtonElement>('.chip[data-sample]').forEach((button) => {
  button.addEventListener('click', () => {
    const key = button.dataset.sample ?? '';
    input.value = key === 'clear' ? '' : (SAMPLES[key] ?? '');
    run();
  });
});

// 進站先載一個範例，讓第一眼就看得到東西在動。
input.value = SAMPLES.service;
run();

// ════════════════════════════════════════════════════════════
// 載體一（瀏覽器擴充）的流程模擬
//
// 這一段**不是預錄、也不是仿製品**：`showPanel()` 直接 import 擴充的
// `content/panel.ts`（它零 chrome 依賴，只用 DOM 與 Shadow DOM），
// 流程照 `content/index.ts` 走一遍：
//
//   偵測 -> 預覽配號 -> 確認面板 -> 依勾選結果正式配號 -> 插入
//
// 「預覽配號器」那一步是擴充自己的設計，不能省：使用者可能取消勾選某些
// 項目，直接在正式配號器上配會留下用不到的號碼缺口。照抄才算忠實。
//
// 唯一的差別是不碰 chrome.storage（網頁沒有），所以配號狀態從空的開始，
// 等同「這個對話的第一次貼上」。
// ════════════════════════════════════════════════════════════

const chatInput = document.getElementById('chat-input') as HTMLTextAreaElement | null;
const btnExt = document.getElementById('btn-ext') as HTMLButtonElement | null;
const extResult = document.getElementById('ext-result');
const chatHint = document.getElementById('chat-hint');

const EXT_SAMPLE = `幫我看一下這段客訴怎麼回比較好：
客戶王大明（身分證 A123456789）說他的信用卡 4111111111111111 被重複扣款，
聯絡電話 0912345678，公司統編 12345675。`;

if (chatInput) chatInput.value = EXT_SAMPLE;

function showExtResult(
  kind: 'good' | 'raw' | 'cancel',
  title: string,
  sent: string | null,
  verdict: string,
): void {
  if (!extResult) return;
  extResult.className = kind;
  extResult.innerHTML =
    `<h5>${title}</h5>` +
    (sent === null ? '' : `<div class="sent">${highlightTokens(sent)}</div>`) +
    `<p class="verdict">${verdict}</p>`;
  extResult.hidden = false;
}

async function runExtensionFlow(): Promise<void> {
  if (!chatInput || !btnExt) return;
  const text = chatInput.value;
  if (!text.trim()) return;

  const { spans, combination_risk } = detectAll(text);

  const risk = combination_risk ?? null;

  // 沒偵測到東西、也沒有組合風險，就完全不打擾使用者 —— 這是擴充能被長期留著的前提。
  // 條件刻意包含組合風險，與 content/index.ts 一致：只有準識別子的文字一個 span
  // 都抓不到，若只看 spans 就放行，等於告訴使用者「沒問題」。
  if (spans.length === 0 && !risk) {
    showExtResult(
      'cancel',
      '面板沒有跳出來',
      null,
      '這段文字沒有偵測到任何個資，也沒有組合風險，擴充直接把原文貼上、不打擾你。' +
        '<b>不該跳的時候不跳</b>，跟該跳的時候要跳一樣重要。',
    );
    return;
  }

  // 面板上顯示的佔位符用「預覽」配號器算（擴充的作法，見 content/index.ts）
  const preview = new PlaceholderAllocator();
  const previewPlaceholders = spans.map((span) => preview.allocate(span.type, span.text));

  btnExt.disabled = true;
  let decision;
  try {
    decision = await showPanel(spans, previewPlaceholders, risk);
  } finally {
    btnExt.disabled = false;
  }

  if (decision.action === 'cancel') {
    showExtResult(
      'cancel',
      '你選了「不貼上」—— 什麼都沒有送出去',
      null,
      '輸入框維持原狀，原文也沒有離開你的電腦。',
    );
    return;
  }

  if (decision.action === 'raw') {
    chatInput.value = text;
    showExtResult(
      'raw',
      '你選了「直接貼上原文」—— 個資會原封不動送進雲端',
      text,
      '擴充不會攔住你 —— <b>使用者有最終決定權</b>，這是刻意的。' +
        '但上面這些內容會原文進入雲端模型的上下文。',
    );
    return;
  }

  const allocator = new PlaceholderAllocator();
  const { maskedText, mapping } = maskText(text, decision.spans, allocator);
  chatInput.value = maskedText;
  if (chatHint) chatHint.textContent = '輸入框裡的內容已被替換';

  const skipped = spans.length - decision.spans.length;
  showExtResult(
    'good',
    mapping.length > 0
      ? `已遮蔽 ${mapping.length} 筆後貼進輸入框`
      : '沒有需要遮蔽的項目，原文貼進輸入框',
    maskedText,
    (mapping.length > 0
      ? `這就是雲端 AI 會看到的內容。對照表（${mapping.length} 筆）只存在你的瀏覽器裡，絕不外傳。`
      : '面板只提示了組合風險；要不要先改寫再送出，由你決定。') +
      (skipped > 0
        ? ` <b>你取消勾選了 ${skipped} 項</b>，那些維持原文送出。`
        : ''),
  );
}

btnExt?.addEventListener('click', () => void runExtensionFlow());

// ════════════════════════════════════════════════════════════
// 「怎麼運作」的三個分頁：擴充（可操作）、Proxy 與語意層（實錄）
// ════════════════════════════════════════════════════════════

const tabs = Array.from(document.querySelectorAll<HTMLButtonElement>('.tab[data-tab]'));

tabs.forEach((tab) => {
  tab.addEventListener('click', () => {
    const name = tab.dataset.tab ?? '';
    tabs.forEach((other) => {
      const selected = other === tab;
      other.setAttribute('aria-selected', String(selected));
      const panel = document.getElementById(`tab-${other.dataset.tab}`);
      if (panel) panel.hidden = !selected;
    });
    // 切到 Proxy 分頁時，若還沒播過就自動播一次——這一段是靜止畫面最沒說服力、
    // 動起來最有說服力的一塊，不該要求使用者再多按一次。
    if (name === 'proxy' && !hasPlayed) void playTerminal();
  });
});

// ────────────────────────────────────────────────────────────
// Proxy 攔截過程的終端機重現
//
// 這些行**不是手寫的假畫面**：出自 2026-08-22 以真實 Codex 執行
// 「修改一個含個資的檔案」任務時，proxy 留下的 proxy_writetest.log。
// 時間戳與毫秒數都是原值，只把上游端點網址隱去（那是內部設施，
// 沒有理由出現在公開網頁上）。
//
// delay 是「這一行印出來之後停多久」，用來重現當時的節奏感；
// 原始 log 的時間戳跨度約 20 秒，這裡壓縮成約 20 秒的播放長度。
// ────────────────────────────────────────────────────────────
interface TermLine {
  text: string;
  cls?: 'cmd' | 'warnline' | 'infoline' | 'note' | 'hit';
  delay?: number;
}

const TERM_SCRIPT: TermLine[] = [
  { text: '$ set PII_ENABLE_NER=0', cls: 'cmd', delay: 260 },
  { text: '$ .venv\\Scripts\\python.exe -m uvicorn proxy.main:app --port 8010', cls: 'cmd', delay: 650 },
  { text: '' },
  { text: '12:40:58 [INFO] proxy 啟動', cls: 'infoline', delay: 90 },
  { text: '上游 base URL：（由 .env 指定）', delay: 60 },
  { text: '上游金鑰：已載入（來自 UPSTREAM_API_KEY）', delay: 60 },
  { text: '預設模型：gpt-4.1-mini', delay: 60 },
  { text: '偵測到但不遮蔽的型別：COMPANY、POSITION', delay: 60 },
  { text: '語意層（NER）：未啟用（僅規則層）', delay: 60 },
  { text: '組合風險提示：已啟用', delay: 60 },
  { text: '對照表閒置逾時：1800 秒', delay: 420 },
  {
    text: '12:40:58 [INFO] 尚未收到任何請求。agent 開始工作後這裡會印一行',
    cls: 'infoline',
    delay: 60,
  },
  {
    text: '                「第一個請求已抵達 proxy」；若始終沒出現，代表流量沒有走這裡。',
    cls: 'infoline',
    delay: 900,
  },
  { text: '' },
  { text: '# ── 另一個終端機：把 Codex 指向 proxy，叫它改一個含個資的檔案 ──', cls: 'note', delay: 1100 },
  { text: '' },
  {
    text: '12:41:27 [INFO] 第一個請求已抵達 proxy（POST /responses）—— 流量確實有走這裡',
    cls: 'hit',
    delay: 700,
  },
  {
    text: '12:41:27 [INFO] POST /responses -> 200 [SSE] 上游 3135 ms｜遮蔽 5.8 ms｜還原 0 筆',
    cls: 'infoline',
    delay: 850,
  },
  {
    text: '12:41:32 [WARNING] 已遮蔽：偵測到 6 筆敏感資訊（TW_ID x3、TW_PHONE_M x3）｜快取命中率 57%',
    cls: 'warnline',
    delay: 950,
  },
  {
    text: '12:41:35 [INFO] POST /responses -> 200 [SSE] 上游 3424 ms｜遮蔽 3.8 ms｜還原 9 筆',
    cls: 'infoline',
    delay: 500,
  },
  {
    text: '12:41:35 [WARNING] 已遮蔽：偵測到 8 筆敏感資訊（TW_ID x5、TW_PHONE_M x3）｜快取命中率 64%',
    cls: 'warnline',
    delay: 950,
  },
  {
    text: '12:41:42 [WARNING] 已遮蔽：偵測到 14 筆敏感資訊（TW_ID x8、TW_PHONE_M x6）｜快取命中率 76%',
    cls: 'warnline',
    delay: 950,
  },
  {
    text: '12:41:48 [INFO] POST /responses -> 200 [SSE] 上游 3262 ms｜遮蔽 5.5 ms｜還原 4 筆',
    cls: 'infoline',
    delay: 500,
  },
  {
    text: '12:41:49 [WARNING] 已遮蔽：偵測到 15 筆敏感資訊（TW_ID x9、TW_PHONE_M x6）｜快取命中率 81%',
    cls: 'warnline',
    delay: 1100,
  },
  { text: '' },
  { text: '# ── Codex 完成任務，檔案被正確改好 ──', cls: 'note', delay: 500 },
  {
    text: '# 磁碟上的檔案是「真值」，不是 [TW_ID_1] —— 回程還原有做到。',
    cls: 'note',
    delay: 500,
  },
  {
    text: '# 整段過程中，雲端 LLM 看到的每一個身分證與電話都是佔位符。',
    cls: 'note',
  },
];

const term = document.getElementById('term');
const btnPlay = document.getElementById('btn-play') as HTMLButtonElement | null;
const playHint = document.getElementById('play-hint');
let hasPlayed = false;
let playing = false;

const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));

async function playTerminal(): Promise<void> {
  if (!term || playing) return;
  playing = true;
  hasPlayed = true;
  if (btnPlay) {
    btnPlay.disabled = true;
    btnPlay.textContent = '播放中⋯⋯';
  }
  if (playHint) playHint.textContent = '';
  term.innerHTML = '';

  for (const line of TERM_SCRIPT) {
    const span = document.createElement('span');
    if (line.cls) span.className = line.cls;
    span.textContent = line.text + '\n';
    term.appendChild(span);
    term.scrollTop = term.scrollHeight;
    await sleep(line.delay ?? 55);
  }

  const cursor = document.createElement('span');
  cursor.className = 'cursor';
  term.appendChild(cursor);

  playing = false;
  if (btnPlay) {
    btnPlay.disabled = false;
    btnPlay.textContent = '重播';
  }
  if (playHint) playHint.textContent = '這是 2026-08-22 的真實執行紀錄';
}

btnPlay?.addEventListener('click', () => void playTerminal());

// 還沒播放前先放一段提示，不要是一塊空白的黑色方塊。
if (term) {
  term.innerHTML =
    '<span class="note">（按上面的按鈕播放）</span>';
}
