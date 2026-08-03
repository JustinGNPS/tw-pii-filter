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

  // manifest.json、popup.html 等靜態檔直接複製
  await cp(path.join(ROOT, 'public'), OUT_DIR, { recursive: true });
  console.log(`建置完成 → ${path.relative(process.cwd(), OUT_DIR)}`);
}

build().catch((error) => {
  console.error(error);
  process.exit(1);
});
