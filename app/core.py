"""NovaHop Rejoin for Windows: accounts, launching Roblox, and the watcher.

Each account's NovaHop_Heartbeat.lua (in the executor's autoexec) writes
NovaHop/hb/<UserId>.txt = "time|ok" or "time|error|message|name" every 3 s.
The watcher reads those files and restarts only that account's Roblox window when
an error screen stays up, the heartbeat stops, or the window closes.
"""

import base64
import calendar
import json
import os
import random
import re
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

import psutil

BEE_SWARM = "1537690962"
ERROR_WAIT = 20      # error screen up this long -> restart (NovaHop gets a chance first)
STALE = 120          # no heartbeat this long -> restart
BOOT_WAIT = 180      # after a launch, give the window this long to load in (slow injectors need time)
GONE_WAIT = 10       # a tracked window that closed is relaunched after this long
RETRY = 900          # after 3 restarts that didn't bring it back, only retry this often
CHECK = 5
HOP_WAIT = 90        # teleport failed / server full: NovaHop retries itself, so wait longer before restarting
HOPPING = re.compile(r"teleport|experience is full|server is full|game is full|full server|unable to join|"
                     r"requested experience|joining|restricted|no longer available|\b(769|770|771|772|773)\b", re.I)
ROBLOX_EXE = "robloxplayerbeta.exe"
HEARTBEAT_LINE = ('loadstring(game:HttpGet("https://gist.githubusercontent.com/saucierauto1/'
                  '4e760d58e39c190bb537f599d9d788a8/raw/NovaHop_Heartbeat.lua"))()')
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")

IS_WIN = sys.platform == "win32"
DATA_DIR = os.path.join(os.environ.get("APPDATA") or os.path.expanduser("~"), "NovaHopRejoin")


# ------------------------------------------------------------------ encryption (Windows DPAPI)
# Sessions are encrypted with the Windows account's own key: only this Windows user on this PC
# can read them back.
if IS_WIN:
    import ctypes
    from ctypes import wintypes

    class _Blob(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    def _dpapi(data: bytes, protect: bool) -> bytes:
        buf = ctypes.create_string_buffer(data, len(data))
        src = _Blob(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))
        dst = _Blob()
        fn = ctypes.windll.crypt32.CryptProtectData if protect else ctypes.windll.crypt32.CryptUnprotectData
        if not fn(ctypes.byref(src), None, None, None, None, 0x1, ctypes.byref(dst)):
            raise OSError("Windows could not encrypt/decrypt the saved login")
        try:
            return ctypes.string_at(dst.pbData, dst.cbData)
        finally:
            ctypes.windll.kernel32.LocalFree(dst.pbData)

    # own copies of kernel32/user32 with proper 64-bit types (other libraries keep using ctypes.windll)
    _k = ctypes.WinDLL("kernel32", use_last_error=True)
    _u = ctypes.WinDLL("user32", use_last_error=True)
    H = wintypes.HANDLE
    for _n, _r, _a in (("OpenProcess", H, [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]),
                       ("CloseHandle", wintypes.BOOL, [H]),
                       ("CreateJobObjectW", H, [wintypes.LPVOID, wintypes.LPCWSTR]),
                       ("AssignProcessToJobObject", wintypes.BOOL, [H, H]),
                       ("SetInformationJobObject", wintypes.BOOL, [H, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD]),
                       ("SetPriorityClass", wintypes.BOOL, [H, wintypes.DWORD]),
                       ("SetProcessInformation", wintypes.BOOL, [H, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD]),
                       ("K32EmptyWorkingSet", wintypes.BOOL, [H]),
                       ("CreateMutexW", H, [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR])):
        getattr(_k, _n).restype, getattr(_k, _n).argtypes = _r, _a
    WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    for _n, _r, _a in (("EnumWindows", wintypes.BOOL, [WNDENUMPROC, wintypes.LPARAM]),
                       ("GetWindowThreadProcessId", wintypes.DWORD, [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]),
                       ("IsWindowVisible", wintypes.BOOL, [wintypes.HWND]),
                       ("IsIconic", wintypes.BOOL, [wintypes.HWND]),
                       ("GetWindowRect", wintypes.BOOL, [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]),
                       ("SetForegroundWindow", wintypes.BOOL, [wintypes.HWND]),
                       ("GetWindow", wintypes.HWND, [wintypes.HWND, ctypes.c_uint]),
                       ("ShowWindow", wintypes.BOOL, [wintypes.HWND, ctypes.c_int]),
                       ("SetWindowPos", wintypes.BOOL, [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
                                                        ctypes.c_int, ctypes.c_int, ctypes.c_uint])):
        getattr(_u, _n).restype, getattr(_u, _n).argtypes = _r, _a
else:
    def _dpapi(data: bytes, protect: bool) -> bytes:   # only for testing off Windows
        return data


def protect(text: str) -> str:
    return base64.b64encode(_dpapi(text.encode(), True)).decode()


def unprotect(blob: str) -> str:
    return _dpapi(base64.b64decode(blob), False).decode()


# ------------------------------------------------------------------ settings and accounts on disk
def _path(name):
    os.makedirs(DATA_DIR, exist_ok=True)
    return os.path.join(DATA_DIR, name)


def _load(name, default):
    try:
        with open(_path(name), encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def _save(name, data):
    tmp = _path(name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, _path(name))


class Store:
    """accounts.json: [{uid, name, display, cookie (encrypted), tracker, enabled}]"""

    def __init__(self):
        self.lock = threading.RLock()
        self.accounts = _load("accounts.json", [])
        self.settings = _load("settings.json", {})
        self.settings.setdefault("place", BEE_SWARM)
        self.settings.setdefault("ps_raw", "")
        self.settings.setdefault("launch_delay", 12)
        self.settings.setdefault("hb_dirs", [])
        for k, v in (("error_wait", ERROR_WAIT), ("stale", STALE), ("webhook", ""), ("wh_ping", ""),
                     ("wh_restart", True), ("wh_stuck", True), ("wh_login", True), ("wh_startstop", False),
                     ("autostart", False), ("on_top", False), ("text_color", "#FFFFFF"),
                     ("wh_status", True), ("status_every", 60), ("shots", True), ("status_msg", ""),
                     ("bg_color", "#000000"), ("btn_color", "#1A1A1A"), ("bg_image", ""), ("alpha", 1.0),
                     ("show_log", True), ("tray", True), ("update_check", True), ("launch_min", False), ("hop_wait", HOP_WAIT),
                     ("mouse_lock", False), ("mouse_key", "ctrl+alt+l"),
                     ("boot_wait", BOOT_WAIT), ("fps_cap", 0), ("ram_trim", 0), ("ram_limit", 0), ("ax_dirs", []),
                     ("low_gfx", False), ("eco", False), ("cpu_cap", 0), ("flag_backup", {}), ("read_logs", False),
                     ("win_start", False), ("hide_on", False), ("hide_key", "ctrl+alt+h")):
            self.settings.setdefault(k, v)
        self.settings.setdefault("win_size", "small" if self.settings.pop("shrink", False) else "normal")

    def save(self):
        with self.lock:
            _save("accounts.json", self.accounts)
            _save("settings.json", self.settings)

    def get(self, uid):
        with self.lock:
            return next((a for a in self.accounts if a["uid"] == uid), None)

    def add(self, uid, name, display, cookie):
        with self.lock:
            a = self.get(uid)
            if a is None:
                a = {"uid": uid, "tracker": str(random.randint(10**11, 10**12 - 1)), "enabled": True}
                self.accounts.append(a)
            a.update(name=name, display=display, cookie=protect(cookie))
            a.pop("relogin", None)
            self.save()
            return a

    # this PC's folders and Discord message id don't make sense on another PC
    LOCAL_ONLY = ("hb_dirs", "ax_dirs", "flag_backup", "status_msg", "bg_image")

    def export(self, path):
        with self.lock:
            data = {"novahop_rejoin": 1, "accounts": self.accounts,
                    "settings": {k: v for k, v in self.settings.items() if k not in self.LOCAL_ONLY}}
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)

    def import_file(self, path):
        """-> (accounts added or updated, accounts that need logging in again on this PC)"""
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict) or "accounts" not in data:
            raise ValueError("that isn't a NovaHop Rejoin export file")
        n, relog = 0, []
        with self.lock:
            for k, v in (data.get("settings") or {}).items():
                if k not in self.LOCAL_ONLY:
                    self.settings[k] = v
            for a in data["accounts"]:
                if not isinstance(a, dict) or not a.get("uid"):
                    continue
                a = dict(a)
                try:
                    unprotect(a.get("cookie", ""))
                    a.pop("relogin", None)
                except Exception:          # logins are locked to the Windows account that saved them
                    a["relogin"] = True
                    relog.append(a.get("name") or a["uid"])
                old = self.get(a["uid"])
                if old and not a.get("relogin"):
                    old.update(a)
                elif old:
                    old.update({k: v for k, v in a.items() if k not in ("cookie", "relogin")})
                    try:
                        unprotect(old.get("cookie", ""))
                        relog.remove(a.get("name") or a["uid"])
                    except Exception:
                        old["relogin"] = True
                else:
                    self.accounts.append(a)
                n += 1
            self.save()
        return n, relog

    def remove(self, uid):
        with self.lock:
            self.accounts = [a for a in self.accounts if a["uid"] != uid]
            self.save()

    def cookie(self, a):
        return unprotect(a["cookie"])


