/**
 * 把文字寫進使用者正在編輯的輸入框。
 *
 * ## 為什麼不能直接設定 value / innerText
 *
 * ChatGPT、Claude、Gemini 的輸入框都是 React（ChatGPT 更是 ProseMirror）
 * 控制的元件。直接改 `textarea.value` 或 `div.innerText`：
 *   - React 內部的 state 不會更新，畫面看起來變了，送出去的還是舊值
 *   - ProseMirror 會在下一次 transaction 把 DOM 改回它自己的 model
 *
 * 正確做法是產生「與真人輸入無異」的事件。`document.execCommand('insertText')`
 * 雖然標記為 deprecated，但它是目前唯一在 textarea 與 contenteditable 上
 * 都能正確觸發 `beforeinput` / `input` 事件、且會被 React 與 ProseMirror
 * 正常接收的 API，主流擴充也都還在用。
 */

/** 判斷元素是不是可以輸入文字的目標。 */
export function isEditable(target: EventTarget | null): target is HTMLElement {
  if (!(target instanceof HTMLElement)) return false;
  if (target instanceof HTMLTextAreaElement) return !target.disabled && !target.readOnly;
  if (target instanceof HTMLInputElement) {
    const textLike = ['text', 'search', 'url', 'email', 'tel', ''];
    return textLike.includes(target.type) && !target.disabled && !target.readOnly;
  }
  return target.isContentEditable;
}

/**
 * 在目前的游標位置插入文字（會取代選取範圍），語意等同使用者手動貼上。
 *
 * @returns 是否插入成功
 */
export function insertText(element: HTMLElement, text: string): boolean {
  element.focus();

  // 主要路徑：execCommand 會產生完整的 beforeinput / input 事件鏈，
  // React 與 ProseMirror 都能正確接收。
  try {
    if (document.execCommand('insertText', false, text)) {
      return true;
    }
  } catch {
    // 某些頁面會攔截 execCommand，往下走 fallback
  }

  // Fallback：繞過 React 的 value setter 快取，手動觸發 input 事件。
  if (element instanceof HTMLTextAreaElement || element instanceof HTMLInputElement) {
    return insertViaNativeSetter(element, text);
  }

  return insertViaRange(element, text);
}

/**
 * textarea / input 的 fallback。
 *
 * React 會記住它上次寫入的 value，直接設定 `element.value` 會讓 React
 * 認為「值沒變」而略過 onChange。透過 prototype 上的原生 setter 寫入，
 * 再手動 dispatch input 事件，才能讓 React 收到變更。
 */
function insertViaNativeSetter(
  element: HTMLTextAreaElement | HTMLInputElement,
  text: string,
): boolean {
  const prototype =
    element instanceof HTMLTextAreaElement
      ? HTMLTextAreaElement.prototype
      : HTMLInputElement.prototype;
  const setter = Object.getOwnPropertyDescriptor(prototype, 'value')?.set;
  if (!setter) return false;

  const start = element.selectionStart ?? element.value.length;
  const end = element.selectionEnd ?? element.value.length;
  const next = element.value.slice(0, start) + text + element.value.slice(end);

  setter.call(element, next);
  const caret = start + text.length;
  element.setSelectionRange(caret, caret);
  element.dispatchEvent(new Event('input', { bubbles: true }));
  return true;
}

/** contenteditable 的最後 fallback：直接操作 Range，再手動觸發 input。 */
function insertViaRange(element: HTMLElement, text: string): boolean {
  const selection = window.getSelection();
  if (!selection || selection.rangeCount === 0) return false;

  const range = selection.getRangeAt(0);
  range.deleteContents();
  const node = document.createTextNode(text);
  range.insertNode(node);
  range.setStartAfter(node);
  range.collapse(true);
  selection.removeAllRanges();
  selection.addRange(range);

  element.dispatchEvent(new InputEvent('input', { bubbles: true, data: text }));
  return true;
}
