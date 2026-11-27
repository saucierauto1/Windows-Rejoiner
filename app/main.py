"""NovaHop Rejoin for Windows - the app window.

Run with no arguments for the app. "--login <file>" opens the Roblox login window (used by
"Add account"; it runs as its own process because the browser window needs the main thread).
"""

import json
import os
import subprocess
import sys
import tempfile
import threading
import time

import core
import mouselock


# ------------------------------------------------------------------ login window (child process)
def login_mode(out_file):
    import webview

    w = webview.create_window("Log in to Roblox - roblox.com", "https://www.roblox.com/login",
                              width=520, height=760)

    READ_USER = ("(function(){var m=document.querySelector('meta[name=\"user-data\"]');"
                 "return m?JSON.stringify({id:m.getAttribute('data-userid'),name:m.getAttribute('data-name'),"
                 "display:m.getAttribute('data-displayname')}):null})()")

    def wait_for_login():
        cookie, since = None, 0
        while True:
            time.sleep(1.0)
            try:
                for c in w.get_cookies() or []:
                    for key, morsel in c.items():
                        if key == ".ROBLOSECURITY" and morsel.value:
                            if morsel.value != cookie:
                                cookie, since = morsel.value, time.time()
                if not cookie:
                    continue
                url = w.get_current_url() or ""
                # wait until Roblox has finished logging in and shows a normal page, then read who it is
                if "/login" in url or "/challenge" in url:
                    continue
                user = None
                try:
                    raw = w.evaluate_js(READ_USER)
                    user = json.loads(raw) if raw else None
                except Exception:
                    user = None
                if not (user and user.get("id")) and time.time() - since < 15:
                    continue
                with open(out_file, "w", encoding="utf-8") as f:
                    json.dump({"cookie": core.protect(cookie), "user": user or {}}, f)   # encrypted on disk
                w.destroy()
                return
            except Exception:
                continue

    webview.start(wait_for_login, private_mode=True)   # private: every login starts logged out
    os._exit(0)


# ------------------------------------------------------------------ app window
_panic_cb = None


def _exit_on_tk_panic(fn):
    """Tk's fatal errors ("Tk_GetPixmap: Error from CreateDIBSection") show a box that waits for a click, which
    keeps the dead window's process alive. Run fn instead (log + exit), so the supervisor opens the window again."""
    global _panic_cb
    import ctypes
    import glob
    import re
    import _tkinter
    for d in {os.path.dirname(_tkinter.__file__), getattr(sys, "_MEIPASS", "")}:
        for f in glob.glob(os.path.join(d, "tcl*.dll")):
            if re.fullmatch(r"tcl\d+t?\.dll|tcl9tcl\d+\.dll", os.path.basename(f), re.I):
                try:
                    _panic_cb = ctypes.CFUNCTYPE(None, ctypes.c_char_p)(fn)
                    ctypes.CDLL(f).Tcl_SetPanicProc(_panic_cb)
                    return True
                except (OSError, AttributeError):
                    pass
    return False


