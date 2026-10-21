/**
 * 擴充建置腳本（esbuild）。
 *
 * 刻意不用 Vite / @crxjs 之類的框架外掛：MV3 的入口點是固定的幾支
 * （service worker、content script、popup），用 esbuild 直接打包最單純，
 * 也避開外掛版本變動造成的不可重現問題（CONTRIBUTING.md：套件版本鎖死）。
 *
 * 用法：
 *   npm run build     一次性建置
 *   npm run dev       watch 模式，改檔即重建
 *
 * 產出在 dist/，到 chrome://extensions 開啟開發人員模式後
 * 用「載入未封裝項目」指向 dist/ 即可。
 */

import { cp, mkdir, rm } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

import * as esbuild from 'esbuild';

const ROOT = path.dirname(fileURLToPath(import.meta.url));
const OUT_DIR = path.join(ROOT, 'dist');
const WATCH = process.argv.includes('--watch');

/**
 * content script 與 service worker 都打包成 IIFE 單檔：
 * MV3 的 content script 不支援 ESM import，service worker 雖然可以用
 * `"type": "module"`，但打成單檔可以少一層不確定性。
 */
const ENTRIES = {
  content: path.join(ROOT, 'src/content/index.ts'),
  background: path.join(ROOT, 'src/background/index.ts'),
  popup: path.join(ROOT, 'src/popup/index.ts'),
};

/**
 * 語意層延遲實測頁（R1 / R2）。
 *
 * 與上面三支分開建置，因為它必須是 **ESM**：transformers.js 內部用動態
 * `import()` 載入 onnxruntime 的 WASM glue，打成 IIFE 會在執行期壞掉。
 * 這支只在實測時載入，不影響正式的擴充路徑。
 */
const BENCHMARK_ENTRY = path.join(ROOT, 'src/benchmark/index.ts');

/**
 * onnxruntime-web 的 WASM 檔案。
 *
 * **MV3 的 CSP 不允許載入遠端腳本**，而 transformers.js 預設會去 CDN 抓
 * ORT 的 wasm glue——那在擴充裡一定失敗。必須把檔案打包進擴充，
 * 再用 `env.backends.onnx.wasm.wasmPaths` 指到擴充自己的路徑。
 *
 * 這件事本身就是風險清單 R1 要驗證的東西之一。
 */
const ORT_DIST = path.join(ROOT, 'node_modules/onnxruntime-web/dist');
const ORT_FILES = [
  'ort-wasm-simd-threaded.wasm', // CPU（WASM）後端
  'ort-wasm-simd-threaded.mjs',
  'ort-wasm-simd-threaded.jsep.wasm', // WebGPU（JSEP）後端
  'ort-wasm-simd-threaded.jsep.mjs',
];

async function build() {
  await rm(OUT_DIR, { recursive: true, force: true });
  await mkdir(OUT_DIR, { recursive: true });

  const context = await esbuild.context({
    entryPoints: ENTRIES,
    outdir: OUT_DIR,
    bundle: true,
    format: 'iife',
    target: ['chrome114'],
    platform: 'browser',
    charset: 'utf8',
    logLevel: 'info',
    sourcemap: WATCH ? 'inline' : false,
    minify: !WATCH,
  });

  if (WATCH) {
    await context.watch();
    console.log('watch 模式啟動，修改 src/ 會自動重建 → dist/');
  } else {
    await context.rebuild();
    await context.dispose();
  }

  // 實測頁單獨建置成 ESM（理由見 BENCHMARK_ENTRY 的說明）
  await esbuild.build({
    entryPoints: [BENCHMARK_ENTRY],
    outfile: path.join(OUT_DIR, 'benchmark.js'),
    bundle: true,
    format: 'esm',
    target: ['chrome114'],
    platform: 'browser',
    charset: 'utf8',
    logLevel: 'info',
    sourcemap: WATCH ? 'inline' : false,
    minify: !WATCH,
    // transformers.js 在 node 下會去載 onnxruntime-node，明確挑瀏覽器進入點
    conditions: ['browser', 'import'],
  });

  // ORT 的 WASM 檔必須在擴充本地（MV3 CSP 不允許遠端腳本）
  await mkdir(path.join(OUT_DIR, 'ort'), { recursive: true });
  for (const file of ORT_FILES) {
    await cp(path.join(ORT_DIST, file), path.join(OUT_DIR, 'ort', file));
  }

  // manifest.json、popup.html 等靜態檔直接複製
  await cp(path.join(ROOT, 'public'), OUT_DIR, { recursive: true });
  console.log(`建置完成 → ${path.relative(process.cwd(), OUT_DIR)}`);
}

build().catch((error) => {
  console.error(error);
  process.exit(1);
});
