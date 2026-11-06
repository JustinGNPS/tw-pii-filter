export interface SubmissionKey {
  key: string;
  shiftKey: boolean;
  altKey: boolean;
  isComposing: boolean;
  ctrlKey?: boolean;
  metaKey?: boolean;
}

export function shouldMarkFromKeydown(
  event: SubmissionKey,
  fromPendingInput: boolean,
  pendingInputHasText: boolean,
): boolean {
  return event.key === 'Enter'
    && !event.shiftKey
    && !event.altKey
    && !event.isComposing
    && fromPendingInput
    && pendingInputHasText;
}

export interface SubmissionControl {
  disabled: boolean;
  tagName: string;
  type: string | null;
  hint: string;
}

export function shouldMarkFromControl(
  control: SubmissionControl,
  sharesPendingComposer: boolean,
  pendingInputHasText: boolean,
): boolean {
  if (control.disabled || !sharesPendingComposer || !pendingInputHasText) return false;
  const explicitSubmit = control.tagName === 'BUTTON' && control.type === 'submit';
  return explicitSubmit
    || /(^|[-_\s])(send|submit)([-_\s]|$)|送出|傳送|发送/.test(control.hint.toLowerCase());
}
