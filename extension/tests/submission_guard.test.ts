import { describe, expect, it } from 'vitest';
import { shouldMarkFromControl, shouldMarkFromKeydown } from '../src/content/submission_guard';

const enter = {
  key: 'Enter',
  shiftKey: false,
  altKey: false,
  isComposing: false,
};

describe('新對話送出判定', () => {
  it('只有實際插入遮蔽文字的輸入框按 Enter 才能標記送出', () => {
    expect(shouldMarkFromKeydown(enter, true, true)).toBe(true);
    expect(shouldMarkFromKeydown(enter, false, true)).toBe(false);
    expect(shouldMarkFromKeydown(enter, true, false)).toBe(false);
  });

  it('換行與輸入法組字不算送出', () => {
    expect(shouldMarkFromKeydown({ ...enter, shiftKey: true }, true, true)).toBe(false);
    expect(shouldMarkFromKeydown({ ...enter, isComposing: true }, true, true)).toBe(false);
  });

  it('Ctrl 或 Cmd+Enter 仍可由同一輸入框送出', () => {
    expect(shouldMarkFromKeydown({ ...enter, ctrlKey: true }, true, true)).toBe(true);
    expect(shouldMarkFromKeydown({ ...enter, metaKey: true }, true, true)).toBe(true);
  });

  it('送出按鈕必須位於同一 composer、可用且輸入框非空', () => {
    const send = { disabled: false, tagName: 'BUTTON', type: null, hint: 'send-button' };
    expect(shouldMarkFromControl(send, true, true)).toBe(true);
    expect(shouldMarkFromControl(send, false, true)).toBe(false);
    expect(shouldMarkFromControl(send, true, false)).toBe(false);
    expect(shouldMarkFromControl({ ...send, disabled: true }, true, true)).toBe(false);
  });
});
