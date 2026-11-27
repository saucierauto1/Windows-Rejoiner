"""Lock the mouse to one monitor with a hotkey (press again to free it).

Windows drops a cursor lock when windows change focus, so while it's on we re-apply it 10 times a second.
"""

import sys
import threading
import time

IS_WIN = sys.platform == "win32"
MODS = {"alt": 0x1, "ctrl": 0x2, "control": 0x2, "shift": 0x4, "win": 0x8}
NAMED = {"pause": 0x13, "scrolllock": 0x91, "insert": 0x2D, "home": 0x24, "end": 0x23,
         "pageup": 0x21, "pagedown": 0x22, "delete": 0x2E, "space": 0x20}


def parse_hotkey(text):
    """'ctrl+alt+l' / 'f8' -> (modifiers, virtual key), or None if not understood"""
    parts = [p.strip().lower() for p in (text or "").replace(" ", "").split("+") if p.strip()]
    if not parts:
        return None
    mods, key = 0, parts[-1]
    for p in parts[:-1]:
        if p not in MODS:
            return None
        mods |= MODS[p]
    if len(key) == 1 and key.isalnum():
        vk = ord(key.upper())
    elif key.startswith("f") and key[1:].isdigit() and 1 <= int(key[1:]) <= 24:
        vk = 0x70 + int(key[1:]) - 1
    elif key in NAMED:
        vk = NAMED[key]
    else:
        return None
    if mods == 0 and len(key) == 1:
        return None          # a plain letter/number would fire while typing
    return mods, vk


class Hotkey:
    """one more global hotkey that runs a function (hide / show all roblox windows)"""

    def __init__(self, log, name, action):
        self.log = log
        self.name = name
        self.action = action
        self._tid = None

    def start(self, hotkey):
        self.stop()
        if not IS_WIN:
            return False
        parsed = parse_hotkey(hotkey)
        if not parsed:
            self.log(f"{self.name}: didn't understand the hotkey '{hotkey}'")
            return False
        ok = threading.Event()
        res = {}

        def loop():
            import ctypes
            from ctypes import wintypes
            u, k = ctypes.windll.user32, ctypes.windll.kernel32
            self._tid = k.GetCurrentThreadId()
            res["ok"] = bool(u.RegisterHotKey(None, 2, parsed[0] | 0x4000, parsed[1]))
            ok.set()
            if not res["ok"]:
                return
            msg = wintypes.MSG()
            while u.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                if msg.message == 0x0312:        # WM_HOTKEY
                    try:
                        self.action()
                    except Exception as e:
                        self.log(f"{self.name}: {e}")
            u.UnregisterHotKey(None, 2)

        threading.Thread(target=loop, daemon=True).start()
        ok.wait(3)
        if res.get("ok"):
            self.log(f"{self.name}: press {hotkey}")
            return True
        self.log(f"{self.name}: {hotkey} is already used by another program, pick another key in Settings")
        return False

    def stop(self):
        if IS_WIN and self._tid:
            import ctypes
            ctypes.windll.user32.PostThreadMessageW(self._tid, 0x0012, 0, 0)   # WM_QUIT
        self._tid = None


class MouseLock:
    def __init__(self, log):
        self.log = log
        self.locked = False
        self.monitor = ""
        self._tid = None
        self._thread = None
        self._rect = None

    # ---------------- hotkey
    def start(self, hotkey):
        self.stop_hotkey()
        if not IS_WIN:
            return False
        parsed = parse_hotkey(hotkey)
        if not parsed:
            self.log(f"mouse lock: didn't understand the hotkey '{hotkey}'")
            return False
        ok = threading.Event()
        res = {}

        def loop():
            import ctypes
            from ctypes import wintypes
            u, k = ctypes.windll.user32, ctypes.windll.kernel32
            self._tid = k.GetCurrentThreadId()
            res["ok"] = bool(u.RegisterHotKey(None, 1, parsed[0] | 0x4000, parsed[1]))   # 0x4000: no auto-repeat
            ok.set()
            if not res["ok"]:
                return
            msg = wintypes.MSG()
            while u.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                if msg.message == 0x0312:        # WM_HOTKEY
                    self.toggle()
            u.UnregisterHotKey(None, 1)

        self._thread = threading.Thread(target=loop, daemon=True)
        self._thread.start()
        ok.wait(3)
        if res.get("ok"):
            self.log(f"mouse lock: press {hotkey} to lock / unlock the mouse to a monitor")
            return True
        self.log(f"mouse lock: {hotkey} is already used by another program, pick another key in Settings")
        return False

    def stop_hotkey(self):
        if IS_WIN and self._tid:
            import ctypes
            ctypes.windll.user32.PostThreadMessageW(self._tid, 0x0012, 0, 0)   # WM_QUIT
        self._tid = None

    # ---------------- the lock itself
    def toggle(self):
        if self.locked:
            self.unlock()
        else:
            self.lock()

    def lock(self):
        if not IS_WIN:
            return
        import ctypes
        from ctypes import wintypes

        class MONITORINFOEX(ctypes.Structure):
            _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT), ("rcWork", wintypes.RECT),
                        ("dwFlags", wintypes.DWORD), ("szDevice", wintypes.WCHAR * 32)]
        u = ctypes.windll.user32
        pt = wintypes.POINT()
        u.GetCursorPos(ctypes.byref(pt))
        mon = u.MonitorFromPoint(pt, 2)           # the monitor the mouse is on
        info = MONITORINFOEX()
        info.cbSize = ctypes.sizeof(MONITORINFOEX)
        u.GetMonitorInfoW(mon, ctypes.byref(info))
        self._rect = info.rcMonitor
        self.monitor = info.szDevice.replace("\\\\.\\", "").lower()
        self.locked = True
        self.log(f"mouse locked to {self.monitor}")

        def keep():
            while self.locked:
                u.ClipCursor(ctypes.byref(self._rect))
                time.sleep(0.1)
            u.ClipCursor(None)
        threading.Thread(target=keep, daemon=True).start()

    def unlock(self):
        if self.locked:
            self.locked = False
            self.log("mouse unlocked")
        if IS_WIN:
            import ctypes
            ctypes.windll.user32.ClipCursor(None)
