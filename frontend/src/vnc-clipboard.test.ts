import { describe, expect, it, vi } from "vitest";
import { pasteTextIntoVnc, type VncClipboardTarget } from "./vnc-clipboard";

describe("pasteTextIntoVnc", () => {
  it("does nothing for empty text", () => {
    const target: VncClipboardTarget = { clipboardPasteFrom: vi.fn(), sendKey: vi.fn(), focus: vi.fn() };
    const schedule = vi.fn();
    expect(pasteTextIntoVnc(target, "", schedule)).toBe(false);
    expect(target.clipboardPasteFrom).not.toHaveBeenCalled();
    expect(schedule).not.toHaveBeenCalled();
  });

  it("transfers the clipboard before focusing and sending Ctrl+V", () => {
    const events: string[] = [];
    const target: VncClipboardTarget = {
      clipboardPasteFrom: (text) => events.push(`clipboard:${text}`),
      focus: () => events.push("focus"),
      sendKey: (keysym, code, down) => events.push(`${code}:${keysym}:${down}`),
    };
    let paste: (() => void) | undefined;
    const schedule = vi.fn((callback: () => void, delay: number) => { paste = callback; expect(delay).toBe(180); });

    expect(pasteTextIntoVnc(target, "hello from local clipboard", schedule)).toBe(true);
    expect(events).toEqual(["clipboard:hello from local clipboard"]);
    expect(schedule).toHaveBeenCalledOnce();
    paste?.();
    expect(events).toEqual([
      "clipboard:hello from local clipboard",
      "focus",
      "ControlLeft:65507:true",
      "KeyV:118:true",
      "KeyV:118:false",
      "ControlLeft:65507:false",
    ]);
  });
});
