import { describe, expect, it, vi } from 'vitest';
import { PageRestorer } from '../src/content/restorer';

describe('本機還原的 DOM 安全邊界', () => {
  it('只回報還原內容，不把明文寫回 AI 網頁文字節點', () => {
    const onChange = vi.fn();
    const restorer = new PageRestorer(
      null,
      onChange,
      {} as Node,
    );
    restorer.setMap(new Map([['[NAME_1]', '王小明']]));

    const textNode = {
      nodeValue: '您好 [NAME_1]',
      isConnected: true,
      parentElement: {
        closest: () => null,
        isContentEditable: false,
      },
    } as unknown as Text;

    // 私有方法在執行期仍是一般方法；此處直接驗證最重要的安全不變量。
    (restorer as unknown as { restoreNode(node: Text): void }).restoreNode(textNode);
    (restorer as unknown as { report(): void }).report();

    expect(textNode.nodeValue).toBe('您好 [NAME_1]');
    expect(onChange).toHaveBeenLastCalledWith(1, ['您好 王小明']);
  });
});
