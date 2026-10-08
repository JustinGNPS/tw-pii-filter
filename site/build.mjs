/**
 * 靜態展示網站建置腳本。
 *
 * 把 src/app.ts（連同它 import 的 extension/src/ 偵測核心）用 esbuild 打包成
 * 一段 JS，直接**內嵌**進 template.html 的 <script> 裡，產出單一 dist/index.html。
 *
 * ## 為什麼要內嵌成單一檔案
 *
 * 這頁的用途是「把連結丟給別人（評審、老師、同學）點開就能用」。單一 HTML 檔
 * 可以放任何靜態主機、可以用 email 寄、可以離線雙擊開啟，不會有相對路徑或
 * MIME type 設錯導致白畫面的問題 —— 展示場合最不需要的就是這種意外。
 *
 * ## 為什麼 esbuild 版本跟 extension/ 鎖同一個
 *
 * 兩邊打包的是同一份原始碼。版本不同 → 產物可能有差異 → 「網頁上看到的
 * 跟擴充實際行為一致」這句話就不成立了。CONTRIBUTING.md 第 6 條：套件版本鎖死。
 *
 * 用法：
 *   npm install   （第一次）
 *   npm run build  → dist/index.html
 */

import { mkdir, readFile, writeFile } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

import * as esbuild from 'esbuild';

const ROOT = path.dirname(fileURLToPath(import.meta.url));
const OUT_DIR = path.join(ROOT, 'dist');
const PLACEHOLDER = '/*APP_JS*/';

async function build() {
  const bundled = await esbuild.build({
    entryPoints: [path.join(ROOT, 'src/app.ts')],
    bundle: true,
    format: 'iife',
    target: ['es2020'],
    platform: 'browser',
    charset: 'utf8',
    minify: true,
    write: false,
    logLevel: 'info',
  });

  const js = bundled.outputFiles[0].text;

  const template = await readFile(path.join(ROOT, 'template.html'), 'utf-8');
  if (!template.includes(PLACEHOLDER)) {
    throw new Error(`template.html 裡找不到 ${PLACEHOLDER} 佔位符，無法注入打包後的 JS`);
  }

  // 用 replace 的 function 形式：JS 內容裡可能出現 $& 這類 replacement pattern，
  // 直接傳字串會被當成特殊語法而悄悄改掉程式碼。
  const html = template.replace(PLACEHOLDER, () => js);

  await mkdir(OUT_DIR, { recursive: true });
  await writeFile(path.join(OUT_DIR, 'index.html'), html, 'utf-8');

  const kb = (Buffer.byteLength(html, 'utf-8') / 1024).toFixed(1);
  console.log(`建置完成 → ${path.relative(process.cwd(), path.join(OUT_DIR, 'index.html'))}（${kb} KB，單一檔案）`);
}

build().catch((error) => {
  console.error(error);
  process.exit(1);
});