# ------------------------------------------------------------------ Roblox web calls
class RobloxError(Exception):
    pass


class LoginExpired(RobloxError):
    pass


def _request(url, cookie, method="GET", body=None, csrf=None):
    data = json.dumps(body).encode() if body is not None else (b"" if method == "POST" else None)
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Cookie", ".ROBLOSECURITY=" + cookie)
    req.add_header("User-Agent", UA)
    req.add_header("Referer", "https://www.roblox.com/")
    req.add_header("Origin", "https://www.roblox.com")
    req.add_header("RBXAuthenticationNegotiation", "1")
    if method == "POST":
        req.add_header("Content-Type", "application/json")
    if csrf:
        req.add_header("X-CSRF-TOKEN", csrf)
    return urllib.request.urlopen(req, timeout=20)


def _why(e):
    """Roblox's own error text from a failed request (never contains the login)"""
    try:
        raw = e.read().decode("utf-8", "ignore")
        d = json.loads(raw)
        errs = d.get("errors") or []
        if errs:
            return "; ".join(str(x.get("message", x)) for x in errs)[:200]
        return raw[:200]
    except Exception:
        return "no details"


def _fail(e, url):
    """a failed Roblox request -> an error that says what to do"""
    if e.code == 401:
        return LoginExpired("login expired, log in again (remove the account and add it again)")
    what = {403: "roblox refused it (2-step check, captcha or a new-login block). wait a few minutes and try again",
            429: "roblox says too many tries. wait a minute and try again"}.get(
        e.code, "roblox's servers had a problem, try again in a minute" if e.code >= 500 else "")
    return RobloxError(f"{what} ({e.code} from {url.split('/')[2]}: {_why(e)})" if what
                       else f"Roblox said {e.code} for {url.split('/')[2]}: {_why(e)}")


def _post(url, cookie, body=None):
    """POST with Roblox's CSRF dance: the first try returns 403 + a token to retry with."""
    csrf = None
    for i in range(3):
        try:
            return _request(url, cookie, "POST", body, csrf)
        except urllib.error.HTTPError as e:
            if e.code == 403 and e.headers.get("x-csrf-token") and e.headers["x-csrf-token"] != csrf:
                csrf = e.headers["x-csrf-token"]
                continue
            if e.code == 429 and i < 2:
                time.sleep(5)
                continue
            raise _fail(e, url) from e
    raise RobloxError("roblox kept refusing the security token, try again in a minute")


def whoami(cookie):
    url = "https://users.roblox.com/v1/users/authenticated"
    for i in range(2):
        try:
            with _request(url, cookie) as r:
                d = json.load(r)
            return str(d["id"]), d.get("name", ""), d.get("displayName", "")
        except urllib.error.HTTPError as e:
            if e.code == 429 and i == 0:
                time.sleep(5)
                continue
            raise _fail(e, url) from e


def auth_ticket(cookie):
    with _post("https://auth.roblox.com/v1/authentication-ticket", cookie) as r:
        t = r.headers.get("rbx-authentication-ticket")
    if not t:
        raise RobloxError("Roblox didn't give a launch ticket")
    return t


def parse_game(text):
    """Game link or place ID -> place ID ('' = Bee Swarm). None if not understood."""
    t = (text or "").strip()
    if not t:
        return BEE_SWARM
    m = re.search(r"games/(\d+)", t) or re.search(r"[?&]placeId=(\d+)", t)
    if m:
        return m.group(1)
    return t if t.isdigit() else None


def parse_ps(text):
    """Private server link -> ('code', linkCode) / ('share', code) / ('', ''). None if not understood."""
    t = (text or "").strip()
    if not t:
        return "", ""
    m = re.search(r"privateServerLinkCode=([A-Za-z0-9_-]+)", t)
    if m:
        return "code", m.group(1)
    m = re.search(r"[?&]code=([A-Za-z0-9_-]+)", t)
    if m:
        return "share", m.group(1)
    return None


def resolve_share(cookie, code):
    """roblox.com/share?code=...&type=Server -> (placeId, linkCode)"""
    with _post("https://apis.roblox.com/sharelinks/v1/resolve-link", cookie,
               {"linkId": code, "linkType": "Server"}) as r:
        d = json.load(r)
    inv = d.get("privateServerInviteData") or {}
    if not inv.get("linkCode"):
        raise RobloxError("That private server link didn't work (expired, or no access)")
    return str(inv.get("placeId") or ""), inv["linkCode"]


def launch_uri(ticket, tracker, place, link_code=""):
    if link_code:
        q = (f"request=RequestPrivateGame&placeId={place}&linkCode={link_code}"
             f"&browserTrackerId={tracker}&joinAttemptId={uuid.uuid4()}&joinAttemptOrigin=PlayButton")
    else:
        q = (f"request=RequestGame&browserTrackerId={tracker}&placeId={place}&isPlayTogetherGame=false"
             f"&joinAttemptId={uuid.uuid4()}&joinAttemptOrigin=PlayButton")
    launcher = "https://www.roblox.com/Game/PlaceLauncher.ashx?" + q
    return ("roblox-player:1+launchmode:play+gameinfo:" + ticket
            + "+launchtime:" + str(int(time.time() * 1000))
            + "+placelauncherurl:" + urllib.parse.quote(launcher, safe="")
            + "+browsertrackerid:" + tracker
            + "+robloxLocale:en_us+gameLocale:en_us+channel:+LaunchExp:InApp")


def server_for(a, s):
    """(place, private server link) for an account: its own if set, else the main Game / server setting"""
    if a.get("own_server"):
        return a.get("place") or BEE_SWARM, a.get("ps_raw", "")
    return s["place"], s.get("ps_raw", "")


_names = {BEE_SWARM: "Bee Swarm Simulator"}


def game_name(place):
    """the game's real name (looked up once in the background), 'Place 123' until it's known"""
    place = str(place)
    if place not in _names:
        _names[place] = f"Place {place}"

        def look():
            try:
                h = {"User-Agent": UA}
                with urllib.request.urlopen(urllib.request.Request(
                        f"https://apis.roblox.com/universes/v1/places/{place}/universe", headers=h), timeout=10) as r:
                    uni = json.load(r)["universeId"]
                with urllib.request.urlopen(urllib.request.Request(
                        f"https://games.roblox.com/v1/games?universeIds={uni}", headers=h), timeout=10) as r:
                    _names[place] = json.load(r)["data"][0]["name"]
            except Exception:
                pass
        threading.Thread(target=look, daemon=True).start()
    return _names[place]


# ------------------------------------------------------------------ updates
REPO = "saucierauto1/Windows-Rejoiner"


