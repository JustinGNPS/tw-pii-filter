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

  async load(url: URL): Promise<PlaceholderState> {
    if (isNewChatPath(url.pathname)) {
      return this.pendingNewChatState ? copyState(this.pendingNewChatState) : emptyState();
    }
    return loadState(conversationKey(url));
  }

  async save(url: URL, state: PlaceholderState): Promise<void> {
    if (isNewChatPath(url.pathname)) {
      this.pendingNewChatState = copyState(state);
      return;
    }
    await saveState(conversationKey(url), state);
  }

  async adoptNewChatState(previous: URL, next: URL): Promise<boolean> {
    if (!isNewChatPath(previous.pathname)) return false;
    if (!this.pendingNewChatState) return false;

    // 新對話頁上的對照表只能跟著「這一次建立的新對話」走。只要已經離開
    // 新對話頁，就必須消耗或丟棄，不能留給之後另一個新對話使用。
    // 跨站導覽絕不搬移對照表。
    if (previous.host !== next.host) {
      this.pendingNewChatState = null;
      return false;
    }
    if (isNewChatPath(next.pathname)) return false;

    const pending = this.pendingNewChatState;
    this.pendingNewChatState = null;

    const existing = await this.load(next);
    if (Object.keys(existing.assigned).length > 0) return false;

    await this.save(next, pending);
    return true;
  }
}
