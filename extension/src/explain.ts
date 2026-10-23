/**
 * 「為什麼沒抓到」：找出格式像個資、但被檢核碼或長度擋下來的字串。
 *
 * ## 為什麼需要
 *
 * 使用者（和評審）會隨手打一組號碼測試，例如 `O123456789`、`12345678`。
 * 這些號碼的檢核碼不對，系統正確地不理它們 —— 但畫面上什麼都沒發生，
 * 看的人只會以為擴充壞了。**沉默的正確比錯誤更傷。**
 *
 * 告訴使用者「這個號碼格式像身分證，但檢核碼不對」，反而能展示本作品的差異：
 * 其他工具不驗檢核碼，任何「1 英文 + 9 數字」都會被當成身分證；我們驗，所以不誤報。
 *
 * ## 與展示網站的關係
 *
 * `site/src/app.ts` 的 `explainMisses()` 是同一套判斷的 HTML 版（另含語意層說明）。
 * 這裡是純文字版，只回報真的被擋下的候選 —— 單純「有中文」不提示，
 * 否則每次貼中文都會跳一次，提示就會被使用者學會無視。
 *
 * 刻意不用 lookbehind：舊版 Safari 解析階段就會丟 SyntaxError（理由同網站版）。
 */

import { isValidTwId, isValidTwPhoneM, isValidTwTax, type Span } from './core';

export interface NearMiss {
  /** 被擋下來的字串 */
  value: string;
  /** 給人看的原因（純文字） */
  reason: string;
}

export function findNearMisses(text: string, spans: Span[]): NearMiss[] {
  const out: NearMiss[] = [];
  const seen = new Set<string>();

  // 以座標判斷是否已被偵測：市話 02-27208889 的後 8 碼單看像統編，
  // 只比字串會把已經遮掉的號碼誤報成「被擋下來」
  const insideDetected = (start: number, end: number): boolean =>
    spans.some((span) => start < span.end && end > span.start);

  const scan = (re: RegExp, handle: (value: string) => string | null): void => {
    for (const match of text.matchAll(re)) {
      const value = match[2];
      if (!value || match.index === undefined) continue;
      const start = match.index + match[1].length;
      if (insideDetected(start, start + value.length) || seen.has(value)) continue;
      const reason = handle(value);
      if (reason) {
        seen.add(value);
        out.push({ value, reason });
      }
    }
  };

  scan(/(^|[^A-Za-z0-9])([A-Za-z][0-9]{9})(?![0-9])/g, (v) =>
    isValidTwId(v) ? null : '格式像身分證，但檢核碼不正確',
  );
  scan(/(^|[^A-Za-z0-9])([A-Za-z][0-9]{7,8})(?![0-9])/g, (v) =>
    `像身分證但位數不足（應為 1 英文 + 9 數字，這個只有 ${v.length - 1} 位數字）`,
  );
  scan(/(^|[^0-9])([0-9]{8})(?![0-9])/g, (v) =>
    isValidTwTax(v) ? null : '符合統一編號的 8 碼長度，但檢核碼不正確',
  );
  scan(/(^|[^0-9])(09[0-9]{6,9})(?![0-9])/g, (v) =>
    isValidTwPhoneM(v) ? null : `09 開頭但長度不符（手機是 10 碼，這個有 ${v.length} 碼）`,
  );

  return out;
}