def app_mode(resume=False):
    import tkinter as tk
    from tkinter import filedialog, messagebox

    FONT = ("Consolas", 10)
    DISCORD = "https://discord.gg/brfJ2ZtegE"

    store = core.Store()
    BG = store.settings.get("bg_color", "#000000")
    BTN = store.settings.get("btn_color", "#1A1A1A")
    DIM = "#888888"
    T = {"fg": store.settings.get("text_color", "#FFFFFF")}
    lines = []
    lock = threading.Lock()
    count = [0]
    LOG_DIR = os.path.join(core.DATA_DIR, "logs")
    os.makedirs(LOG_DIR, exist_ok=True)
    for f in os.listdir(LOG_DIR):          # keep the last few days
        p = os.path.join(LOG_DIR, f)
        try:
            if time.time() - os.path.getmtime(p) > 4 * 86400:
                os.remove(p)
        except OSError:
            pass

    def log(msg):
        with lock:
            count[0] += 1
            lines.append((count[0], time.strftime("[%H:%M:%S] ") + msg))
            del lines[:-300]
            try:
                with open(os.path.join(LOG_DIR, time.strftime("rejoin-%Y-%m-%d.txt")), "a", encoding="utf-8") as f:
                    f.write(time.strftime("%H:%M:%S ") + msg + "\n")
            except OSError:
                pass

    threading.excepthook = lambda a: log(f"problem in a background task: {a.exc_value!r}")
    watcher = core.Watcher(store, log)
    mlock = mouselock.MouseLock(log)
    hider = mouselock.Hotkey(log, "hide roblox", watcher.toggle_hidden)
    show_flag = os.path.join(core.DATA_DIR, "show.flag")
    running_flag = os.path.join(core.DATA_DIR, "watching.flag")

    root = tk.Tk()
    root.title("NovaHop Rejoin")
    root.geometry("960x720")
    root.configure(bg=BG)
    root.report_callback_exception = lambda kind, val, tb: log(f"screen problem (watching goes on): {val!r}")

    def on_panic(msg):
        try:
            with open(os.path.join(LOG_DIR, time.strftime("rejoin-%Y-%m-%d.txt")), "a", encoding="utf-8") as f:
                f.write(time.strftime("%H:%M:%S ") + "the app window crashed ("
                        + (msg or b"").decode("utf-8", "ignore") + "), opening it again\n")
        finally:
            os._exit(3)
    try:
        if not _exit_on_tk_panic(on_panic):
            log("note: couldn't set up the crash catcher, a screen crash may show an error box")
    except Exception as e:
        log(f"note: couldn't set up the crash catcher ({e})")
    try:
        root.attributes("-alpha", max(0.4, min(1.0, float(store.settings.get("alpha", 1.0)))))
    except Exception:
        pass

    # background picture (drawn on the main canvas below)
    bg_src = None
    bg_path = store.settings.get("bg_image", "")
    if bg_path and not os.path.exists(bg_path):
        log(f"background picture is missing ({bg_path}), pick it again in Look")
    elif bg_path:
        try:
            from PIL import Image
            bg_src = Image.open(bg_path).convert("RGB")
            bg_src.thumbnail((root.winfo_screenwidth(), root.winfo_screenheight()))   # never bigger than the screen
        except Exception as e:
            log(f"background picture failed: {e}")
    PIC = bg_src is not None

    root.attributes("-topmost", bool(store.settings.get("on_top")))
    try:
        root.iconbitmap(default=os.path.join(getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__))), "icon.ico"))
    except Exception:
        pass

    def btn(parent, text, cmd, width=None):
        b = tk.Button(parent, text=text, command=cmd, bg=BTN, fg=T["fg"], activebackground=DIM,
                      activeforeground=T["fg"], relief="solid", bd=1, font=FONT, padx=8, pady=3)
        if width:
            b.configure(width=width)
        return b

    def lbl(parent, text="", fg=None, **kw):
        return tk.Label(parent, text=text, bg=BG, fg=fg or T["fg"], font=FONT, anchor="w", justify="left", **kw)

    # ---------------- the main screen is one canvas: text is written straight onto the background
    import tkinter.font as tkfont
    cv = tk.Canvas(root, bg=BG, highlightthickness=0, bd=0)
    cv.place(x=0, y=0, relwidth=1, relheight=1)
    FNT = tkfont.Font(family="Consolas", size=10)
    LH = FNT.metrics("linespace") + 2
    CW = FNT.measure("0")
    bg_item = cv.create_image(0, 0, anchor="nw")
    bar_btns = []

    def text(x, y, s, fill, tag):
        if PIC:   # dark shadow so text stays readable on any picture
            cv.create_text(x + 1, y + 1, text=s, anchor="nw", font=FNT, fill="#000000", tags=(tag, "txt"))
        cv.create_text(x, y, text=s, anchor="nw", font=FNT, fill=fill, tags=(tag, tag + "_fg", "txt"))

    drawn = {}

    def set_text(tag, s, fill=None):
        if drawn.get(tag) == (s, fill):     # unchanged: don't make windows redraw the picture
            return
        drawn[tag] = (s, fill)
        cv.itemconfigure(tag, text=s)
        if fill:
            cv.itemconfigure(tag + "_fg", fill=fill)

    def toggle():
        if watcher.alive:
            watcher.stop()
            try:
                os.remove(running_flag)
            except OSError:
                pass
        else:
            if not store.accounts:
                messagebox.showinfo("NovaHop Rejoin", "add an account first")
                return
            watcher.start()
            open(running_flag, "w").close()    # after a crash the app starts watching again by itself
        refresh()

    start_btn = btn(cv, "Start", toggle, 8)
    bar_btns.append(start_btn)

    # ---------------- add account
    def add_account():
        add_btn.configure(state="disabled", text="log in in the new window...")
        place_bar()
        out = os.path.join(tempfile.gettempdir(), f"nhr_login_{os.getpid()}_{int(time.time())}.tmp")
        if getattr(sys, "frozen", False):
            cmd = [sys.executable, "--login", out]
        else:
            cmd = [sys.executable, os.path.abspath(__file__), "--login", out]

        def run():
            try:
                subprocess.run(cmd)
                if not os.path.exists(out):
                    log("login window closed without logging in")
                    return
                with open(out, encoding="utf-8") as f:
                    got = json.load(f)
                os.remove(out)
                cookie = core.unprotect(got["cookie"])
                page = got.get("user") or {}
                try:
                    uid, name, display = core.whoami(cookie)
                except core.RobloxError as e:
                    if not page.get("id"):
                        raise
                    uid, name, display = str(page["id"]), page.get("name") or str(page["id"]), page.get("display") or ""
                    log(f"note: roblox's account check said '{e}', used the login page instead")
                store.add(uid, name, display, cookie)
                log(f"added {name} ({uid})")
            except Exception as e:
                log(f"couldn't add account: {e}")
            finally:
                try:
                    os.remove(out)
                except OSError:
                    pass
                root.after(0, lambda: (add_btn.configure(state="normal", text="Add account"), place_bar()))

        threading.Thread(target=run, daemon=True).start()

    add_btn = btn(cv, "Add account", add_account)
    bar_btns.append(add_btn)

    def popup(title, size):
        d = tk.Toplevel(root, bg=BG)
        d.title(title)
        d.geometry(size)
        d.transient(root)
        d.grab_set()
        return d

    # ---------------- game / private server
    def game_dialog():
        d = popup("Game / server", "460x240")
        s = store.settings

        def field(text, value):
            lbl(d, text).pack(anchor="w", padx=10, pady=(8, 0))
            e = tk.Entry(d, bg="#FFFFFF", fg="#000000", insertbackground="#000000", relief="solid", bd=1, font=FONT)
            e.pack(fill="x", padx=10)
            e.insert(0, value)
            return e

        g = field("game link or place id (empty = bee swarm)", "" if s["place"] == core.BEE_SWARM else s["place"])
        p = field("private server link (empty = public)", s.get("ps_raw", ""))
        dl = field("seconds between launches", str(s.get("launch_delay", 12)))

        def save():
            place = core.parse_game(g.get())
            ps = core.parse_ps(p.get())
            try:
                delay = max(5, int(dl.get() or 12))
            except ValueError:
                delay = None
            if place is None or ps is None or delay is None:
                messagebox.showerror("NovaHop Rejoin", "invalid input, nothing changed", parent=d)
                return
            s.update(place=place, ps_raw=p.get().strip(), launch_delay=delay)
            store.save()
            log(f"saved: place {place}, private server: {'yes' if ps[0] else 'no'}")
            d.destroy()

        btn(d, "Save", save).pack(anchor="w", padx=10, pady=10)

    bar_btns.append(btn(cv, "Game / server", game_dialog))

    # ---------------- heartbeat folders
    def folders_dialog():
        d = popup("Folders", "560x320")
        lbl(d, "heartbeat folders (executor workspace\\NovaHop\\hb)").pack(anchor="w", padx=10, pady=(8, 4))
        lst = tk.Frame(d, bg=BG)
        lst.pack(fill="both", expand=True, padx=10)

        def draw():
            for w in lst.winfo_children():
                w.destroy()
            if not store.settings["hb_dirs"]:
                lbl(lst, "none", fg=DIM).pack(anchor="w")
            for path in store.settings["hb_dirs"]:
                row = tk.Frame(lst, bg=BG)
                row.pack(fill="x", pady=1)
                btn(row, "x", lambda p=path: (store.settings["hb_dirs"].remove(p), store.save(), draw())).pack(side="left")
                lbl(row, " " + path).pack(side="left")

        def scan():
            scan_btn.configure(state="disabled", text="scanning...")

            def run():
                watcher.rescan()
                root.after(0, lambda: (draw(), scan_btn.configure(state="normal", text="Scan")))
            threading.Thread(target=run, daemon=True).start()

        def pick():
            path = filedialog.askdirectory(parent=d, title="pick your executor's workspace folder")
            if not path:
                return
            path = os.path.normpath(path)
            hb = path if path.lower().endswith(os.path.join("novahop", "hb")) else os.path.join(path, "NovaHop", "hb")
            os.makedirs(hb, exist_ok=True)
            if hb not in store.settings["hb_dirs"]:
                store.settings["hb_dirs"].append(hb)
                store.save()
                log(f"watching {hb}")
            parent = os.path.dirname(path)
            ax = [os.path.join(parent, n) for n in os.listdir(parent)
                  if os.path.isdir(os.path.join(parent, n)) and n.lower().startswith("autoexec")]
            for a in core.install_heartbeat(ax):
                log(f"heartbeat in {a}")
            draw()

        row = tk.Frame(d, bg=BG)
        row.pack(anchor="w", padx=10, pady=10)
        scan_btn = btn(row, "Scan", scan)
        scan_btn.pack(side="left")
        btn(row, "Pick folder", pick).pack(side="left", padx=4)
        draw()

    bar_btns.append(btn(cv, "Folders", folders_dialog))
    bar_btns.append(btn(cv, "Settings", lambda: settings_dialog()))
    bar_btns.append(btn(cv, "Look", lambda: look_dialog()))
    bar_btns.append(btn(cv, "Discord", lambda: __import__("webbrowser").open(DISCORD)))


    # ---------------- settings
    COLORS = {"white": "#FFFFFF", "green": "#00FF00", "cyan": "#00FFFF", "pink": "#FF77FF",
              "orange": "#FFA500", "purple": "#B388FF", "yellow": "#FFFF66"}

    def recolor(widget, old, new):
        for w in widget.winfo_children():
            try:
                if str(w.cget("fg")).upper() == old.upper():
                    w.configure(fg=new)
                if isinstance(w, tk.Button):
                    w.configure(activeforeground=new)
            except tk.TclError:
                pass
            recolor(w, old, new)

    PRESETS = {"off": dict(fps_cap=0, low_gfx=False, eco=False, ram_trim=0, ram_limit=0, cpu_cap=0),
               "balanced": dict(fps_cap=30, low_gfx=False, eco=True, ram_trim=15, ram_limit=0, cpu_cap=0),
               "max alts": dict(fps_cap=15, low_gfx=True, eco=True, ram_trim=10, ram_limit=2500, cpu_cap=0)}

    def settings_dialog():
        win = popup("Settings", "540x760")
        sc = tk.Canvas(win, bg=BG, highlightthickness=0)
        bar_ = tk.Scrollbar(win, orient="vertical", command=sc.yview)
        sc.configure(yscrollcommand=bar_.set)
        bar_.pack(side="right", fill="y")
        sc.pack(side="left", fill="both", expand=True)
        d = tk.Frame(sc, bg=BG)
        sc.create_window(0, 0, window=d, anchor="nw")
        d.bind("<Configure>", lambda e: sc.configure(scrollregion=sc.bbox("all")))
        win.bind("<MouseWheel>", lambda e: sc.yview_scroll(int(-e.delta / 120), "units"))
        s = store.settings
        cv_ = {}

        def head(t):
            lbl(d, t, fg=DIM).pack(anchor="w", padx=10, pady=(12, 0))

        def entry(t, v):
            lbl(d, t).pack(anchor="w", padx=10, pady=(4, 0))
            e = tk.Entry(d, bg="#FFFFFF", fg="#000000", insertbackground="#000000", relief="solid", bd=1, font=FONT)
            e.pack(fill="x", padx=10)
            e.insert(0, str(v))
            return e

        def check(t, key):
            v = tk.IntVar(value=1 if s.get(key) else 0)
            tk.Checkbutton(d, text=t, variable=v, bg=BG, fg=T["fg"], activebackground=BG, activeforeground=T["fg"],
                           selectcolor=BG, font=FONT, anchor="w").pack(anchor="w", padx=6)
            cv_[key] = v
            return key, v

        head("-- discord webhook --")
        wh = entry("webhook url", s.get("webhook", ""))
        ping = entry("ping user id on problems (optional)", s.get("wh_ping", ""))
        checks = [check("message on every restart", "wh_restart"),
                  check("message when an account is stuck", "wh_stuck"),
                  check("message when a login expires", "wh_login"),
                  check("message on start / stop", "wh_startstop"),
                  check("live status message (accounts, ram, cpu)", "wh_status")]
        every = entry("update the status every (seconds, min 30)", s.get("status_every", 60))

        def test():
            url = wh.get().strip()
            if not core.is_webhook(url):
                messagebox.showerror("NovaHop Rejoin", "that doesn't look like a discord webhook url", parent=win)
                return

            def go():
                try:
                    core.send_webhook(url, "NovaHop Rejoin test message")
                    msg = (messagebox.showinfo, "sent, check your discord")
                except Exception as e:
                    msg = (messagebox.showerror, f"failed: {e}")
                root.after(0, lambda: msg[0]("NovaHop Rejoin", msg[1], parent=win) if win.winfo_exists() else None)
            threading.Thread(target=go, daemon=True).start()
        btn(d, "Test webhook", test).pack(anchor="w", padx=10, pady=4)

        head("-- hotkeys --")
        checks += [check("hotkey locks the mouse to the monitor it's on", "mouse_lock")]
        mk = entry("mouse lock hotkey (e.g. ctrl+alt+l, f8, ctrl+shift+m)", s.get("mouse_key", "ctrl+alt+l"))
        checks += [check("hotkey hides / shows all roblox windows", "hide_on")]
        hk = entry("hide hotkey (e.g. ctrl+alt+h)", s.get("hide_key", "ctrl+alt+h"))

        head("-- app --")
        checks += [check("start watching when the app opens", "autostart"),
                   check("start the app with windows", "win_start"),
                   check("keep window on top", "on_top"),
                   check("minimize to the tray (next to the clock)", "tray"),
                   check("open roblox windows minimized (they won't steal focus)", "launch_min"),
                   check("also read roblox's own logs (catches kicks / disconnects without the executor)", "read_logs"),
                   check("check for updates when the app opens", "update_check")]
        lbl(d, "text colour").pack(anchor="w", padx=10, pady=(4, 0))
        cur = next((k for k, v in COLORS.items() if v.upper() == T["fg"].upper()), "white")
        col = tk.StringVar(value=cur)
        om = tk.OptionMenu(d, col, *COLORS)
        om.configure(bg=BTN, fg=T["fg"], activebackground="#333333", activeforeground=T["fg"], relief="solid", bd=1,
                     font=FONT, highlightthickness=0)
        om["menu"].configure(bg=BTN, fg="#FFFFFF", font=FONT)
        om.pack(anchor="w", padx=10)

        def export():
            f = filedialog.asksaveasfilename(parent=win, title="save settings and accounts",
                                             initialfile="novahop-rejoin-backup.json",
                                             defaultextension=".json", filetypes=[("backup", "*.json")])
            if not f:
                return
            try:
                store.export(f)
                messagebox.showinfo("NovaHop Rejoin", "saved. the logins in it only work on this windows account: "
                                    "on a new pc you log in again. don't send this file to anyone.", parent=win)
            except Exception as e:
                messagebox.showerror("NovaHop Rejoin", f"couldn't save it: {e}", parent=win)

        def import_():
            f = filedialog.askopenfilename(parent=win, title="pick a novahop rejoin backup",
                                           filetypes=[("backup", "*.json")])
            if not f:
                return
            try:
                n, relog = store.import_file(f)
            except Exception as e:
                messagebox.showerror("NovaHop Rejoin", f"couldn't read it: {e}", parent=win)
                return
            rows_key[0] = None
            log(f"imported {n} account(s) and the settings")
            msg = f"imported {n} account(s) and the settings."
            if relog:
                msg += ("\n\nthese logins were saved on another pc, so log in again with Add account:\n"
                        + ", ".join(relog))
            win.destroy()
            messagebox.showinfo("NovaHop Rejoin", msg + "\n\nrestart the app to use the new look / hotkeys.")

        row = tk.Frame(d, bg=BG)
        row.pack(anchor="w", padx=10, pady=(6, 0))
        btn(row, "Export settings & accounts", export).pack(side="left")
        btn(row, "Import", import_).pack(side="left", padx=4)
        btn(row, "Open log folder", lambda: os.startfile(LOG_DIR)).pack(side="left")

        head("-- restart timing (seconds) --")
        ew = entry("restart after an error screen stays up", s.get("error_wait", core.ERROR_WAIT))
        st = entry("restart after no heartbeat for", s.get("stale", core.STALE))
        hw = entry("wait for teleport / full server errors (novahop retries these itself)", s.get("hop_wait", core.HOP_WAIT))
        bw = entry("loading time after a launch (raise it if your executor injects slowly)", s.get("boot_wait", core.BOOT_WAIT))

        head("-- performance (0 = off) --")
        prow = tk.Frame(d, bg=BG)
        prow.pack(anchor="w", padx=10, pady=(4, 0))
        lbl(prow, "preset: ").pack(side="left")
        pre = tk.StringVar(value="pick one")

        def use_preset(name):
            for key, v in PRESETS[name].items():
                if key in cv_:
                    cv_[key].set(1 if v else 0)
                else:
                    e = {"fps_cap": fc, "ram_trim": rt, "ram_limit": rl, "cpu_cap": cc}[key]
                    e.delete(0, "end")
                    e.insert(0, str(v))
        pm = tk.OptionMenu(prow, pre, *PRESETS, command=use_preset)
        pm.configure(bg=BTN, fg=T["fg"], activebackground="#333333", activeforeground=T["fg"], relief="solid", bd=1,
                     font=FONT, highlightthickness=0)
        pm["menu"].configure(bg=BTN, fg="#FFFFFF", font=FONT)
        pm.pack(side="left")
        lbl(d, "(fills in the boxes below, you can still change them)", fg=DIM).pack(anchor="w", padx=10)
        fc = entry("fps cap for every roblox window (e.g. 15 or 30)", s.get("fps_cap", 0))
        rt = entry("free up roblox ram every (minutes)", s.get("ram_trim", 0))
        rl = entry("restart an account using more ram than (MB, e.g. 2500)", s.get("ram_limit", 0))
        cc = entry("cpu limit per roblox window (% of the whole cpu, e.g. 10)", s.get("cpu_cap", 0))
        mt = entry("slow a window down while it uses more ram than (MB, e.g. 2560)", s.get("mem_throttle", 0))
        tc = entry("cpu limit while slowed down (%, e.g. 10)", s.get("throttle_cpu", 10))
        srow = tk.Frame(d, bg=BG)
        srow.pack(anchor="w", padx=10, pady=(6, 0))
        lbl(srow, "roblox window size: ").pack(side="left")
        SIZE_NAMES = {"normal": "normal", "small": "small (640x400)", "micro": "micro (320x200)"}
        size_var = tk.StringVar(value=SIZE_NAMES.get(s.get("win_size", "normal"), "normal"))
        sm = tk.OptionMenu(srow, size_var, *SIZE_NAMES.values())
        sm.configure(bg=BTN, fg=T["fg"], activebackground="#333333", activeforeground=T["fg"], relief="solid", bd=1,
                     font=FONT, highlightthickness=0)
        sm["menu"].configure(bg=BTN, fg="#FFFFFF", font=FONT)
        sm.pack(side="left")
        lbl(d, "(smaller = less graphics work. micro gets past roblox's minimum size)", fg=DIM).pack(anchor="w", padx=10)
        low_was = bool(s.get("low_gfx"))
        checks += [check("lowest graphics: no grass, low textures, gray sky (less ram & gpu)", "low_gfx"),
                   check("efficiency mode: roblox windows get low priority so your pc stays smooth", "eco")]

        def save():
            # check everything first: nothing is saved unless all of it is right
            url = wh.get().strip()
            if url and not core.is_webhook(url):
                messagebox.showerror("NovaHop Rejoin", "that doesn't look like a discord webhook url, nothing saved", parent=win)
                return
            try:
                new = dict(error_wait=max(5, int(ew.get())), stale=max(30, int(st.get())),
                           boot_wait=max(60, int(bw.get())), hop_wait=max(10, int(hw.get())),
                           status_every=max(30, int(every.get())), fps_cap=max(0, int(fc.get())),
                           ram_trim=max(0, int(rt.get())), ram_limit=max(0, int(rl.get())),
                           cpu_cap=max(0, min(100, int(cc.get()))), mem_throttle=max(0, int(mt.get())),
                           throttle_cpu=max(1, min(100, int(tc.get()))))
            except ValueError:
                messagebox.showerror("NovaHop Rejoin", "timings and performance boxes have to be numbers, nothing saved", parent=win)
                return
            key = mk.get().strip().lower()
            hkey = hk.get().strip().lower()
            if not mouselock.parse_hotkey(key) or not mouselock.parse_hotkey(hkey):
                messagebox.showerror("NovaHop Rejoin", "didn't understand a hotkey. use something like ctrl+alt+l or f8, "
                                                       "nothing saved", parent=win)
                return
            if key == hkey and cv_["mouse_lock"].get() and cv_["hide_on"].get():
                messagebox.showerror("NovaHop Rejoin", "the two hotkeys have to be different, nothing saved", parent=win)
                return
            fps_changed = new["fps_cap"] != int(s.get("fps_cap", 0))
            new.update(webhook=url, wh_ping=ping.get().strip(), mouse_key=key, hide_key=hkey,
                       win_size=next(k for k, v in SIZE_NAMES.items() if v == size_var.get()))
            for k, v in checks:
                new[k] = bool(v.get())
            if new["win_start"] != bool(s.get("win_start")):
                try:
                    core.set_autorun(new["win_start"])
                except OSError as e:
                    messagebox.showerror("NovaHop Rejoin", f"couldn't change start with windows: {e}", parent=win)
                    new["win_start"] = bool(s.get("win_start"))
            s.update(new)
            if fps_changed or bool(s.get("low_gfx")) != low_was:
                threading.Thread(target=watcher.apply_fps, daemon=True).start()
            new_col = COLORS[col.get()]
            if new_col.upper() != T["fg"].upper():
                old = T["fg"]
                T["fg"] = new_col
                s["text_color"] = new_col
                recolor(root, old, new_col)
                rows_key[0] = None
            root.attributes("-topmost", bool(s.get("on_top")))
            store.save()
            if s.get("mouse_lock"):
                mlock.start(s["mouse_key"])
            else:
                mlock.stop_hotkey()
                mlock.unlock()
            if s.get("hide_on"):
                hider.start(s["hide_key"])
            else:
                hider.stop()
            log("settings saved")
            win.destroy()

        btn(d, "Save", save).pack(anchor="w", padx=10, pady=12)


    # ---------------- look: colours, background picture, see-through window
    def restart_app():
        hider.stop()
        root.destroy()
        os._exit(RESTART)      # the supervisor opens the app window again

    def look_dialog():
        from tkinter import colorchooser
        d = popup("Look", "500x540")
        s = store.settings
        new = {k: s.get(k) for k in ("bg_color", "btn_color", "bg_image", "alpha", "show_log", "bg_mode")}
        new["bg_mode"] = new["bg_mode"] or "fill"

        def color_row(text, key):
            row = tk.Frame(d, bg=BG)
            row.pack(fill="x", padx=10, pady=4)
            sw = tk.Label(row, width=4, bg=new[key], relief="solid", bd=1)
            sw.pack(side="left")
            lbl(row, " " + text).pack(side="left")

            def pick():
                c = colorchooser.askcolor(color=new[key], parent=d, title=text)[1]
                if c:
                    new[key] = c
                    sw.configure(bg=c)
            btn(row, "pick", pick).pack(side="right")

        lbl(d, "-- colours --", fg=DIM).pack(anchor="w", padx=10, pady=(10, 0))
        color_row("background colour", "bg_color")
        color_row("button colour", "btn_color")
        lbl(d, "(text colour is in Settings)", fg=DIM).pack(anchor="w", padx=10)

        lbl(d, "-- background picture --", fg=DIM).pack(anchor="w", padx=10, pady=(12, 0))
        pic = lbl(d, os.path.basename(new["bg_image"]) if new["bg_image"] else "none")
        pic.pack(anchor="w", padx=10)
        prow = tk.Frame(d, bg=BG)
        prow.pack(anchor="w", padx=10, pady=4)

        def choose():
            f = filedialog.askopenfilename(parent=d, title="pick a background picture",
                                           filetypes=[("pictures", "*.png *.jpg *.jpeg *.gif *.bmp *.webp")])
            if not f:
                return
            try:
                from PIL import Image
                im = Image.open(f).convert("RGB")
                im.thumbnail((2560, 1440))
                dest = os.path.join(core.DATA_DIR, "background.png")
                os.makedirs(core.DATA_DIR, exist_ok=True)
                im.save(dest)                 # keep our own copy, so moving the original doesn't break it
                new["bg_image"] = dest
                pic.configure(text=os.path.basename(f))
            except Exception as e:
                messagebox.showerror("NovaHop Rejoin", f"couldn't open that picture: {e}", parent=d)

        def clear():
            new["bg_image"] = ""
            pic.configure(text="none")
        btn(prow, "Choose picture", choose).pack(side="left")
        btn(prow, "Remove", clear).pack(side="left", padx=4)

        mrow = tk.Frame(d, bg=BG)
        mrow.pack(anchor="w", padx=10, pady=(2, 0))
        lbl(mrow, "picture size: ").pack(side="left")
        mode_var = tk.StringVar(value=new["bg_mode"])
        mm = tk.OptionMenu(mrow, mode_var, "fill", "fit", "stretch", "center", "tile")
        mm.configure(bg=BTN, fg=T["fg"], activebackground=DIM, activeforeground=T["fg"], relief="solid", bd=1,
                     font=FONT, highlightthickness=0)
        mm["menu"].configure(bg=BTN, fg="#FFFFFF", font=FONT)
        mm.pack(side="left")
        lbl(d, "fill = covers the window   fit = whole picture   tile = repeat a small one", fg=DIM).pack(anchor="w", padx=10)

        lbl(d, "-- window --", fg=DIM).pack(anchor="w", padx=10, pady=(12, 0))
        lbl(d, "see-through (100 = solid)").pack(anchor="w", padx=10)
        op = tk.Scale(d, from_=40, to=100, orient="horizontal", bg=BG, fg=T["fg"], troughcolor=BTN,
                      highlightthickness=0, font=FONT, length=260)
        op.set(int(float(new["alpha"] or 1.0) * 100))
        op.configure(command=lambda v: root.attributes("-alpha", int(v) / 100))
        op.pack(anchor="w", padx=10)
        sl = tk.IntVar(value=1 if new["show_log"] else 0)
        tk.Checkbutton(d, text="show the log", variable=sl, bg=BG, fg=T["fg"], activebackground=BG,
                       activeforeground=T["fg"], selectcolor=BG, font=FONT).pack(anchor="w", padx=6)

        def save():
            new["alpha"] = int(op.get()) / 100
            new["show_log"] = bool(sl.get())
            new["bg_mode"] = mode_var.get()
            changed = any(new[k] != s.get(k) for k in ("bg_color", "btn_color", "bg_image", "show_log", "bg_mode"))
            s.update(new)
            store.save()
            d.destroy()
            if changed and messagebox.askyesno("NovaHop Rejoin", "restart the app now to apply the new look?\n"
                                                                 "(your roblox windows keep running)"):
                restart_app()

        def reset():
            new.update(bg_color="#000000", btn_color="#1A1A1A", bg_image="", alpha=1.0, show_log=True)
            mode_var.set("fill")
            op.set(100)
            sl.set(1)
            save()

        row = tk.Frame(d, bg=BG)
        row.pack(anchor="w", padx=10, pady=12)
        btn(row, "Save", save).pack(side="left")
        btn(row, "Reset to default", reset).pack(side="left", padx=4)

    # ---------------- accounts
    def close_all():
        if messagebox.askyesno("NovaHop Rejoin", "close every roblox window this app launched?"):
            watcher.close_all()
    rows = {}
    rows_key = [None]
    row_widgets = []
    log_view = {"y": 0, "lines": []}
    shown = [0]

    def remove(a):
        if messagebox.askyesno("NovaHop Rejoin", f"remove {a['name']}? its saved login gets deleted"):
            store.remove(a["uid"])

    def rename(a):
        from tkinter import simpledialog
        n = simpledialog.askstring("Rename", f"nickname for {a['name']} (empty = username)",
                                   initialvalue=a.get("nick", ""), parent=root)
        if n is not None:
            a["nick"] = n.strip()
            store.save()
            rows_key[0] = None

    def server_dialog(a):
        d = popup(f"Server for {a.get('nick') or a['name']}", "460x260")
        own = tk.IntVar(value=1 if a.get("own_server") else 0)
        tk.Checkbutton(d, text="use its own game / private server", variable=own, bg=BG, fg=T["fg"],
                       activebackground=BG, activeforeground=T["fg"], selectcolor=BG, font=FONT).pack(anchor="w", padx=6, pady=(10, 0))
        lbl(d, "(off = uses the main Game / server setting)", fg=DIM).pack(anchor="w", padx=10)

        def field(text, value):
            lbl(d, text).pack(anchor="w", padx=10, pady=(8, 0))
            e = tk.Entry(d, bg="#FFFFFF", fg="#000000", insertbackground="#000000", relief="solid", bd=1, font=FONT)
            e.pack(fill="x", padx=10)
            e.insert(0, value)
            return e
        place = a.get("place", "")
        g = field("game link or place id (empty = bee swarm)", "" if place in ("", core.BEE_SWARM) else place)
        p = field("private server link (empty = public)", a.get("ps_raw", ""))

        def save():
            pl = core.parse_game(g.get())
            ps = core.parse_ps(p.get())
            if pl is None or ps is None:
                messagebox.showerror("NovaHop Rejoin", "invalid input, nothing changed", parent=d)
                return
            a.update(own_server=bool(own.get()), place=pl, ps_raw=p.get().strip())
            store.save()
            log(f"{a['name']}: " + (f"own server, place {pl}, private: {'yes' if ps[0] else 'no'}" if own.get() else "uses the main server setting"))
            d.destroy()
        btn(d, "Save", save).pack(anchor="w", padx=10, pady=10)

    def set_enabled(a, on):
        a["enabled"] = on
        store.save()

    # ---------------- smooth rounded shapes: Tk can't smooth edges itself, so they are small pictures made with
    # Pillow (drawn 4x bigger, then shrunk). Same size = same picture, so the whole screen uses only a handful.
    CARD_W, CARD_H, GAP, BH = 300, 158, 10, 26
    CARD_BG, CARD_LINE, BAR_BG, HOVER = "#0E0E0E", "#2A2A2A", "#262626", "#2E2E2E"
    _imgs = {}

    def rimg(w, h, r, fill, line=None):
        key = (w, h, r, fill, line)
        if key not in _imgs:
            from PIL import Image, ImageDraw, ImageTk
            im = Image.new("RGBA", (w * 4, h * 4), (0, 0, 0, 0))
            ImageDraw.Draw(im).rounded_rectangle((0, 0, w * 4 - 1, h * 4 - 1), r * 4, fill=fill, outline=line,
                                                 width=4 if line else 0)
            _imgs[key] = ImageTk.PhotoImage(im.resize((w, h), Image.LANCZOS))
        return _imgs[key]

    def pill(x, y, label, tag, cmd, w=None, h=BH, small=False):
        """a rounded button drawn on the canvas, lighter while the mouse is over it -> its width"""
        f = FNT_S if small else FNT
        w = w or f.measure(label) + 22
        cv.create_image(x, y, anchor="nw", image=rimg(w, h, 7, BTN, CARD_LINE), tags=(tag, tag + "_bg", "pill"))
        cv.create_text(x + w / 2, y + h / 2, text=label, font=f, fill=T["fg"] if cmd else DIM,
                       tags=(tag, tag + "_t", "pill"))
        if cmd:
            cv.tag_bind(tag, "<Button-1>", lambda e: cmd(e))
            cv.tag_bind(tag, "<Enter>", lambda e: (cv.itemconfigure(tag + "_bg", image=rimg(w, h, 7, HOVER, CARD_LINE)),
                                                   cv.configure(cursor="hand2")))
            cv.tag_bind(tag, "<Leave>", lambda e: (cv.itemconfigure(tag + "_bg", image=rimg(w, h, 7, BTN, CARD_LINE)),
                                                   cv.configure(cursor="")))
        return w

    def place_bar():
        """the top buttons: the tk.Buttons only hold their text / command / state, the canvas draws them"""
        cv.delete("bar")
        x = 12
        for i, b in enumerate(bar_btns):
            on = b.cget("state") != "disabled"
            x += pill(x, layout_y["bar"], b.cget("text"), f"bar{i}", (lambda e, b=b: b.invoke()) if on else None) + 6
        cv.addtag_withtag("bar", "bar0")
        for i in range(1, len(bar_btns)):
            cv.addtag_withtag("bar", f"bar{i}")

    layout_y = {"bar": 12 + LH * 2 + 6}

    # ---------------- account cards: drawn straight onto the canvas (no extra windows per card, so it stays light)
    FNT_B = tkfont.Font(family="Consolas", size=10, weight="bold")
    FNT_S = tkfont.Font(family="Consolas", size=9)
    card_seen = {}          # tag -> last value, so unchanged parts are never redrawn
    avatars = {}            # uid -> PhotoImage (made once)
    PERF = ("fps_cap", "low_gfx", "eco", "ram_trim", "cpu_cap", "mem_throttle")
    menu = tk.Menu(root, tearoff=0, bg=BTN, fg="#FFFFFF", activebackground=DIM, font=FONT)

    def columns():
        return max(1, (max(cv.winfo_width(), root.winfo_width()) - 24 + GAP) // (CARD_W + GAP))

    def perf_count():
        return sum(1 for k in PERF if store.settings.get(k))

    def cset(tag, **kw):
        if card_seen.get(tag) != kw:
            card_seen[tag] = kw
            cv.itemconfigure(tag, **kw)

    def bar(tag, x, frac):
        x2 = x + max(0, min(1.0, frac)) * 60
        if card_seen.get(tag) != x2:
            card_seen[tag] = x2
            c = cv.coords(tag)
            if c:
                cv.coords(tag, c[0], c[1], max(c[0] + 1, x2), c[3])

    def card_btn(x, y, w, label, tag, cmd):
        pill(x, y, label, tag, cmd, w=w, h=22, small=True)
        cv.addtag_withtag("card", tag)

    def more(e, a):
        menu.delete(0, "end")
        menu.add_command(label="launch", command=lambda: watcher.launch_now(a["uid"]))
        menu.add_command(label="server", command=lambda: server_dialog(a))
        menu.add_command(label="close", command=lambda: watcher.close(a["uid"]))
        menu.add_command(label="rename", command=lambda: rename(a))
        menu.add_separator()
        menu.add_command(label="remove", command=lambda: remove(a))
        menu.tk_popup(e.x_root, e.y_root)

    def min_or_restore(a):
        if watcher.minimized.get(a["uid"]):
            watcher.show(a["uid"], front=False)
        else:
            watcher.minimize(a["uid"])

    def load_avatar(uid, tag):
        f = os.path.join(core.DATA_DIR, "avatars", uid + ".png")

        def put():
            try:
                if uid not in avatars:           # rounded corners, to match the cards
                    from PIL import Image, ImageDraw, ImageTk
                    im = Image.open(f).convert("RGBA").resize((48, 48), Image.LANCZOS)
                    mask = Image.new("L", (192, 192), 0)
                    ImageDraw.Draw(mask).rounded_rectangle((0, 0, 191, 191), 32, fill=255)
                    im.putalpha(mask.resize((48, 48), Image.LANCZOS))
                    avatars[uid] = ImageTk.PhotoImage(im)
                cv.itemconfigure(tag, image=avatars[uid])
            except Exception:
                pass
        if uid in avatars or os.path.exists(f):
            put()
            return

        def fetch():          # roblox's public headshot picture, saved so it's only downloaded once
            try:
                import urllib.request
                u = f"https://thumbnails.roblox.com/v1/users/avatar-headshot?userIds={uid}&size=48x48&format=Png"
                with urllib.request.urlopen(urllib.request.Request(u, headers={"User-Agent": core.UA}), timeout=10) as r:
                    img = json.load(r)["data"][0]["imageUrl"]
                with urllib.request.urlopen(urllib.request.Request(img, headers={"User-Agent": core.UA}), timeout=10) as r:
                    data = r.read()
                os.makedirs(os.path.dirname(f), exist_ok=True)
                with open(f, "wb") as fh:
                    fh.write(data)
                root.after(0, put)
            except Exception:
                pass
        threading.Thread(target=fetch, daemon=True).start()

    def draw_card(a, x, y):
        uid = a["uid"]
        cv.create_image(x, y, anchor="nw", image=rimg(CARD_W, CARD_H, 12, CARD_BG, CARD_LINE), tags="card")
        cv.create_image(x + 10, y + 10, anchor="nw", image=rimg(48, 48, 8, BAR_BG), tags="card")
        cv.create_image(x + 10, y + 10, anchor="nw", tags=("card", "av_" + uid))
        load_avatar(uid, "av_" + uid)
        nm = "nm_" + uid
        cv.create_text(x + 68, y + 12, text=a.get("nick") or a["name"], anchor="nw", font=FNT_B, fill=T["fg"],
                       tags=("card", nm))
        cv.tag_bind(nm, "<Double-Button-1>", lambda e: rename(a))
        cv.create_text(x + 68, y + 32, text="", anchor="nw", font=FNT_S, fill=DIM, tags=("card", "sub_" + uid))
        en = "en_" + uid
        cv.create_text(x + CARD_W - 10, y + 12, text="", anchor="ne", font=FNT_S, tags=("card", en))
        cv.tag_bind(en, "<Button-1>", lambda e: set_enabled(a, not a.get("enabled", True)))
        # tags: window size + how many performance settings are on
        tx = x + 68
        size = store.settings.get("win_size", "normal")
        n = perf_count()
        for t in ([size] if size != "normal" else []) + ([f"+{n} from performance"] if n else []):
            w = FNT_S.measure(t) + 12
            cv.create_image(tx, y + 50, anchor="nw", image=rimg(w, 17, 7, BAR_BG), tags="card")
            cv.create_text(tx + 6, y + 58, text=t, anchor="w", font=FNT_S, fill=T["fg"], tags="card")
            tx += w + 6
        cv.create_text(x + 10, y + 74, text="", anchor="nw", font=FNT_S, tags=("card", "st_" + uid))
        for label, bx, key in (("cpu", x + 10, "cpu"), ("ram", x + 152, "mem")):
            cv.create_text(bx, y + 104, text=label, anchor="w", font=FNT_S, fill=DIM, tags="card")
            cv.create_rectangle(bx + 30, y + 102, bx + 90, y + 107, fill=BAR_BG, outline="", tags="card")
            cv.create_rectangle(bx + 30, y + 102, bx + 31, y + 107, fill=T["fg"], outline="",
                                tags=("card", f"{key}bar_{uid}"))
            cv.create_text(bx + 96, y + 104, text="", anchor="w", font=FNT_S, fill=T["fg"],
                           tags=("card", f"{key}_{uid}"))
        card_btn(x + 10, y + 124, 80, "show", "bshow_" + uid, lambda e: watcher.show(uid))
        card_btn(x + 96, y + 124, 90, "minimize", "bmin_" + uid, lambda e: min_or_restore(a))
        card_btn(x + CARD_W - 40, y + 124, 30, "...", "bmore_" + uid, lambda e: more(e, a))

    def layout():
        """(re)draw everything except the background"""
        cv.delete("txt")
        cv.delete("rowpill")
        cv.delete("card")
        drawn.clear()
        card_seen.clear()
        for w in row_widgets:
            w.destroy()
        row_widgets.clear()
        rows.clear()
        x0 = 12
        text(x0, 10, "status: stopped", T["fg"], "status")
        text(x0, 10 + LH, "", DIM, "info")
        place_bar()
        bh = BH
        y = layout_y["bar"] + bh + 14

        def rbtn(t, cmd, x, yy):
            tag = "row_" + t.replace(" ", "_")
            w = pill(x, yy, t, tag, lambda e: cmd())
            cv.addtag_withtag("rowpill", tag)
            return x + w + 6

        text(x0, y + 4, "accounts:", T["fg"], "acc_h")
        x = x0 + FNT.measure("accounts: ") + 6
        x = rbtn("Launch all", watcher.launch_all, x, y)
        rbtn("Close all", close_all, x, y)
        y += bh + 10
        if not store.accounts:
            text(x0, y, "no accounts", DIM, "noacc")
            y += LH + 6
        cols = columns()
        for i, a in enumerate(store.accounts):
            draw_card(a, x0 + (i % cols) * (CARD_W + GAP), y + (i // cols) * (CARD_H + GAP))
            rows[a["uid"]] = True
        if store.accounts:
            y += ((len(store.accounts) + cols - 1) // cols) * (CARD_H + GAP)
        if store.settings.get("show_log", True):
            y += 6
            text(x0, y + 4, "log:", T["fg"], "log_h")
            rbtn("clear", clear_log, x0 + FNT.measure("log: ") + 6, y)
            y += bh + 6
            log_view["y"] = y
            text(x0, y, "", DIM, "logtxt")
            draw_log()

    def draw_log():
        if not store.settings.get("show_log", True) or not log_view["y"]:
            return
        h = max(cv.winfo_height(), root.winfo_height())
        n = max(1, (h - log_view["y"] - 10) // LH)
        set_text("logtxt", "\n".join(log_view["lines"][-n:]))

    def clear_log():
        log_view["lines"].clear()
        draw_log()

    def color(s):
        if s == "OK":
            return "#00FF00"
        if s in ("off", "not watching", "not started"):
            return DIM
        if any(s.startswith(x) for x in ("loading", "starting", "launching", "waiting", "window closed",
                                         "teleporting", "already open")):
            return "#FFFF00"
        return "#FF4040"

    def refresh():
        on = watcher.alive
        s = store.settings
        key = "|".join([",".join(a["uid"] for a in store.accounts), T["fg"], str(columns()),
                        s.get("win_size", "normal"), str(perf_count())])
        if key != rows_key[0]:
            rows_key[0] = key
            layout()
        set_text("status", "status: running" if on else "status: stopped")
        if start_btn.cget("text") != ("Stop" if on else "Start"):
            start_btn.configure(text="Stop" if on else "Start")
            place_bar()
        game = "bee swarm" if s["place"] == core.BEE_SWARM else f"place {s['place']}"
        set_text("info", f"{game} | {'private server' if s.get('ps_raw') else 'public'} | {len(s['hb_dirs'])} hb folder(s)"
                         + (f" | mouse locked ({s.get('mouse_key')} to free)" if mlock.locked else "")
                         + (" | ROBLOX WAS ALREADY OPEN: only 1 window works, close roblox + stop/start"
                            if on and not watcher.multi_ok else "")
                         + (" | roblox is updating..." if on and watcher.updating else "")
                         + (f" | {len(watcher.hidden)} window(s) hidden" if watcher.hidden else ""))
        limit = int(s.get("ram_limit", 0)) or 3000
        for a in store.accounts:
            uid = a["uid"]
            if uid not in rows:
                continue
            t = watcher.status.get(uid, "not started")
            if a.get("relogin"):
                t = "log in again (add this account again)"
            elif not a.get("enabled", True):
                t = "off"
            elif not on and not t.startswith(("login expired", "log in again")):
                t = "not watching"
            r = watcher.restarts.get(uid, 0)
            cset("st_" + uid, text=(f"{t} | {r} restarts" if r else t)[:44], fill=color(t))
            last = watcher.last.get(uid)
            up = f" · {core._ago(time.time() - last)}" if on and t == "OK" and last else ""
            game_ = core.game_name(core.server_for(a, s)[0]).lower()
            cset("sub_" + uid, text=(game_[:24] + up))
            en = a.get("enabled", True)
            cset("en_" + uid, text="[on]" if en else "[off]", fill="#00FF00" if en else DIM)
            cpu = watcher.cpu.get(uid) if on else None
            mb = watcher.ram.get(uid) if on else None
            cset(f"cpu_{uid}", text=f"{cpu:.0f}%" if cpu is not None else "-")
            cset(f"mem_{uid}", text=(f"{mb / 1024:.1f} GB" if mb >= 1024 else f"{mb} MB") if mb else "-")
            bar(f"cpubar_{uid}", cv.coords(f"cpubar_{uid}")[0] if cv.coords(f"cpubar_{uid}") else 0, (cpu or 0) / 100 * 5)
            bar(f"membar_{uid}", cv.coords(f"membar_{uid}")[0] if cv.coords(f"membar_{uid}") else 0, (mb or 0) / limit)
            cset("bmin_" + uid + "_t", text="restore" if watcher.minimized.get(uid) else "minimize")
        with lock:
            new = [t for n, t in lines if n > shown[0]]
            shown[0] = count[0]
        if new:
            log_view["lines"].extend(new)
            del log_view["lines"][:-300]
            draw_log()

    bg_state = {"size": None, "job": None}

    def fit_bg():
        if not PIC:
            return
        try:
            w, h = root.winfo_width(), root.winfo_height()
            if w < 50 or h < 50:
                root.after(200, fit_bg)
                return
            if bg_state["size"] == (w, h):
                return
            bg_state["size"] = (w, h)
            mode = store.settings.get("bg_mode", "fill")
            if mode == "stretch":
                out = bg_src.resize((w, h))
            elif mode in ("fit", "center"):
                out = Image.new("RGB", (w, h), BG)
                r = min(w / bg_src.width, h / bg_src.height) if mode == "fit" else min(1.0, w / bg_src.width, h / bg_src.height)
                im = bg_src.resize((max(1, int(bg_src.width * r)), max(1, int(bg_src.height * r))))
                out.paste(im, ((w - im.width) // 2, (h - im.height) // 2))
            elif mode == "tile":
                out = Image.new("RGB", (w, h), BG)
                for yy in range(0, h, bg_src.height):
                    for xx in range(0, w, bg_src.width):
                        out.paste(bg_src, (xx, yy))
            else:                                                     # fill: cover the window, crop the rest
                r = max(w / bg_src.width, h / bg_src.height)
                im = bg_src.resize((int(bg_src.width * r) + 1, int(bg_src.height * r) + 1))
                l, t = (im.width - w) // 2, (im.height - h) // 2
                out = im.crop((l, t, l + w, t + h))
            cache = os.path.join(core.DATA_DIR, "background_fit.png")
            out.save(cache)
            cv.img = tk.PhotoImage(file=cache)                       # Tk reads PNG itself
            cv.itemconfigure(bg_item, image=cv.img)
            cv.tag_lower(bg_item)
        except Exception as e:
            log(f"background picture failed: {e}")

    def on_resize(e):
        if e.widget is root:
            if bg_state["job"]:
                root.after_cancel(bg_state["job"])
            bg_state["job"] = root.after(150, lambda: (fit_bg(), draw_log()))
    root.bind("<Configure>", on_resize, add="+")
    root.after(300, fit_bg)

    def tick():
        if os.path.exists(show_flag):       # the app was opened again: show this copy instead
            try:
                os.remove(show_flag)
            except OSError:
                pass
            restore()
        if root.state() == "normal":        # minimized / in the tray: nothing to draw
            refresh()
        root.after(1500, tick)

    # ---------------- tray
    tray = {"icon": None}
    icon_file = os.path.join(getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__))), "icon.ico")

    def quit_app():
        mlock.unlock()
        if watcher.hidden:
            core.set_hidden([], False, watcher.hidden)    # never leave roblox windows invisible
        try:
            os.remove(running_flag)
        except OSError:
            pass
        if tray["icon"]:
            try:
                tray["icon"].stop()
            except Exception:
                pass
        root.destroy()
        os._exit(0)

    def restore():
        if tray["icon"]:
            try:
                tray["icon"].stop()
            except Exception:
                pass
            tray["icon"] = None
        root.deiconify()
        root.lift()
        root.focus_force()

    def to_tray():
        try:
            import pystray
            from PIL import Image
        except Exception:
            return False
        try:
            img = Image.open(icon_file)
        except Exception:
            img = Image.new("RGB", (64, 64), (0, 0, 0))
        menu = pystray.Menu(
            pystray.MenuItem("Show", lambda i, it: root.after(0, restore), default=True),
            pystray.MenuItem(lambda it: "Stop watching" if watcher.alive else "Start watching",
                             lambda i, it: root.after(0, toggle)),
            pystray.MenuItem("Quit", lambda i, it: root.after(0, quit_app)))
        tray["icon"] = pystray.Icon("NovaHopRejoin", img, "NovaHop Rejoin", menu)
        tray["icon"].run_detached()
        root.withdraw()
        return True

    def on_unmap(e):
        if e.widget is root and root.state() == "iconic" and store.settings.get("tray", True) and not tray["icon"]:
            root.after(10, to_tray)
    root.bind("<Unmap>", on_unmap, add="+")
    root.protocol("WM_DELETE_WINDOW", quit_app)     # always free the mouse when closing
    if store.settings.get("mouse_lock"):
        mlock.start(store.settings.get("mouse_key", "ctrl+alt+l"))
    if store.settings.get("hide_on"):
        hider.start(store.settings.get("hide_key", "ctrl+alt+h"))
    if store.settings.get("win_start"):
        try:
            core.set_autorun(True)      # keeps pointing at this copy if the exe was moved
        except OSError:
            pass

    # ---------------- update check
    VERSION_FILE = os.path.join(getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__))), "version.txt")
    try:
        with open(VERSION_FILE, encoding="utf-8-sig") as f:
            my_version = int(f.read().strip())
    except Exception:
        my_version = None
    root.title(f"NovaHop Rejoin v1.0.{my_version}" if my_version else "NovaHop Rejoin")

    def check_updates():
        try:
            newest = core.latest_version()
        except Exception:
            return
        if my_version and newest and newest > my_version:
            def ask():
                log(f"update available: v1.0.{newest} (you have v1.0.{my_version})")
                if messagebox.askyesno("NovaHop Rejoin", f"a new version is out (v1.0.{newest}).\nopen the download page?"):
                    __import__("webbrowser").open(f"https://github.com/{core.REPO}/releases/latest")
            root.after(0, ask)
    if store.settings.get("update_check", True):
        threading.Thread(target=check_updates, daemon=True).start()

    if not core.roblox_installed():
        log("roblox doesn't look installed. install it from roblox.com (or bloxstrap / fishstrap) first")
    if resume and os.path.exists(running_flag):
        log("the app window crashed and was opened again, watching goes on")
        root.after(1500, toggle)
    else:
        log("ready. close all roblox windows before pressing start")
        if store.settings.get("autostart") and store.accounts:
            root.after(1500, toggle)
    tick()
    root.mainloop()


# ------------------------------------------------------------------ supervisor
RESTART = 5      # exit code: open the app window again (new look)
_app_mutex = None


def supervise():
    """Only one copy at a time (opening it again shows the running one). The app window runs as a child
    process that is opened again if it crashes: when Windows runs out of drawing memory Tk can kill its
    whole process, and the watcher must not die with it."""
    global _app_mutex
    if core.IS_WIN:
        import ctypes
        _app_mutex = core._k.CreateMutexW(None, False, "Local\\NovaHopRejoinApp")
        if ctypes.get_last_error() == 183:
            try:
                open(core._path("show.flag"), "w").close()
            except OSError:
                pass
            return
    cmd = [sys.executable] + ([] if getattr(sys, "frozen", False) else [os.path.abspath(__file__)]) + ["--child"]
    crashes, resume = [], False
    while True:
        code = subprocess.run(cmd + (["--resume"] if resume else [])).returncode
        if code == 0:
            return
        if code == RESTART:
            resume = False
            continue
        crashes = [t for t in crashes if time.time() - t < 600] + [time.time()]
        if len(crashes) > 5:          # crashing over and over: stop instead of looping forever
            return
        resume = True
        time.sleep(3)


if __name__ == "__main__":
    if len(sys.argv) >= 3 and sys.argv[1] == "--login":
        login_mode(sys.argv[2])
    elif "--child" in sys.argv:
        app_mode("--resume" in sys.argv)
    else:
        supervise()