def latest_version():
    """newest release number on GitHub (tags look like win-v1.0.12), or None"""
    req = urllib.request.Request(f"https://api.github.com/repos/{REPO}/releases/latest",
                                 headers={"User-Agent": "NovaHopRejoin", "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(req, timeout=10) as r:
        tag = json.load(r).get("tag_name", "")
    m = re.search(r"(\d+)$", tag)
    return int(m.group(1)) if m else None


# ------------------------------------------------------------------ Discord webhook
def is_webhook(url):
    return bool(re.match(r"^https://(ptb\.|canary\.)?discord(app)?\.com/api/webhooks/\d+/[\w-]+$", (url or "").strip()))


def send_webhook(url, text):
    data = json.dumps({"content": text[:1900], "username": "NovaHop Rejoin"}).encode()
    req = urllib.request.Request(url.strip(), data=data, method="POST",
                                 headers={"Content-Type": "application/json", "User-Agent": "NovaHopRejoin/1.0"})
    urllib.request.urlopen(req, timeout=10).close()


def send_status(url, embed, jpg=None):
    """Post the status message (optionally with a screenshot). Returns the new message id."""
    boundary = "----nhr%d" % int(time.time() * 1000)
    payload = json.dumps({"username": "NovaHop Rejoin", "embeds": [embed]}).encode()
    body = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"payload_json\"\r\n"
            f"Content-Type: application/json\r\n\r\n").encode() + payload + b"\r\n"
    if jpg:
        body += (f"--{boundary}\r\nContent-Disposition: form-data; name=\"files[0]\"; filename=\"screen.jpg\"\r\n"
                 f"Content-Type: image/jpeg\r\n\r\n").encode() + jpg + b"\r\n"
    body += f"--{boundary}--\r\n".encode()
    req = urllib.request.Request(url.strip() + "?wait=true", data=body, method="POST",
                                 headers={"Content-Type": "multipart/form-data; boundary=" + boundary,
                                          "User-Agent": "NovaHopRejoin/1.0"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return str(json.load(r).get("id", ""))


def delete_message(url, mid):
    req = urllib.request.Request(f"{url.strip()}/messages/{mid}", method="DELETE",
                                 headers={"User-Agent": "NovaHopRejoin/1.0"})
    urllib.request.urlopen(req, timeout=15).close()


# ------------------------------------------------------------------ several Roblox windows at once
_mutex = None


def allow_multi():
    """Holding Roblox's single-instance mutex lets several Roblox windows run side by side.
    Returns False when Roblox was already open (then it only works after closing Roblox)."""
    global _mutex
    if not IS_WIN or _mutex:
        return True
    _mutex = _k.CreateMutexW(None, True, "ROBLOX_singletonMutex")
    return ctypes.get_last_error() != 183   # 183 = already existed


def roblox_installed():
    """the roblox-player: link is registered (by Roblox itself, Bloxstrap or Fishstrap)"""
    if not IS_WIN:
        return True
    import winreg
    for hive, key in ((winreg.HKEY_CURRENT_USER, r"Software\Classes\roblox-player"),
                      (winreg.HKEY_CLASSES_ROOT, "roblox-player")):
        try:
            winreg.CloseKey(winreg.OpenKey(hive, key))
            return True
        except OSError:
            pass
    return False


def set_autorun(on):
    """start the app with Windows (HKCU Run key)"""
    if not IS_WIN:
        return
    import winreg
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run", 0,
                        winreg.KEY_SET_VALUE) as k:
        if on:
            if getattr(sys, "frozen", False):
                cmd = f'"{sys.executable}"'
            else:
                exe = sys.executable.replace("python.exe", "pythonw.exe")
                cmd = f'"{exe}" "{os.path.join(os.path.dirname(os.path.abspath(__file__)), "main.py")}"'
            winreg.SetValueEx(k, "NovaHopRejoin", 0, winreg.REG_SZ, cmd)
        else:
            try:
                winreg.DeleteValue(k, "NovaHopRejoin")
            except OSError:
                pass


# ------------------------------------------------------------------ heartbeat folders
def find_heartbeat_dirs():
    """Every NovaHop/hb folder an executor workspace has, plus its autoexec folder(s)."""
    home = os.path.expanduser("~")
    roots = [(home, 5)]
    for env in ("LOCALAPPDATA", "APPDATA"):
        if os.environ.get(env):
            roots.append((os.environ[env], 3))
    if IS_WIN:
        for d in ("C:\\", "D:\\"):
            if os.path.isdir(d):
                roots.append((d, 2))
    skip = {"windows", "program files", "program files (x86)", "programdata", "$recycle.bin", "node_modules",
            ".git", "packages", "microsoft", "google", "mozilla", "temp", "cache", "steamapps",
            "system volume information", "appdata"}
    workspaces, autoexecs = set(), set()

    def walk(path, depth):
        try:
            it = list(os.scandir(path))
        except OSError:
            return
        names = {e.name.lower(): e for e in it if e.is_dir(follow_symlinks=False)}
        for low, e in names.items():
            if low == "workspace":
                workspaces.add(e.path)
            if low.startswith("autoexec") or low in ("auto-exec", "autorun"):
                autoexecs.add(e.path)
        if depth <= 0:
            return
        for low, e in names.items():
            if low in skip or low.startswith("."):
                continue
            walk(e.path, depth - 1)

    for root, depth in roots:
        walk(root, depth)
    hb = []
    for w in sorted(workspaces):
        p = os.path.join(w, "NovaHop", "hb")
        if os.path.isdir(p):
            hb.append(p)
    return hb, sorted(workspaces), sorted(autoexecs)


def install_heartbeat(autoexecs):
    done = []
    for d in autoexecs:
        try:
            if any("NovaHop_Heartbeat" in open(os.path.join(d, f), encoding="utf-8", errors="ignore").read()
                   for f in os.listdir(d) if os.path.isfile(os.path.join(d, f))):
                done.append(d)
                continue
            with open(os.path.join(d, "heartbeat.lua"), "w", encoding="utf-8") as f:
                f.write(HEARTBEAT_LINE + "\n")
            done.append(d)
        except OSError:
            pass
    return done


# ------------------------------------------------------------------ fps / ram limiters
FPS_FILE = "novahop_fps.lua"
FPS_FLAG = "DFIntTaskSchedulerTargetFps"
# lowest graphics: only flags Roblox still allows in ClientAppSettings.json (same set RoALT / Bloxstrap use)
LOW_FLAGS = {"DFIntCSGLevelOfDetailSwitchingDistance": "0", "DFIntCSGLevelOfDetailSwitchingDistanceL12": "0",
             "DFIntCSGLevelOfDetailSwitchingDistanceL23": "0", "DFIntCSGLevelOfDetailSwitchingDistanceL34": "0",
             "FIntDebugForceMSAASamples": "1", "DFFlagTextureQualityOverrideEnabled": "True",
             "DFIntTextureQualityOverride": "0", "FFlagDebugSkyGray": "True", "FIntFRMMinGrassDistance": "0",
             "FIntFRMMaxGrassDistance": "0", "DFIntDebugFRMQualityLevelOverride": "1"}


def fps_lua(cap):
    """capped while in a server, uncapped while teleporting so hops load at full speed
    (the executor runs autoexec again on the new server, which caps it again once it has loaded)"""
    return ("if setfpscap then task.spawn(function() if not game:IsLoaded() then game.Loaded:Wait() end "
            f"task.wait(5) setfpscap({cap}) game:GetService(\"Players\").LocalPlayer.OnTeleport:Connect(function(s) "
            f"if s == Enum.TeleportState.Failed then setfpscap({cap}) else setfpscap(240) end end) end) end\n")


def _client_settings_dirs():
    """Roblox's ClientSettings folders (every installed version, plus Bloxstrap / Fishstrap)"""
    out = []
    la = os.environ.get("LOCALAPPDATA", "")
    vers = os.path.join(la, "Roblox", "Versions")
    try:
        for v in os.listdir(vers):
            if os.path.isfile(os.path.join(vers, v, "RobloxPlayerBeta.exe")):
                out.append(os.path.join(vers, v, "ClientSettings"))
    except OSError:
        pass
    for b in ("Bloxstrap", "Fishstrap"):
        if os.path.isdir(os.path.join(la, b)):
            out.append(os.path.join(la, b, "Modifications", "ClientSettings"))
    return out


def apply_fps(cap, autoexecs, low=False, backup=None):
    """cap > 0: put an fps cap in every autoexec folder and in Roblox's settings file. 0 removes both.
    low: lowest graphics flags in Roblox's settings file too (removed when off).
    backup {file: {flag: the user's own value or None}} remembers what was there before, so turning
    it off puts the user's own flags back instead of deleting them."""
    backup = {} if backup is None else backup
    n = 0
    for d in autoexecs:
        f = os.path.join(d, FPS_FILE)
        try:
            if cap > 0:
                with open(f, "w", encoding="utf-8") as fh:
                    fh.write(fps_lua(cap))
                n += 1
            elif os.path.exists(f):
                os.remove(f)
        except OSError:
            pass
    for d in _client_settings_dirs():
        f = os.path.join(d, "ClientAppSettings.json")
        try:
            data = {}
            if os.path.exists(f):
                with open(f, encoding="utf-8-sig") as fh:
                    data = json.load(fh)
            if not isinstance(data, dict):
                continue                 # not a flag list we understand: leave the user's file alone
            old = dict(data)
            mine = backup.setdefault(f, {})
            want = dict(LOW_FLAGS) if low else {}
            if cap > 0 and n == 0:     # Roblox's own cap also slows loading, so only when no executor can cap it
                want[FPS_FLAG] = str(cap)
            for k in [FPS_FLAG, *LOW_FLAGS]:
                if k in want:
                    if k not in mine:     # same value already there = written by an older version of this app
                        mine[k] = None if data.get(k) == want[k] else data.get(k)
                    data[k] = want[k]
                elif k in mine:
                    if mine[k] is None:
                        data.pop(k, None)
                    else:
                        data[k] = mine[k]
                    del mine[k]
            if not mine:
                backup.pop(f, None)
            if data == old:
                continue
            os.makedirs(d, exist_ok=True)
            with open(f + ".tmp", "w", encoding="utf-8") as fh:
                json.dump(data, fh, indent=2)
            os.replace(f + ".tmp", f)
        except (OSError, ValueError):
            pass
    return n


def limit_process(pid, eco, cpu, jobs):
    """eco: low priority + Windows efficiency mode. cpu: hard cap in % of the whole CPU (0 = none).
    jobs keeps one job object per window so its cap can be changed later.
    -> (eco applied, cpu cap applied)"""
    if not IS_WIN:
        return False, False
    h = _k.OpenProcess(0x0200 | 0x0100 | 0x0001 | 0x1000, False, pid)  # SET_INFORMATION|SET_QUOTA|TERMINATE|QUERY_LIMITED
    if not h:
        return False, False
    try:
        ok_eco = bool(_k.SetPriorityClass(h, 0x4000 if eco else 0x20))   # BELOW_NORMAL / NORMAL

        class THROTTLE(ctypes.Structure):
            _fields_ = [("Version", wintypes.ULONG), ("ControlMask", wintypes.ULONG), ("StateMask", wintypes.ULONG)]
        t = THROTTLE(1, 1, 1 if eco else 0)                              # execution speed throttling = efficiency mode
        _k.SetProcessInformation(h, 4, ctypes.byref(t), ctypes.sizeof(t))
        mp = wintypes.ULONG(1 if eco else 5)                             # memory priority very low / normal
        _k.SetProcessInformation(h, 0, ctypes.byref(mp), ctypes.sizeof(mp))

        class RATE(ctypes.Structure):
            _fields_ = [("ControlFlags", wintypes.DWORD), ("CpuRate", wintypes.DWORD)]
        job = jobs.get(pid)
        if cpu <= 0 and not job:
            return ok_eco, True
        if not job:
            job = _k.CreateJobObjectW(None, None)
            if not job:
                return ok_eco, False
            if not _k.AssignProcessToJobObject(job, h):
                _k.CloseHandle(job)
                return ok_eco, False
            jobs[pid] = job
        r = RATE(0x1 | 0x4, max(1, min(100, cpu)) * 100) if cpu > 0 else RATE(0, 0)   # ENABLE | HARD_CAP
        return ok_eco, bool(_k.SetInformationJobObject(job, 15, ctypes.byref(r), ctypes.sizeof(r)))
    finally:
        _k.CloseHandle(h)


def close_job(job):
    if IS_WIN and job:
        _k.CloseHandle(job)


def ram_mb(pid):
    try:
        mi = psutil.Process(pid).memory_info()
        return int(getattr(mi, "private", mi.rss) / 1048576)
    except (psutil.Error, OSError):
        return 0


def trim_ram(pid):
    """ask Windows to move the window's unused memory out of RAM (what RAM cleaners do)"""
    if not IS_WIN:
        return False
    h = _k.OpenProcess(0x0100 | 0x0400, False, pid)     # PROCESS_SET_QUOTA | PROCESS_QUERY_INFORMATION
    if not h:
        return False
    try:
        return bool(_k.K32EmptyWorkingSet(h))
    finally:
        _k.CloseHandle(h)


def read_heartbeats(dirs):
    """newest heartbeat per UserId across all folders -> {uid: (time, state, msg, name)}.
    The time is when the file was last written by the PC's own clock (the executor's clock can be off)."""
    best = {}
    for d in dirs:
        try:
            files = list(os.scandir(d))
        except OSError:
            continue
        for e in files:
            f = e.name
            if not f.endswith(".txt") or not f[:-4].isdigit():
                continue
            try:
                ts = e.stat().st_mtime
                with open(e.path, encoding="utf-8", errors="ignore") as fh:
                    parts = fh.read().strip().split("|")
                int(parts[0])
            except (OSError, ValueError, IndexError):
                continue          # half written right now: the watcher keeps the last good one
            uid = f[:-4]
            if uid not in best or ts > best[uid][0]:
                best[uid] = (ts, parts[1] if len(parts) > 1 else "", parts[2] if len(parts) > 2 else "",
                             parts[3] if len(parts) > 3 else "")
    return best


# ------------------------------------------------------------------ Roblox's own log files
LOG_DIR = os.path.join(os.environ.get("LOCALAPPDATA", ""), "Roblox", "logs")
# disconnect codes -> plain words. HOLD = don't rejoin (it would kick the other device / the player left)
REASONS = {"264": "same account launched somewhere else", "273": "same account launched somewhere else",
           "267": "kicked", "277": "connection lost", "278": "idle kick (20 min)", "279": "couldn't connect",
           "285": "left the game", "288": "server shut down", "266": "connection lost", "282": "disconnected"}
HOLD = {"264", "273", "285"}
_LOG_TIME = re.compile(r"^(\d{4})-(\d\d)-(\d\d)T(\d\d):(\d\d):(\d\d)")
_LOG_UID = re.compile(r"userid:(\d+)", re.I)
_LOG_CODE = re.compile(r"(?:disconnect|reason|error code)\D{0,20}\b(2[6-9]\d)\b", re.I)


class RobloxLogs:
    """Reads only the new lines of Roblox's player logs. Keeps nothing but (account, event, time, code):
    lines with tickets, cookies or server codes are never logged or stored."""

    def __init__(self, folder=LOG_DIR):
        self.folder = folder
        self.files = {}        # path -> {"pos", "uid", "made"}
        self.events = {}       # path -> (kind, time, code)   kind: join / teleport / disconnect

    def scan(self, now=None):
        now = now or time.time()
        try:
            found = [e for e in os.scandir(self.folder) if "_Player_" in e.name and e.name.endswith(".log")]
        except OSError:
            return
        live = set()
        for e in found:
            try:
                st = e.stat()
            except OSError:
                continue
            if now - st.st_mtime > 1800:
                continue
            live.add(e.path)
            info = self.files.setdefault(e.path, {"pos": 0, "uid": None, "made": st.st_ctime})
            if st.st_size <= info["pos"]:
                continue
            try:
                with open(e.path, "rb") as fh:
                    fh.seek(info["pos"])
                    chunk = fh.read(4 << 20)
            except OSError:
                continue
            end = chunk.rfind(b"\n") + 1      # only whole lines
            info["pos"] += end
            for line in chunk[:end].decode("utf-8", "ignore").splitlines():
                self._line(e.path, info, line)
        for p in list(self.files):
            if p not in live:
                self.files.pop(p)
                self.events.pop(p, None)

    def _line(self, path, info, line):
        m = _LOG_TIME.match(line)
        if not m:
            return
        t = calendar.timegm(tuple(int(x) for x in m.groups()) + (0, 0, 0))
        low = line.lower()
        if info["uid"] is None:
            u = _LOG_UID.search(line)
            if u:
                info["uid"] = u.group(1)
        if "initiateteleport" in low or "! joining game" in low:
            self.events[path] = ("teleport", t, "")
        elif "serverid:" in low or "game_join_loadtime" in low:
            self.events[path] = ("join", t, "")
        else:
            c = _LOG_CODE.search(line)
            if c and c.group(1) in REASONS:
                self.events[path] = ("disconnect", t, c.group(1))
            elif "connection lost" in low or "lost connection" in low:
                self.events[path] = ("disconnect", t, "277")
            elif "server shutdown" in low or "server has shut down" in low:
                self.events[path] = ("disconnect", t, "288")

    def latest(self):
        """-> [(uid or None, file made time, kind, time, code)]"""
        return [(self.files[p]["uid"], self.files[p]["made"], *ev) for p, ev in self.events.items() if p in self.files]


# ------------------------------------------------------------------ Roblox windows (processes)
def roblox_processes():
    out = []
    for p in psutil.process_iter(["pid", "name", "cmdline", "create_time"]):
        try:
            if (p.info["name"] or "").lower() == ROBLOX_EXE:
                out.append((p.info["pid"], " ".join(p.info["cmdline"] or []), p.info["create_time"] or 0))
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    return out


def roblox_updating():
    """Roblox's installer/updater has been running for over 30 s (it downloads a new version before a window
    opens; a normal launch passes through the launcher in a few seconds)"""
    now = time.time()
    for p in psutil.process_iter(["name", "create_time"]):
        if (p.info["name"] or "").lower() in ("robloxplayerinstaller.exe", "robloxplayerlauncher.exe") \
                and now - (p.info["create_time"] or now) > 30:
            return True
    return False


def windows_of(pids, visible=True):
    """top-level windows of these processes -> [(hwnd, pid)]"""
    if not IS_WIN:
        return []
    pids = set(pids)
    found = []

    def each(hwnd, _):
        owner = wintypes.DWORD()
        _u.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        if owner.value in pids and bool(_u.IsWindowVisible(hwnd)) == visible and not _u.GetWindow(hwnd, 4):  # 4 = owner
            found.append((hwnd, owner.value))
        return True
    _u.EnumWindows(WNDENUMPROC(each), 0)
    return found


SIZES = {"small": (640, 400), "micro": (320, 200)}
_RESIZE = 0x2 | 0x4 | 0x10 | 0x400   # SWP_NOMOVE | NOZORDER | NOACTIVATE | NOSENDCHANGING (gets past Roblox's minimum size)


def tame_window(pid, minimize=False, size=None):
    """for the first minute of a new Roblox window: resize and/or minimize every window it shows,
    never activating it (Roblox shows its real window a few seconds after the splash)"""
    if not IS_WIN or not (minimize or size):
        return

    def run():
        done = set()
        for _ in range(120):
            for h, _p in windows_of([pid]):
                if h in done:
                    continue
                done.add(h)
                if size:
                    _u.SetWindowPos(h, None, 0, 0, size[0], size[1], _RESIZE)
                if minimize:
                    _u.ShowWindow(h, 7)      # SW_SHOWMINNOACTIVE
            time.sleep(0.5)
    threading.Thread(target=run, daemon=True).start()


def fit_window(pid, size):
    """put a window Roblox grew back (e.g. after a teleport) to its size again; minimized ones are left alone"""
    if not IS_WIN:
        return
    for h, _p in windows_of([pid]):
        if _u.IsIconic(h):
            continue
        r = wintypes.RECT()
        if _u.GetWindowRect(h, ctypes.byref(r)) and (abs(r.right - r.left - size[0]) > 8 or abs(r.bottom - r.top - size[1]) > 8):
            _u.SetWindowPos(h, None, 0, 0, size[0], size[1], _RESIZE)


def is_minimized(pid):
    return IS_WIN and any(_u.IsIconic(h) for h, _p in windows_of([pid]))


def show_window(pid, hidden, front=True):
    """the card's Show (front=True: restore + bring to the front) / Restore (front=False: restore without focus).
    Only ever called from a button click, so Roblox never jumps to the front by itself."""
    if not IS_WIN:
        return
    for h in list(hidden):              # hidden with the hotkey: unhide this one
        owner = wintypes.DWORD()
        _u.GetWindowThreadProcessId(h, ctypes.byref(owner))
        if owner.value == pid:
            hidden.remove(h)
            _u.ShowWindow(h, 4)
    for h, _p in windows_of([pid]):
        _u.ShowWindow(h, 9 if front else 4)          # SW_RESTORE / SW_SHOWNOACTIVATE
        if front:
            _u.SetForegroundWindow(h)


def minimize_window(pid):
    if IS_WIN:
        for h, _p in windows_of([pid]):
            _u.ShowWindow(h, 7)                      # SW_SHOWMINNOACTIVE


_cpu_procs = {}


def cpu_pct(pid):
    """this window's share of the whole PC's CPU, in % (the first reading of a new window is 0)"""
    try:
        p = _cpu_procs.get(pid) or _cpu_procs.setdefault(pid, psutil.Process(pid))
        return round(p.cpu_percent(None) / (psutil.cpu_count() or 1), 1)
    except (psutil.Error, OSError):
        return 0.0


def forget_cpu(live):
    for pid in [p for p in _cpu_procs if p not in live]:
        del _cpu_procs[pid]


def set_hidden(pids, hide, hidden):
    """hide every visible window of these processes (remembered in hidden), or show the remembered ones
    again without focusing them -> how many"""
    if not IS_WIN:
        return 0
    if hide:
        wins = [h for h, _p in windows_of(pids)]
        hidden.extend(wins)
    else:
        wins = list(hidden)
        hidden.clear()
    for h in wins:
        _u.ShowWindow(h, 0 if hide else 4)       # SW_HIDE / SW_SHOWNOACTIVATE
    return len(wins)


def kill(pid):
    try:
        p = psutil.Process(pid)
        p.kill()
        p.wait(5)          # gone before the next launch counts windows
    except psutil.Error:
        pass


def open_uri(uri):
    if IS_WIN:
        os.startfile(uri)
    else:
        raise RobloxError("Launching Roblox only works on Windows")




# ------------------------------------------------------------------ the watcher
def _ago(sec):
    sec = int(sec)
    return f"{sec // 3600}h {sec % 3600 // 60}m" if sec >= 3600 else f"{sec // 60}m"


class Watcher:
    def __init__(self, store, log):
        self.store = store
        self.log = log
        self.alive = False
        self.gen = 0          # each Start gets a number, so threads of an earlier Start stop by themselves
        self.lock = threading.RLock()   # pids/last/queue are used by the watcher, launch and button threads
        self.pids = {}        # uid -> pid of its Roblox window
        self.last = {}        # uid -> time of last launch
        self.err_since = {}
        self.err_msg = {}     # uid -> the error text the timer started with
        self.err_first = {}   # uid -> when this error spell began (hop errors can't push it back)
        self.unknown = set()  # uids whose window can't be told apart from other windows (warned once)
        self.restored = False
        self.saved = None
        self.gone_since = {}
        self.fails = {}
        self.restarts = {}
        self.reason = {}      # uid -> why it was last restarted
        self.status = {}      # uid -> text for the UI
        self.next_launch = 0.0
        self.queue = []       # uids waiting for their launch slot
        self.beats = {}       # uid -> last heartbeat that could be read
        self.hold = {}        # uid -> why it's left alone (same account elsewhere / left on purpose)
        self.hidden = []      # window handles hidden with the hotkey
        self.tamed = set()    # pids already shrunk / minimized
        self.cpu_warned = set()
        self.multi_ok = True
        self.updating = False
        self.update_since = 0.0
        self.logs = RobloxLogs()
        # hooks (swapped out in tests)
        self.procs = roblox_processes
        self.kill = kill
        self.open = open_uri
        self.now = time.time
        self.ticket = auth_ticket
        self.resolve = resolve_share
        self.mem = ram_mb
        self.limit = limit_process
        self.is_updating = roblox_updating
        self.cpu_of = cpu_pct
        self.win_min = is_minimized
        self.fit = fit_window
        self.cpu = {}         # uid -> % of the whole CPU, for the cards
        self.minimized = {}   # uid -> its window is minimized
        self.limited = {}     # pid -> (eco, cpu) already set on that window
        self.jobs = {}        # pid -> job object handle (cpu cap)
        self.ram = {}         # uid -> MB, for the UI
        self.ram_hits = {}    # uid -> ram limit restarts in a row

    # -------------------------------------------------- control
    def start(self):
        with self.lock:
            if self.alive:
                return
            self.alive = True
            self.gen += 1
            g = self.gen
        self.multi_ok = allow_multi()
        if not self.multi_ok:
            self.log("Roblox was already open when watching started, so only one Roblox window can run. "
                     "Close every Roblox window, then press Stop and Start.")
        if not roblox_installed():
            self.log("Roblox doesn't look installed (no roblox-player link). Install it from roblox.com "
                     "(or set up Bloxstrap / Fishstrap), then press Start again.")
        for loop in (self._run, self._report_loop, self._ram_loop):
            threading.Thread(target=loop, args=(g,), daemon=True).start()

    def _on(self, g):
        return self.alive and self.gen == g

    def stop(self):
        with self.lock:
            self.alive = False
            self.queue.clear()
        self.log("Watcher stopped")
        self.notify("startstop", "Watcher stopped")

    def _run(self, g):
        self.log("Watcher started")
        self.notify("startstop", f"Watcher started ({len(self.store.accounts)} accounts)")
        if not self.store.settings["hb_dirs"]:
            self.rescan()
        self.apply_fps()
        while self._on(g):
            try:
                self.tick()
            except Exception as e:      # never let one bad loop kill the watcher
                self.log(f"Problem in watcher loop: {e}")
            for _ in range(CHECK * 10):
                if not self._on(g):
                    break
                time.sleep(0.1)

    def rescan(self):
        hb, ws, ax = find_heartbeat_dirs()
        added = install_heartbeat(ax)
        for d in added:
            self.log(f"Heartbeat is in {d}")
        self.store.settings["ax_dirs"] = list(ax)
        known = self.store.settings["hb_dirs"]
        for d in hb:
            if d not in known:
                known.append(d)
                self.log(f"Watching {d}")
        if not known:
            self.log("No NovaHop/hb folder found yet. Join the game once with the executor attached, "
                     "or pick the executor's workspace folder in Folders.")
        self.store.save()
        return hb, ws, ax

    _fps_lock = threading.Lock()

    def apply_fps(self, quiet=False):
        s = self.store.settings
        cap = int(s.get("fps_cap", 0))
        try:
            with self._fps_lock:
                n = apply_fps(cap, s.get("ax_dirs", []), bool(s.get("low_gfx")), s.setdefault("flag_backup", {}))
            self.store.save()
        except Exception as e:
            self.log(f"couldn't write the fps / graphics settings: {e}")
            return
        if cap > 0 and not quiet:
            if n:
                self.log(f"fps cap {cap} set in {n} executor autoexec folder(s): capped in game, full speed while "
                         "hopping. windows opened from now on use it")
            else:
                self.log(f"fps cap {cap} set in roblox's own settings (this also slows hops down a bit). press Scan "
                         "in Folders so the executor caps it instead")

    def _ram_loop(self, g):
        waited = 0
        while self._on(g):
            time.sleep(1)
            waited += 1
            try:
                mins = int(self.store.settings.get("ram_trim", 0))
                if mins <= 0:
                    waited = 0
                    continue
                if waited < mins * 60:
                    continue
                waited = 0
                with self.lock:     # only accounts in game: trimming one that's loading / hopping slows it down
                    pids = {pid for uid, pid in self.pids.items() if self.status.get(uid) == "OK"}
                n = sum(1 for pid in pids if trim_ram(pid))
                if n:
                    self.log(f"trimmed ram of {n} roblox window(s)")
            except Exception as e:
                self.log(f"ram trim failed: {e}")

    # -------------------------------------------------- which window is whose
    def _adopt(self, procs):
        """Give every account its window: our tracker id in the window's command line first, then new
        windows to the accounts waiting for one, oldest launch first (Roblox can hand over to a second
        process while starting, and its anti-cheat can hide the command line)."""
        with self.lock:
            live = {p[0] for p in procs}
            for uid, pid in list(self.pids.items()):
                if pid not in live:
                    del self.pids[uid]
            accs = [a for a in self.store.accounts if a.get("tracker")]
            tracked = {}
            for p in procs:
                for a in accs:
                    if a["tracker"] in p[1]:
                        tracked[p[0]] = a["uid"]
                        if a["uid"] not in self.pids and p[0] not in self.pids.values():
                            self.pids[a["uid"]] = p[0]
                        break
            claimed = set(self.pids.values())
            free = sorted((p for p in procs if p[0] not in claimed and p[0] not in tracked), key=lambda p: p[2])
            waiting = sorted((a["uid"] for a in self.store.accounts
                              if a["uid"] not in self.pids and self.last.get(a["uid"])), key=lambda u: self.last[u])
            for uid in waiting:
                last = self.last[uid]
                for p in free:
                    if last - 5 <= p[2] <= last + 180:
                        self.pids[uid] = p[0]
                        free.remove(p)
                        break

    def find_pid(self, a, procs):
        self._adopt(procs)
        with self.lock:
            return self.pids.get(a["uid"])

    def _cleanup(self, live):
        """forget windows that closed and accounts that were removed"""
        with self.lock:
            for pid in [p for p in self.jobs if p not in live]:
                close_job(self.jobs.pop(pid))
            for pid in [p for p in self.limited if p not in live]:
                del self.limited[pid]
            self.tamed &= live
            self.cpu_warned &= live
            forget_cpu(live)
            uids = {a["uid"] for a in self.store.accounts}
            for d in (self.ram, self.cpu, self.minimized, self.status, self.beats, self.last, self.fails, self.err_since, self.err_msg,
                      self.err_first, self.gone_since, self.hold, self.ram_hits, self.reason):
                for u in [u for u in d if u not in uids]:
                    del d[u]

    # which window is whose is kept in windows.json, so it survives the app window crashing / restarting
    def _restore(self, procs):
        self.restored = True
        d = _load("windows.json", {})
        live = {p[0]: p[2] for p in procs}
        uids = {a["uid"] for a in self.store.accounts}
        with self.lock:
            for u, v in (d.get("pids") or {}).items():
                try:
                    pid, made = int(v[0]), float(v[1])
                except (TypeError, ValueError, IndexError):
                    continue
                if u in uids and pid in live and abs(live[pid] - made) < 2 and pid not in self.pids.values():
                    self.pids.setdefault(u, pid)
                    if u in (d.get("last") or {}):
                        self.last.setdefault(u, d["last"][u])
            if not self.hidden:
                self.hidden.extend(d.get("hidden") or [])

    def _save_state(self, procs):
        made = {p[0]: p[2] for p in procs}
        with self.lock:
            snap = {"pids": {u: [p, made.get(p, 0)] for u, p in self.pids.items()},
                    "last": dict(self.last), "hidden": list(self.hidden)}
        if snap != self.saved:
            self.saved = snap
            try:
                _save("windows.json", snap)
            except OSError:
                pass

    def _cant_find(self, a, procs):
        """the account needs a new window, but every window slot is taken by windows this app can't tell apart
        (opened before the app, or by hand): closing a random one would hit another account"""
        if not self.too_many_windows(procs):
            return False
        uid = a["uid"]
        self.status[uid] = "can't tell which roblox window is this one: close the extra windows"
        if uid not in self.unknown:
            self.unknown.add(uid)
            self.log(f"{a.get('nick') or a['name']} needs a restart, but there are already as many roblox windows "
                     "as accounts and the app can't tell which one is its window. Close that window (or Close all) "
                     "and it comes back by itself.")
        return True

    def too_many_windows(self, procs):
        """never run more Roblox windows than there are enabled accounts (disabled accounts' windows don't count)"""
        with self.lock:
            off = {self.pids.get(a["uid"]) for a in self.store.accounts if not a.get("enabled", True)}
        return sum(1 for p in procs if p[0] not in off) >= sum(1 for a in self.store.accounts if a.get("enabled", True))

    def boot_wait(self):
        return max(60, int(self.store.settings.get("boot_wait", BOOT_WAIT)))

    def _tame(self, pid):
        s = self.store.settings
        size = SIZES.get(s.get("win_size"))
        if pid not in self.tamed and (s.get("launch_min") or size):
            self.tamed.add(pid)
            tame_window(pid, bool(s.get("launch_min")), size)

    # the card buttons (each runs in its own thread: window calls can take a moment)
    def show(self, uid, front=True):
        with self.lock:
            pid = self.pids.get(uid)
        if pid:
            threading.Thread(target=show_window, args=(pid, self.hidden, front), daemon=True).start()
            self.minimized[uid] = False

    def minimize(self, uid):
        with self.lock:
            pid = self.pids.get(uid)
        if pid:
            threading.Thread(target=minimize_window, args=(pid,), daemon=True).start()
            self.minimized[uid] = True

    def toggle_hidden(self):
        if self.hidden:
            n = set_hidden([], False, self.hidden)
            self.log(f"showing {n} roblox window(s) again")
        else:
            n = set_hidden([p[0] for p in self.procs()], True, self.hidden)
            self.log(f"hid {n} roblox window(s), press the hotkey again to show them")

    # -------------------------------------------------- launching
    def launch(self, a, why):
        uid = a["uid"]
        s = self.store.settings
        if a.get("relogin"):
            self.status[uid] = "log in again (add this account again)"
            return
        try:
            cookie = self.store.cookie(a)
            place, ps_raw = server_for(a, s)
            link = ""
            kind, code = parse_ps(ps_raw) or ("", "")
            if kind == "code":
                link = code
            elif kind == "share":
                p2, link = self.resolve(cookie, code)
                place = p2 or place
            uri = launch_uri(self.ticket(cookie), a["tracker"], place, link)
        except LoginExpired as e:
            self.status[uid] = "login expired, log in again"
            self.log(f"{a['name']}: {e}")
            self.notify("login", f"**{a['name']}**: login expired, log in again in the app")
            a["enabled"] = False
            self.store.save()
            return
        except Exception as e:
            self.status[uid] = "launch failed"
            self.log(f"{a['name']}: couldn't launch ({e})")
            self.last[uid] = self.now() - self.boot_wait() + 30   # try again in ~30 s
            return
        procs = self.procs()
        pid = self.find_pid(a, procs)
        if pid:            # one account never gets two windows
            if why != "launched by hand":
                self.status[uid] = "already open"
                self.log(f"{a['name']}: its roblox window is still open, not opening a second one")
                return
            self.kill(pid)
            with self.lock:
                self.pids.pop(uid, None)
            procs = self.procs()
        if why != "launched by hand" and self.too_many_windows(procs):
            self.status[uid] = "not opened: already as many roblox windows as accounts"
            self.log(f"{a['name']}: not opening another Roblox, there are already {len(procs)} open "
                     "(close the extra ones, or use Launch to force it)")
            self.last[uid] = self.now()
            self.err_since[uid] = 0
            return
        if s.get("fps_cap") or s.get("low_gfx"):
            self.apply_fps(quiet=True)      # a Roblox update puts it in a new folder without them
        self.hold.pop(uid, None)
        self.log(f"Launching {a['name']}: {why}")
        try:
            self.open(uri)
        except OSError as e:
            self.status[uid] = "roblox isn't installed?"
            self.log(f"{a['name']}: windows couldn't open roblox ({e}). Is Roblox installed?")
            self.last[uid] = self.now() - self.boot_wait() + 30
            return
        with self.lock:
            self.last[uid] = self.now()
            self.err_since[uid] = 0
            self.gone_since.pop(uid, None)
        self.status[uid] = "launching"
        threading.Thread(target=self._grab, args=(uid,), daemon=True).start()

    def _grab(self, uid):
        """find the new window quickly, so it can be shrunk / minimized before it gets in the way"""
        for _ in range(60):
            time.sleep(0.5)
            self._adopt(self.procs())
            with self.lock:
                pid = self.pids.get(uid)
            if pid:
                self._tame(pid)
                return

    def notify(self, kind, text):
        s = self.store.settings
        url = s.get("webhook", "")
        if not is_webhook(url) or not s.get("wh_" + kind, True):
            return
        ping = s.get("wh_ping", "").strip()
        if ping and kind in ("stuck", "login"):
            text = f"<@{ping}> " + text

        def go():
            try:
                send_webhook(url, text)
            except Exception as e:
                self.log(f"Discord webhook failed: {e}")
        threading.Thread(target=go, daemon=True).start()

    # -------------------------------------------------- live Discord status message
    def _report_loop(self, g):
        for _ in range(200):          # first update ~20 s after start
            if not self._on(g):
                return
            time.sleep(0.1)
        while self._on(g):
            s = self.store.settings
            if is_webhook(s.get("webhook", "")) and s.get("wh_status", True):
                try:
                    self.post_status()
                except Exception as e:
                    self.log(f"Discord status failed: {e}")
            for _ in range(max(30, int(s.get("status_every", 60))) * 10):
                if not self._on(g):
                    return
                time.sleep(0.1)

    def status_embed(self):
        lines, online = [], 0
        now = self.now()
        for a in self.store.accounts:
            uid = a["uid"]
            st = self.status.get(uid, "not started") if a.get("enabled", True) else "off"
            if st == "OK":
                dot, word = "\U0001F7E2", "Online"; online += 1
            elif st.startswith(("loading", "starting", "launching", "waiting", "window closed", "teleporting")):
                dot, word = "\U0001F7E1", st[:1].upper() + st[1:]
            elif st in ("off", "not started"):
                dot, word = "⚫", st[:1].upper() + st[1:]
            else:
                dot, word = "\U0001F534", st[:1].upper() + st[1:]
            mb = self.ram.get(uid)
            ram = f"{mb} MB" if mb else "–"
            up = f" · Up {_ago(now - self.last[uid])}" if st == "OK" and self.last.get(uid) else ""
            why = f"\nLast restart: {self.reason[uid]}" if self.reason.get(uid) else ""
            name = a.get("nick") or a["name"]
            game = game_name(server_for(a, self.store.settings)[0])
            lines.append(f"{dot} **{name}** — {word}\nGame: {game} · Rejoins: {self.restarts.get(uid, 0)}"
                         f" · RAM: {ram}{up}{why}")
        vm = psutil.virtual_memory()
        cpu = psutil.cpu_percent(interval=1)
        total = sum(v for v in self.ram.values() if v)
        return {
            "title": f"NovaHop Rejoin · {online}/{len(self.store.accounts)} online",
            "description": ("\n\n".join(lines) or "No accounts yet.")[:3900],
            "color": 0x7C3AED,
            "fields": [{"name": "PC", "value": f"CPU {cpu:.0f}% · RAM {vm.used / 1024**3:.1f} / {vm.total / 1024**3:.1f} GB",
                        "inline": False},
                       {"name": "Roblox", "value": f"{len(self.ram)} window(s) · {total / 1024:.1f} GB RAM in total",
                        "inline": False}],
            "footer": {"text": f"Updates every {max(30, int(self.store.settings.get('status_every', 60)))}s"},
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }

    def post_status(self):
        s = self.store.settings
        url = s["webhook"]
        embed = self.status_embed()
        new = send_status(url, embed)
        old = s.get("status_msg", "")
        if old and old != new:
            try:
                delete_message(url, old)
            except Exception:
                pass
        s["status_msg"] = new
        self.store.save()

    def restart(self, a, pid, why):
        uid = a["uid"]
        if pid:
            self.kill(pid)
        with self.lock:
            self.pids.pop(uid, None)
        self.restarts[uid] = self.restarts.get(uid, 0) + 1
        self.reason[uid] = why
        self.log(f"{a['name']}: restarting - {why}")
        game = game_name(server_for(a, self.store.settings)[0])
        self.notify("restart", f"Restarting **{a.get('nick') or a['name']}** ({game}): {why} "
                               f"(restart #{self.restarts[uid]})")
        self.want_launch(a, why)

    def want_launch(self, a, why):
        with self.lock:
            if a["uid"] not in [q[0] for q in self.queue]:
                self.queue.append((a["uid"], why))
                self.status[a["uid"]] = "waiting to launch"

    def launch_now(self, uid):
        """the Launch button on one account: queued while watching, straight away otherwise"""
        a = self.store.get(uid)
        if not a:
            return

        def go():
            self.hold.pop(uid, None)
            pid = self.find_pid(a, self.procs())
            if self.alive:
                self.restart(a, pid, "launched by hand")
            else:
                self.launch(a, "launched by hand")
        threading.Thread(target=go, daemon=True).start()

    def launch_all(self):
        """open every enabled account that isn't already open, one at a time"""
        def go():
            todo = [a for a in self.store.accounts if a.get("enabled", True)]
            for i, a in enumerate(todo):
                if self.find_pid(a, self.procs()):
                    continue
                if self.alive:
                    self.want_launch(a, "launch all")
                    continue
                self.launch(a, "launch all")
                if i < len(todo) - 1:
                    time.sleep(max(5, int(self.store.settings.get("launch_delay", 12))))
            self.log("launch all: done" if not self.alive else "launch all: queued")
        threading.Thread(target=go, daemon=True).start()

    def close(self, uid):
        def go():
            a = self.store.get(uid)
            pid = self.find_pid(a, self.procs()) if a else None
            if pid:
                self.kill(pid)
                with self.lock:
                    self.pids.pop(uid, None)
                self.log(f"closed {a['name']}")
            elif a:
                self.log(f"{a['name']} isn't open (or wasn't launched by this app)")
        threading.Thread(target=go, daemon=True).start()

    def close_all(self):
        def go():
            procs = self.procs()
            for a in self.store.accounts:
                pid = self.find_pid(a, procs)
                if pid:
                    self.kill(pid)
                    with self.lock:
                        self.pids.pop(a["uid"], None)
            self.log("closed all accounts launched by this app")
        threading.Thread(target=go, daemon=True).start()

    # -------------------------------------------------- one pass over every account
    def _log_events(self):
        """newest event per account from Roblox's own logs -> {uid: (kind, time, code)}"""
        try:
            self.logs.scan()
        except Exception:
            return {}
        out = {}
        for uid, made, kind, t, code in self.logs.latest():
            if uid is None:     # not in a game yet: the account launched closest to when the log started
                near = sorted((abs(l - made), u) for u, l in self.last.items() if abs(l - made) < 60)
                if not near:
                    continue
                uid = near[0][1]
            if uid not in out or t > out[uid][1]:
                out[uid] = (kind, t, code)
        return out

    def tick(self):
        now = self.now()
        s = self.store.settings
        procs = self.procs()
        if not self.restored:
            self._restore(procs)
        self._adopt(procs)
        self._cleanup({p[0] for p in procs})
        err_wait = int(s.get("error_wait", ERROR_WAIT))
        stale = int(s.get("stale", STALE))
        hop = int(s.get("hop_wait", HOP_WAIT))
        for uid, b in read_heartbeats(s["hb_dirs"]).items():
            if uid not in self.beats or b[0] >= self.beats[uid][0]:
                self.beats[uid] = b      # a file being rewritten right now keeps its last good read
        events = self._log_events() if s.get("read_logs") else {}

        upd = self.is_updating()
        if upd != self.updating:
            self.updating = upd
            if upd:
                self.update_since = now
                self.log("Roblox is downloading an update, launches wait until it's done")
            else:
                self.log("Roblox update finished")
                for u, l in list(self.last.items()):     # windows opened during the update get a fresh loading time
                    if l >= self.update_since - self.boot_wait():
                        self.last[u] = now

        with self.lock:
            queued = {q[0] for q in self.queue}
        for a in list(self.store.accounts):
            uid = a["uid"]
            name = a.get("nick") or a["name"]
            if not a.get("enabled", True):
                self.status[uid] = "off"
                continue
            if a.get("relogin"):
                self.status[uid] = "log in again (add this account again)"
                continue
            if uid in queued:
                if upd:
                    self.status[uid] = "waiting: roblox is updating"
                continue
            with self.lock:
                pid = self.pids.get(uid)
            if pid:
                self.unknown.discard(uid)
                self.ram[uid] = self.mem(pid)
                self.cpu[uid] = self.cpu_of(pid)
                self.minimized[uid] = self.win_min(pid)
                if self.last.get(uid) and now - self.last[uid] < 180:
                    self._tame(pid)
                want = (bool(s.get("eco")), int(s.get("cpu_cap", 0)))
                if self.limited.get(pid) != want and (want != (False, 0) or pid in self.limited):
                    ok_eco, ok_cpu = self.limit(pid, want[0], want[1], self.jobs)
                    self.limited[pid] = want         # tried: don't hammer a window that refuses
                    if want[1] and not ok_cpu and pid not in self.cpu_warned:
                        self.cpu_warned.add(pid)
                        self.log(f"{name}: windows or roblox refused the cpu limit on this window")
            else:
                self.ram.pop(uid, None)
                self.cpu.pop(uid, None)
                self.minimized.pop(uid, None)
            hb = self.beats.get(uid)
            age = now - hb[0] if hb else None
            last = self.last.get(uid)
            # a fresh heartbeat written after this launch = it's in game, no need to sit out the whole loading time
            loaded = hb is not None and last is not None and hb[0] > last and age < 20 and hb[1] != "error"
            booting = last is not None and now - last < self.boot_wait() and not loaded
            f = self.fails.get(uid, 0)
            ev = events.get(uid)
            if ev and last and ev[1] < last - 5:
                ev = None                            # from before its current window was opened

            # same account launched somewhere else / player left on purpose: rejoining would fight it
            if uid in self.hold:
                if ev and ev[0] == "join":
                    self.hold.pop(uid)
                else:
                    self.status[uid] = f"{self.hold[uid]}, not rejoining (press Launch)"
                    continue
            if ev and ev[0] == "disconnect" and ev[2] in HOLD:
                self.hold[uid] = REASONS[ev[2]]
                self.log(f"{name}: {REASONS[ev[2]]}, leaving it alone until you press Launch")
                self.status[uid] = f"{self.hold[uid]}, not rejoining (press Launch)"
                continue
            hopping = ev is not None and ev[0] == "teleport" and now - ev[1] < hop

            # a fresh heartbeat = the account is in the game, even if we lost track of its window
            fresh = hb is not None and age < 30
            if pid is None and not fresh:
                if upd:
                    self.status[uid] = "waiting: roblox is updating"
                    continue
                if booting and now - last < 90:
                    self.status[uid] = f"starting ({int(now - last)}s)"
                    continue
                if last is None:
                    self.want_launch(a, "not running")
                    continue
                gone = self.gone_since.setdefault(uid, now)
                if now - gone < GONE_WAIT:
                    self.status[uid] = "window closed"
                    continue
                if f >= 3 and now - last < RETRY:
                    self.status[uid] = "not coming back, retrying every 15 min"
                    continue
                if self._cant_find(a, procs):
                    continue
                if f == 2:
                    self.notify("stuck", f"**{name}** keeps closing after 3 relaunches. Check that PC.")
                self.fails[uid] = f + 1
                self.restart(a, None, "window closed")
                continue
            self.gone_since.pop(uid, None)

            if booting:
                self.status[uid] = f"loading ({int(now - last)}s)"
                if hb and age < 20 and hb[1] != "error":
                    self.fails[uid] = 0
                continue

            # an error screen from the heartbeat, or a disconnect in Roblox's own log
            err, from_log = None, False
            if hb and age < stale and hb[1] == "error":
                err = hb[2] or "error"
            elif ev and ev[0] == "disconnect" and not (hb and age < 30 and hb[1] != "error"):
                err, from_log = REASONS.get(ev[2], "disconnected"), True
            if err is not None:
                self.status[uid] = "error: " + err
                # NovaHop moving on to the next hop error = it's still working: the timer starts again
                if not self.err_since.get(uid) or (err != self.err_msg.get(uid) and HOPPING.search(err)):
                    if not self.err_since.get(uid):
                        self.log(f"{name}: error on screen: {err}")
                        self.err_first[uid] = now
                    self.err_since[uid] = now
                    self.err_msg[uid] = err
                # a teleport / full-server error is NovaHop mid-hop and it retries by itself: give it longer,
                # but never more than 5x that in one go (two hop errors taking turns forever = stuck)
                wait = hop if HOPPING.search(err) else err_wait
                if now - self.err_since[uid] >= wait or now - self.err_first.get(uid, now) >= 5 * hop:
                    if pid is None and self._cant_find(a, procs):
                        continue
                    self.restart(a, pid, err if from_log else f"error screen ({err})")
                continue
            if self.err_since.get(uid):
                self.log(f"{name}: error cleared")
            self.err_since[uid] = 0
            self.err_msg.pop(uid, None)
            self.err_first.pop(uid, None)

            if hb is None or age >= stale:
                if hopping:
                    self.status[uid] = "teleporting"
                    continue
                if f >= 3 and last and now - last < RETRY:
                    self.status[uid] = "not coming back, retrying every 15 min"
                    continue
                if pid is None and self._cant_find(a, procs):
                    continue
                if f == 2:
                    self.notify("stuck", f"**{name}** didn't come back after 3 restarts. Check that PC.")
                    self.log(f"{name} didn't come back after 3 restarts. Is the heartbeat in the executor's "
                             "autoexec, and is the executor attaching? Retrying every 15 min.")
                self.fails[uid] = f + 1
                self.restart(a, pid, f"no heartbeat for {int(age)}s" if hb else "no heartbeat (is the executor attached?)")
            else:
                self.fails[uid] = 0
                self.status[uid] = "OK"
                size = SIZES.get(s.get("win_size"))
                if size and pid and not self.minimized.get(uid):
                    self.fit(pid, size)            # Roblox can grow a window back after a teleport
                limit = int(s.get("ram_limit", 0))
                if limit > 0 and pid:
                    mb = self.ram.get(uid) or self.mem(pid)
                    h = self.ram_hits.get(uid, 0)
                    if mb <= limit:
                        if last is None or now - last >= RETRY:
                            self.ram_hits.pop(uid, None)
                    elif h < 3 or last is None or now - last >= RETRY:
                        if h == 2:
                            self.notify("stuck", f"**{name}** keeps going over the ram limit ({limit} MB). Raise it in settings.")
                        self.ram_hits[uid] = h + 1
                        self.restart(a, pid, f"RAM limit: using {mb} MB (limit {limit})")
                    else:
                        self.status[uid] = "over the ram limit, retrying every 15 min"

        # launch one waiting account per slot, so Roblox isn't flooded (and none while Roblox updates)
        if not upd and now >= self.next_launch:
            with self.lock:
                item = self.queue.pop(0) if self.queue else None
            if item:
                a = self.store.get(item[0])
                if a and a.get("enabled", True):
                    self.launch(a, item[1])
                self.next_launch = now + max(5, int(s.get("launch_delay", 12)))
        self._save_state(self.procs())
