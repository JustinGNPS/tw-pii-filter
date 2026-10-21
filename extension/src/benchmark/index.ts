/**
 * 語意層在瀏覽器裡的延遲實測（對應風險清單 R1 / R2）。
 *
 * ## 這支程式要回答什麼
 *
 * 報告把「擴充跑語意層」列為 R1（MV3 的 CSP 能不能載入模型）與 R2
 * （延遲會不會超過 1 秒）。一直沒有實測數字，只有推論。
 *
 * 已知的參照點：
 *  - 規則層（TypeScript，本專案實測）：2 千字 0.11 ms
 *  - NER 模型（PyTorch CPU，修好 512 token 截斷後）：2800 字約 2412 ms
 *  - proxy 端因為這個延遲，把 `PII_ENABLE_NER` 預設關閉
 *
 * proxy 的運算餘裕遠大於瀏覽器（原生 PyTorch、可用 GPU、有跨請求快取），
 * 連它都不敢預設開。所以這支要量的其實是：**瀏覽器到底差多少**，
 * 以及差距是否大到讓「同步跑完再顯示面板」徹底出局。
 *
 * ## 為什麼先用現成的 ONNX 模型，不轉 D 的模型
 *
 * 轉檔要裝 torch + optimum（約 2.5 GB、半天），但如果答案是「瀏覽器要 8 秒」，
 * 那半天就白花了。先用架構相同的現成 ONNX 模型測天花板，有希望再投入轉檔。
 *
 * `Xenova/bert-base-multilingual-cased-ner-hrl` 與 D 用的
 * `gyr66/bert-base-chinese-finetuned-ner` 同為 **BERT-base（12 層、768 維）**，
 * 推論的計算量一致。
 *
 * ⚠️ 已知差異（解讀數字時要記得）：
 *  - 多語版的詞彙表大得多（約 12 萬 vs 中文版約 2 萬），**模型檔比較大**，
 *    所以「下載 / 載入」的數字偏悲觀，但「推論」的數字具代表性
 *  - 多語 tokenizer 對中文的切分與中文專用模型不同，token 數會有差異，
 *    因此本頁同時回報實際 token 數，讓數字可以被正確比較
 */

import {
  AutoTokenizer,
  env,
  pipeline,
  type TokenClassificationPipeline,
} from '@huggingface/transformers';

const MODEL_ID = 'Xenova/bert-base-multilingual-cased-ner-hrl';

/** 與 D 量測時相同的長度（約 2800 字），數字才可比。 */
const SAMPLE_BLOCK = [
  '甲方王小明，身分證字號 A123456789，聯絡電話 0912-345-678，',
  '通訊地址設於台北市大安區復興南路一段 100 號 5 樓，',
  '電子郵件 wang.ming@example.com.tw，任職於好房股份有限公司擔任資深後端工程師。',
  '乙方陳大文，統一編號 12345675，市內電話 02-23456789，',
  '現年 35 歲，男性，居住於新竹市東區光復路二段 200 號。',
  '本合約自簽訂日起生效，雙方應遵守個人資料保護法之相關規定，',
  '未經他方書面同意不得將本合約所載個人資料提供予第三人。',
].join('');

function buildSample(targetChars: number): string {
  let text = '';
  while (text.length < targetChars) text += SAMPLE_BLOCK;
  return text.slice(0, targetChars);
}

// ── DOM ──────────────────────────────────────────────────────────────

const $ = <T extends HTMLElement>(selector: string): T =>
  document.querySelector<T>(selector)!;

const logEl = $<HTMLPreElement>('#log');
const resultEl = $<HTMLDivElement>('#result');
const runBtn = $<HTMLButtonElement>('#run');
const backendSel = $<HTMLSelectElement>('#backend');
const lengthSel = $<HTMLSelectElement>('#length');
const dtypeSel = $<HTMLSelectElement>('#dtype');

function log(message: string): void {
  const stamp = new Date().toLocaleTimeString('zh-TW', { hour12: false });
  logEl.textContent += `[${stamp}] ${message}\n`;
  logEl.scrollTop = logEl.scrollHeight;
}

// ── transformers.js 環境設定 ─────────────────────────────────────────

/**
 * 關鍵設定：把 ORT 的 WASM 檔指到擴充自己的路徑。
 *
 * transformers.js 預設從 jsDelivr CDN 載入 ORT 的 wasm glue，
 * 但 **MV3 的 CSP 不允許執行遠端腳本**，那條路在擴充裡一定失敗。
 * 檔案由 build.mjs 複製進 dist/ort/。
 */
function configureEnv(): void {
  env.backends.onnx.wasm!.wasmPaths = chrome.runtime.getURL('ort/');
  // 不從擴充內部找模型（模型是執行期從 HF 下載的）
  env.allowLocalModels = false;
  // 用瀏覽器的 Cache Storage 存模型，第二次之後不必重新下載
  env.useBrowserCache = true;
}

// ── 量測 ─────────────────────────────────────────────────────────────

interface RunResult {
  backend: string;
  dtype: string;
  chars: number;
  tokens: number;
  loadMs: number;
  warmupMs: number;
  runs: number[];
  entities: number;
}

function stats(values: number[]) {
  const sorted = [...values].sort((a, b) => a - b);
  const sum = sorted.reduce((a, b) => a + b, 0);
  return {
    mean: sum / sorted.length,
    median: sorted[Math.floor(sorted.length / 2)],
    min: sorted[0],
    max: sorted[sorted.length - 1],
  };
}

