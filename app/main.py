"""NovaHop Rejoin for Windows - the app window.

Run with no arguments for the app. "--login <file>" opens the Roblox login window (used by
"Add account"; it runs as its own process because the browser window needs the main thread).
"""

import os
import subprocess
import sys
import tempfile
import threading
import time

import core


# ------------------------------------------------------------------ login window (child process)
def login_mode(out_file):
    import webview

    w = webview.create_window("Log in to Roblox - roblox.com", "https://www.roblox.com/login",
                              width=520, height=760)

    def wait_for_login():
        while True:
            time.sleep(1.2)
            try:
                cookies = w.get_cookies() or []
            except Exception:
                continue
            for c in cookies:
                for key, morsel in c.items():
                    if key == ".ROBLOSECURITY" and morsel.value:
                        with open(out_file, "w", encoding="utf-8") as f:
                            f.write(core.protect(morsel.value))   # encrypted before it touches the disk
                        w.destroy()
                        return

    webview.start(wait_for_login, private_mode=True)   # private: every login starts logged out
    os._exit(0)


# ------------------------------------------------------------------ app window
def app_mode():
    import tkinter as tk
    from tkinter import filedialog, messagebox

    BG = "#000000"; FG = "#FFFFFF"; DIM = "#888888"; BTN = "#1A1A1A"
    FONT = ("Consolas", 10)
    DISCORD = "https://discord.gg/brfJ2ZtegE"

    store = core.Store()
    T = {"fg": store.settings.get("text_color", "#FFFFFF")}
    lines = []
    lock = threading.Lock()
    count = [0]

    def log(msg):
        with lock:
            count[0] += 1
            lines.append((count[0], time.strftime("[%H:%M:%S] ") + msg))
            del lines[:-300]

    watcher = core.Watcher(store, log)

    root = tk.Tk()
    root.title("NovaHop Rejoin")
    root.geometry("640x620")
    root.configure(bg=BG)
    root.attributes("-topmost", bool(store.settings.get("on_top")))
    try:
        root.iconbitmap(os.path.join(getattr(sys, "_MEIPASS", os.path.dirname(__file__)), "icon.ico"))
    except Exception:
        pass

    def btn(parent, text, cmd, width=None):
        b = tk.Button(parent, text=text, command=cmd, bg=BTN, fg=T["fg"], activebackground="#333333",
                      activeforeground=T["fg"], relief="solid", bd=1, font=FONT, padx=8, pady=3)
        if width:
            b.configure(width=width)
        return b

    def lbl(parent, text="", fg=None, **kw):
        return tk.Label(parent, text=text, bg=BG, fg=fg or T["fg"], font=FONT, anchor="w", justify="left", **kw)

    top = tk.Frame(root, bg=BG)
    top.pack(fill="x", padx=10, pady=(10, 4))
    status_lbl = lbl(top, "status: stopped")
    status_lbl.pack(anchor="w")
    info_lbl = lbl(top, fg=DIM)
    info_lbl.pack(anchor="w")

    def toggle():
        if watcher.alive:
            watcher.stop()
        else:
            if not store.accounts:
                messagebox.showinfo("NovaHop Rejoin", "add an account first")
                return
            watcher.start()
        refresh()

    bar = tk.Frame(root, bg=BG)
    bar.pack(fill="x", padx=10, pady=4)
    start_btn = btn(bar, "Start", toggle, 8)
    start_btn.pack(side="left")

    # ---------------- add account
    def add_account():
        add_btn.configure(state="disabled", text="log in in the new window...")
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
                    cookie = core.unprotect(f.read())
                os.remove(out)
                uid, name, display = core.whoami(cookie)
                store.add(uid, name, display, cookie)
                log(f"added {name} ({uid})")
            except Exception as e:
                log(f"couldn't add account: {e}")
            finally:
                try:
                    os.remove(out)
                except OSError:
                    pass
                root.after(0, lambda: add_btn.configure(state="normal", text="Add account"))

        threading.Thread(target=run, daemon=True).start()

    add_btn = btn(bar, "Add account", add_account)
    add_btn.pack(side="left", padx=4)

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

    btn(bar, "Game / server", game_dialog).pack(side="left", padx=4)

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

    btn(bar, "Folders", folders_dialog).pack(side="left", padx=4)
    btn(bar, "Settings", lambda: settings_dialog()).pack(side="left", padx=4)
    btn(bar, "Discord", lambda: __import__("webbrowser").open(DISCORD)).pack(side="left", padx=4)


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

    def settings_dialog():
        d = popup("Settings", "500x720")
        s = store.settings

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
            return key, v

        head("-- discord webhook --")
        wh = entry("webhook url", s.get("webhook", ""))
        ping = entry("ping user id on problems (optional)", s.get("wh_ping", ""))
        checks = [check("message on every restart", "wh_restart"),
                  check("message when an account is stuck", "wh_stuck"),
                  check("message when a login expires", "wh_login"),
                  check("message on start / stop", "wh_startstop")]

        def test():
            url = wh.get().strip()
            if not core.is_webhook(url):
                messagebox.showerror("NovaHop Rejoin", "that doesn't look like a discord webhook url", parent=d)
                return
            try:
                core.send_webhook(url, "NovaHop Rejoin test message")
                messagebox.showinfo("NovaHop Rejoin", "sent, check your discord", parent=d)
            except Exception as e:
                messagebox.showerror("NovaHop Rejoin", f"failed: {e}", parent=d)
        btn(d, "Test webhook", test).pack(anchor="w", padx=10, pady=4)

        head("-- app --")
        checks += [check("start watching when the app opens", "autostart"),
                   check("keep window on top", "on_top")]
        lbl(d, "text colour").pack(anchor="w", padx=10, pady=(4, 0))
        cur = next((k for k, v in COLORS.items() if v.upper() == T["fg"].upper()), "white")
        col = tk.StringVar(value=cur)
        om = tk.OptionMenu(d, col, *COLORS)
        om.configure(bg=BTN, fg=T["fg"], activebackground="#333333", activeforeground=T["fg"], relief="solid", bd=1,
                     font=FONT, highlightthickness=0)
        om["menu"].configure(bg=BTN, fg="#FFFFFF", font=FONT)
        om.pack(anchor="w", padx=10)

        head("-- restart timing (seconds) --")
        ew = entry("restart after an error screen stays up", s.get("error_wait", core.ERROR_WAIT))
        st = entry("restart after no heartbeat for", s.get("stale", core.STALE))

        def save():
            url = wh.get().strip()
            if url and not core.is_webhook(url):
                messagebox.showerror("NovaHop Rejoin", "that doesn't look like a discord webhook url", parent=d)
                return
            try:
                e_w = max(5, int(ew.get())); s_t = max(30, int(st.get()))
            except ValueError:
                messagebox.showerror("NovaHop Rejoin", "timings have to be numbers", parent=d)
                return
            s.update(webhook=url, wh_ping=ping.get().strip(), error_wait=e_w, stale=s_t)
            for key, v in checks:
                s[key] = bool(v.get())
            new = COLORS[col.get()]
            if new.upper() != T["fg"].upper():
                old = T["fg"]
                T["fg"] = new
                s["text_color"] = new
                recolor(root, old, new)
                rows_key[0] = None
            root.attributes("-topmost", bool(s.get("on_top")))
            store.save()
            log("settings saved")
            d.destroy()

        btn(d, "Save", save).pack(anchor="w", padx=10, pady=12)

    # ---------------- accounts
    lbl(root, "accounts:").pack(anchor="w", padx=10, pady=(10, 0))
    acc_box = tk.Frame(root, bg=BG)
    acc_box.pack(fill="x", padx=10)
    rows = {}
    rows_key = [None]

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

    def set_enabled(a, var):
        a["enabled"] = bool(var.get())
        store.save()

    def build_rows():
        for w in acc_box.winfo_children():
            w.destroy()
        rows.clear()
        if not store.accounts:
            lbl(acc_box, "no accounts", fg=DIM).pack(anchor="w")
        for a in store.accounts:
            r = tk.Frame(acc_box, bg=BG)
            r.pack(fill="x", pady=1)
            var = tk.IntVar(value=1 if a.get("enabled", True) else 0)
            tk.Checkbutton(r, variable=var, bg=BG, activebackground=BG, selectcolor=BG, fg=T["fg"],
                           command=lambda a=a, v=var: set_enabled(a, v)).pack(side="left")
            name = lbl(r, a.get("nick") or a["name"], width=18)
            name.pack(side="left")
            name.bind("<Double-Button-1>", lambda e, a=a: rename(a))
            st = lbl(r, "", width=34)
            st.pack(side="left")
            btn(r, "x", lambda a=a: remove(a)).pack(side="right")
            btn(r, "Relaunch", lambda u=a["uid"]: watcher.launch_now(u)).pack(side="right", padx=4)
            rows[a["uid"]] = st

    logbar = tk.Frame(root, bg=BG)
    logbar.pack(fill="x", padx=10, pady=(10, 0))
    lbl(logbar, "log:").pack(side="left")

    def clear_log():
        log_box.configure(state="normal")
        log_box.delete("1.0", "end")
        log_box.configure(state="disabled")
    btn(logbar, "clear", clear_log).pack(side="right")
    log_box = tk.Text(root, bg=BG, fg=DIM, font=FONT, relief="solid", bd=1, height=12,
                      highlightthickness=0, state="disabled")
    log_box.pack(fill="both", expand=True, padx=10, pady=(0, 10))
    shown = [0]

    def color(s):
        if s == "OK":
            return "#00FF00"
        if s in ("off", "not watching", "not started"):
            return DIM
        if any(s.startswith(x) for x in ("loading", "starting", "launching", "waiting", "window closed")):
            return "#FFFF00"
        return "#FF4040"

    def refresh():
        on = watcher.alive
        status_lbl.configure(text="status: running" if on else "status: stopped")
        start_btn.configure(text="Stop" if on else "Start")
        s = store.settings
        game = "bee swarm" if s["place"] == core.BEE_SWARM else f"place {s['place']}"
        info_lbl.configure(text=f"{game} | {'private server' if s.get('ps_raw') else 'public'} | "
                                f"{len(s['hb_dirs'])} hb folder(s)")
        key = ",".join(a["uid"] for a in store.accounts)
        if key != rows_key[0]:
            rows_key[0] = key
            build_rows()
        for a in store.accounts:
            st = rows.get(a["uid"])
            if st is None:
                continue
            text = watcher.status.get(a["uid"], "not started")
            if not a.get("enabled", True):
                text = "off"
            elif not on and not text.startswith("login expired"):
                text = "not watching"
            r = watcher.restarts.get(a["uid"], 0)
            st.configure(text=f"{text} ({r} restarts)" if r else text, fg=color(text))
        with lock:
            new = [t for n, t in lines if n > shown[0]]
            shown[0] = count[0]
        if new:
            log_box.configure(state="normal")
            log_box.insert("end", "\n".join(new) + "\n")
            log_box.see("end")
            log_box.configure(state="disabled")

    def tick():
        refresh()
        root.after(1000, tick)

    log("ready. close all roblox windows before pressing start")
    if store.settings.get("autostart") and store.accounts:
        root.after(1500, toggle)
    tick()
    root.mainloop()


if __name__ == "__main__":
    if len(sys.argv) >= 3 and sys.argv[1] == "--login":
        login_mode(sys.argv[2])
    else:
        app_mode()
