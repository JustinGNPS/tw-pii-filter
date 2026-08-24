/**
 * 全形 → 半形正規化（偵測前置處理）。
 *
 * ## 為什麼需要
 *
 * 全形數字在中文輸入環境是**很自然會出現**的寫法——注音輸入法的全形模式、
 * 從 Word / PDF / 網頁表單複製貼上的內容都可能帶全形英數。但規則層的正則
 * 用的是 `[0-9]` / `[A-Za-z]`，對不到 U+FF10–FF19 / U+FF21–FF5A。
 *
 * 後果是使用者貼一份含全形統編的合約，面板顯示「未偵測到敏感資訊」，
 * 個資原封不動送出去——**使用者以為已遮蔽、實際沒有**，比不做更危險
 * （issue #21）。
 *
 * ## 為什麼是定點映射，不是 NFKC
 *
 * `NFKC` 看起來更省事，但**不保證長度不變**：
 *
 * ```
 * '０９１２３４５６７８'  10 → '0912345678'      10   等長 ✅
 * '㍿'                    1 → '株式会社'         4   長度改變 ⚠️
 * 'ﬁle'                   3 → 'file'            4   長度改變 ⚠️
 * ```
 *
 * 長度一變，後面所有 span 的 `start`/`end` 就對不回原文，而 B 的遮蔽是
 * 「從後往前依座標替換」，座標錯位會直接把文字切壞。
 *
 * 因此這裡只對**全形英數與全形空格**做定點映射。這個範圍是嚴格 1:1、
 * 都在 BMP 內、字元數保證不變，所以正規化後的座標可以直接沿用到原文，
 * 不需要維護對應表。
 *
 * ## 座標與 `span.text` 的關係
 *
 * 偵測跑在正規化後的文字上，但回傳的 `span.text` 一律取自**原文**，
 * 以維持 `docs/interface.md` 的約定：`text.slice(start, end) === span.text`。
 * 也就是說使用者在面板上看到的是自己打的全形原文，不是被我們改寫過的版本。
 */

/** 全形空格 U+3000 → 半形空格。 */
const IDEOGRAPHIC_SPACE = '　';

/**
 * 全形英數與符號（U+FF01–U+FF5E）對半形（U+0021–U+007E）是固定 offset 0xFEE0。
 * 這一段完整涵蓋全形的 `！` 到 `～`，含數字、大小寫英文與常見符號（`＠`、`．`、`－`）。
 */
const FULLWIDTH_START = 0xff01;
const FULLWIDTH_END = 0xff5e;
const FULLWIDTH_OFFSET = 0xfee0;

/**
 * 把全形英數/符號轉成半形，**保證字元數不變**。
 *
 * 不做其他 Unicode 正規化——見檔案頂端關於 NFKC 的說明。
 */
export function toHalfWidth(text: string): string {
  let result = '';
  for (const char of text) {
    const code = char.codePointAt(0)!;
    if (code >= FULLWIDTH_START && code <= FULLWIDTH_END) {
      result += String.fromCodePoint(code - FULLWIDTH_OFFSET);
    } else if (char === IDEOGRAPHIC_SPACE) {
      result += ' ';
    } else {
      result += char;
    }
  }
  return result;
}

/** 這段文字含有需要正規化的全形字元嗎（用來略過不必要的處理）。 */
export function hasFullWidth(text: string): boolean {
  for (const char of text) {
    const code = char.codePointAt(0)!;
    if ((code >= FULLWIDTH_START && code <= FULLWIDTH_END) || char === IDEOGRAPHIC_SPACE) {
      return true;
    }
  }
  return false;
}