async function run(): Promise<void> {
  runBtn.disabled = true;
  resultEl.innerHTML = '';
  logEl.textContent = '';

  const backend = backendSel.value as 'wasm' | 'webgpu';
  // 下拉選單的值對應 transformers.js 的量化型別（見 benchmark.html 的 option）
  const dtype = dtypeSel.value as 'q8' | 'fp32' | 'fp16';
  const chars = Number(lengthSel.value);
  const text = buildSample(chars);

  try {
    configureEnv();
    log(`後端=${backend} 量化=${dtype} 文字長度=${chars} 字`);
    log(`ORT wasm 路徑：${env.backends.onnx.wasm!.wasmPaths}`);

    if (backend === 'webgpu' && !('gpu' in navigator)) {
      log('⚠️ 這個環境沒有 navigator.gpu，WebGPU 不可用');
      throw new Error('WebGPU 不可用');
    }

    // ── 1. 載入模型（含首次下載）──
    log('載入模型中…（第一次會下載，數百 MB，請耐心等）');
    const loadStart = performance.now();
    const ner = (await pipeline('token-classification', MODEL_ID, {
      device: backend,
      dtype,
      progress_callback: (p: { status?: string; file?: string; progress?: number }) => {
        if (p.status === 'progress' && p.file && typeof p.progress === 'number') {
          if (Math.round(p.progress) % 25 === 0) {
            log(`  下載 ${p.file} ${Math.round(p.progress)}%`);
          }
        }
      },
    })) as TokenClassificationPipeline;
    const loadMs = performance.now() - loadStart;
    log(`模型就緒：${loadMs.toFixed(0)} ms`);

    // ── 2. 實際 token 數（跟 PyTorch 端的數字對齊時要看這個）──
    const tokenizer = await AutoTokenizer.from_pretrained(MODEL_ID);
    const tokens = (tokenizer(text) as { input_ids: { size: number } }).input_ids.size;
    log(`這段文字 = ${tokens} tokens（BERT 上限 512，超過會被截斷或需分段）`);
    if (tokens > 512) {
      log('⚠️ 超過 512，實際產品需要分段推論，成本會再乘上段數');
    }

    // ── 3. 暖機（第一次推論含 kernel 編譯，不計入平均）──
    log('暖機推論…');
    const warmStart = performance.now();
    const warmOut = (await ner(text)) as unknown[];
    const warmupMs = performance.now() - warmStart;
    log(`暖機：${warmupMs.toFixed(0)} ms，抓到 ${warmOut.length} 個實體`);

    // ── 4. 正式量測 ──
    const runs: number[] = [];
    for (let i = 1; i <= 5; i += 1) {
      const start = performance.now();
      await ner(text);
      const elapsed = performance.now() - start;
      runs.push(elapsed);
      log(`第 ${i} 次：${elapsed.toFixed(0)} ms`);
    }

    render({
      backend,
      dtype,
      chars,
      tokens,
      loadMs,
      warmupMs,
      runs,
      entities: warmOut.length,
    });
  } catch (error) {
    log(`❌ 失敗：${error instanceof Error ? error.message : String(error)}`);
    resultEl.innerHTML = `<div class="fail">
      <b>這次執行失敗</b><br>
      失敗本身就是 R1 的答案之一——把上面的 log 完整貼進報告，
      它會指出是 CSP、WASM 載入、WebGPU 不支援，還是其他原因。
    </div>`;
    console.error(error);
  } finally {
    runBtn.disabled = false;
  }
}

function render(r: RunResult): void {
  const s = stats(r.runs);
  // 規則層在同樣長度下的實測值，用來對比「延遲預算被誰吃掉」
  const RULE_LAYER_MS = 0.11;
  const ratio = Math.round(s.median / RULE_LAYER_MS);

  const verdict =
    s.median > 5000
      ? { cls: 'bad', text: '同步跑完整 NER 不可行，應走報告既定退路（規則層 + 警告）' }
      : s.median > 1000
        ? { cls: 'warn', text: '超過 R2 的 1 秒門檻，同步不可行，但「分段顯示」可行' }
        : { cls: 'good', text: '落在 1 秒門檻內，同步跑語意層可行' };

  resultEl.innerHTML = `
    <table>
      <tr><th colspan="2">結果</th></tr>
      <tr><td>後端</td><td>${r.backend} / ${r.dtype}</td></tr>
      <tr><td>文字長度</td><td>${r.chars} 字 = <b>${r.tokens}</b> tokens</td></tr>
      <tr><td>模型載入（含下載）</td><td>${r.loadMs.toFixed(0)} ms</td></tr>
      <tr><td>暖機推論</td><td>${r.warmupMs.toFixed(0)} ms</td></tr>
      <tr class="hl"><td>推論中位數</td><td><b>${s.median.toFixed(0)} ms</b></td></tr>
      <tr><td>平均 / 最快 / 最慢</td><td>${s.mean.toFixed(0)} / ${s.min.toFixed(0)} / ${s.max.toFixed(0)} ms</td></tr>
      <tr><td>抓到實體數</td><td>${r.entities}</td></tr>
      <tr><td>對比規則層</td><td>規則層 ${RULE_LAYER_MS} ms → <b>慢約 ${ratio.toLocaleString()} 倍</b></td></tr>
    </table>
    <div class="verdict ${verdict.cls}">${verdict.text}</div>
    <p class="note">
      可直接貼進報告的一行：
      <code>瀏覽器（${r.backend}/${r.dtype}）BERT-base 對 ${r.chars} 字（${r.tokens} tokens）
      推論中位數 ${s.median.toFixed(0)} ms，模型載入 ${(r.loadMs / 1000).toFixed(1)} s。</code>
    </p>
  `;
}

runBtn.addEventListener('click', () => void run());

log('就緒。按「開始實測」。第一次會下載模型（數百 MB），之後有瀏覽器快取。');
log(`WebGPU 支援：${'gpu' in navigator ? '有 navigator.gpu' : '沒有（只能測 wasm）'}`);
