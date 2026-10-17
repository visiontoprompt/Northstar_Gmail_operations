export type VncClipboardTarget = {
  clipboardPasteFrom: (text: string) => void;
  sendKey: (keysym: number, code: string, down?: boolean) => void;
  focus: () => void;
};

/** Send clipboard text, then paste it into the currently focused remote field. */
export function pasteTextIntoVnc(
  target: VncClipboardTarget,
  text: string,
  schedule: (callback: () => void, delayMs: number) => unknown = (callback, delayMs) => window.setTimeout(callback, delayMs),
) {
  if (!text) return false;
  target.clipboardPasteFrom(text);
  // Clipboard transfer and keyboard input are separate RFB messages. Keep their
  // order and give the remote X clipboard a moment to accept the transferred text.
  schedule(() => {
    target.focus();
    target.sendKey(0xffe3, "ControlLeft", true);
    target.sendKey(0x0076, "KeyV", true);
    target.sendKey(0x0076, "KeyV", false);
    target.sendKey(0xffe3, "ControlLeft", false);
  }, 180);
  return true;
}
