/**
 * 單一分頁的對話狀態管理。
 *
 * 新對話頁（ChatGPT `/`、Claude `/new`、Gemini `/app`）沒有對話 ID；若直接用
 * `host + pathname` 存進 chrome.storage.local，兩個分頁會寫到同一把 key，最後可能
 * 把 A 分頁的 `[NAME_1]` 用 B 分頁的人名還原。新對話狀態因此只留在本 content
 * script 的記憶體，等網址取得真正的對話 ID 後才寫入持久儲存。
 */

import {
  conversationKey,
  emptyState,
  loadState,
  saveState,
  type PlaceholderState,
} from '../placeholder';
import { isNewChatPath } from '../restore';

function copyState(state: PlaceholderState): PlaceholderState {
  return structuredClone(state);
}

export class ConversationStateStore {
  private pendingNewChatState: PlaceholderState | null = null;
  private pendingNewChatSubmitted = false;

  async load(url: URL): Promise<PlaceholderState> {
    if (isNewChatPath(url.pathname)) {
      return this.pendingNewChatState ? copyState(this.pendingNewChatState) : emptyState();
    }
    return loadState(conversationKey(url));
  }

  async save(url: URL, state: PlaceholderState): Promise<void> {
    if (isNewChatPath(url.pathname)) {
      if (!this.pendingNewChatState) this.pendingNewChatSubmitted = false;
      this.pendingNewChatState = copyState(state);
      return;
    }
    await saveState(conversationKey(url), state);
  }

  markNewChatSubmitted(url: URL): boolean {
    if (!isNewChatPath(url.pathname) || !this.pendingNewChatState) return false;
    this.pendingNewChatSubmitted = true;
    return true;
  }

  async adoptNewChatState(previous: URL, next: URL): Promise<boolean> {
    if (!isNewChatPath(previous.pathname)) return false;
    if (!this.pendingNewChatState) return false;

    // 新對話頁上的對照表只能跟著「這一次建立的新對話」走。只要已經離開
    // 新對話頁，就必須消耗或丟棄，不能留給之後另一個新對話使用。
    // 跨站導覽絕不搬移對照表。
    if (previous.host !== next.host) {
      this.pendingNewChatState = null;
      this.pendingNewChatSubmitted = false;
      return false;
    }
    if (isNewChatPath(next.pathname)) return false;

    const pending = this.pendingNewChatState;
    const submitted = this.pendingNewChatSubmitted;
    this.pendingNewChatState = null;
    this.pendingNewChatSubmitted = false;

    // 只有真的按下送出後，網站把新對話網址換成對話 ID，才能搬移暫存。
    // 單靠 URL 無法分辨這種情況和「從側欄點進沒有本機對照表的舊對話」。
    if (!submitted) return false;

    const existing = await this.load(next);
    if (Object.keys(existing.assigned).length > 0) return false;

    await this.save(next, pending);
    return true;
  }
}
