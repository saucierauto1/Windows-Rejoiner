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

    w = webview.create_window("Log in to Roblox  ·  NovaHop Rejoin", "https://www.roblox.com/login",
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
    import customtkinter as ctk
    from tkinter import filedialog, messagebox

    BG = "#0F0F14"; CARD = "#1B1B25"; CARD2 = "#2A2A38"; ACCENT = "#A855F7"; ACCENT_H = "#9333EA"
    TEXT = "#F1F1F5"; MUTED = "#9A9AAD"; GREEN = "#34D399"; YELLOW = "#FBBF24"; RED = "#EF4444"; RED_H = "#DC2626"

    ctk.set_appearance_mode("dark")
    store = core.Store()
    lines = []          # (number, text)
    lock = threading.Lock()
    count = [0]

    def log(msg):
        with lock:
            count[0] += 1
            lines.append((count[0], time.strftime("[%H:%M:%S] ") + msg))
            del lines[:-300]

    watcher = core.Watcher(store, log)

    root = ctk.CTk(fg_color=BG)
    root.title("NovaHop Rejoin")
    root.geometry("780x820")
    root.minsize(640, 640)
    try:
        root.iconbitmap(os.path.join(getattr(sys, "_MEIPASS", os.path.dirname(__file__)), "icon.ico"))
    except Exception:
        pass

    def F(size, bold=False):
        return ctk.CTkFont(family="Segoe UI", size=size, weight="bold" if bold else "normal")

    def button(parent, text, cmd, color=CARD2, hover="#3A3A4C", h=44, size=14):
        return ctk.CTkButton(parent, text=text, command=cmd, fg_color=color, hover_color=hover, height=h,
                             corner_radius=12, font=F(size, True), text_color=TEXT)

    body = ctk.CTkFrame(root, fg_color=BG)
    body.pack(fill="both", expand=True, padx=20, pady=18)

    # header
    ctk.CTkLabel(body, text="NovaHop Rejoin", font=F(28, True), text_color=TEXT).pack(anchor="w")
    status_row = ctk.CTkFrame(body, fg_color=BG)
    status_row.pack(anchor="w", pady=(2, 0))
    dot = ctk.CTkLabel(status_row, text="●", font=F(16), text_color=MUTED)
    dot.pack(side="left")
    status_lbl = ctk.CTkLabel(status_row, text="Stopped", font=F(15, True), text_color=TEXT)
    status_lbl.pack(side="left", padx=(6, 0))
    info_lbl = ctk.CTkLabel(body, text="", font=F(13), text_color=MUTED)
    info_lbl.pack(anchor="w")

    def toggle():
        if watcher.alive:
            watcher.stop()
        else:
            if not store.accounts:
                messagebox.showinfo("NovaHop Rejoin", "Add an account first.")
                return
            watcher.start()
        refresh()

    start_btn = button(body, "START", toggle, ACCENT, ACCENT_H, h=64, size=20)
    start_btn.pack(fill="x", pady=(16, 10))

    actions = ctk.CTkFrame(body, fg_color=BG)
    actions.pack(fill="x")
    for i in range(3):
        actions.grid_columnconfigure(i, weight=1, uniform="a")

    # ---------------- add account
    def add_account():
        add_btn.configure(state="disabled", text="Log in in the new window…")
        out = os.path.join(tempfile.gettempdir(), f"nhr_login_{os.getpid()}_{int(time.time())}.tmp")
        if getattr(sys, "frozen", False):
            cmd = [sys.executable, "--login", out]
        else:
            cmd = [sys.executable, os.path.abspath(__file__), "--login", out]

        def run():
            try:
                subprocess.run(cmd)
                if not os.path.exists(out):
                    log("Login window closed without logging in")
                    return
                with open(out, encoding="utf-8") as f:
                    cookie = core.unprotect(f.read())
                os.remove(out)
                uid, name, display = core.whoami(cookie)
                store.add(uid, name, display, cookie)
                log(f"Added {name} ({uid})")
            except Exception as e:
                log(f"Couldn't add the account: {e}")
            finally:
                try:
                    os.remove(out)
                except OSError:
                    pass
                root.after(0, lambda: add_btn.configure(state="normal", text="+  Add account"))

        threading.Thread(target=run, daemon=True).start()

    add_btn = button(actions, "+  Add account", add_account, h=48)
    add_btn.grid(row=0, column=0, sticky="ew", padx=(0, 6))

    # ---------------- game / private server
    def game_dialog():
        d = ctk.CTkToplevel(root, fg_color=BG)
        d.title("Game and private server")
        d.geometry("520x380")
        d.transient(root)
        d.grab_set()
        fr = ctk.CTkFrame(d, fg_color=BG)
        fr.pack(fill="both", expand=True, padx=20, pady=16)

        def field(label, value, hint):
            ctk.CTkLabel(fr, text=label, font=F(13, True), text_color=TEXT).pack(anchor="w", pady=(8, 2))
            e = ctk.CTkEntry(fr, height=40, corner_radius=10, fg_color=CARD, border_color=CARD2,
                             placeholder_text=hint, font=F(13))
            e.pack(fill="x")
            if value:
                e.insert(0, value)
            return e

        s = store.settings
        g = field("Game (empty = Bee Swarm Simulator)", "" if s["place"] == core.BEE_SWARM else s["place"],
                  "Game link or place ID")
        p = field("Private server link (empty = public servers)", s.get("ps_raw", ""), "Paste the private server link")
        dl = field("Seconds between launching accounts", str(s.get("launch_delay", 12)), "12")

        def save():
            place = core.parse_game(g.get())
            ps = core.parse_ps(p.get())
            try:
                delay = max(5, int(dl.get() or 12))
            except ValueError:
                delay = None
            if place is None or ps is None or delay is None:
                messagebox.showerror("NovaHop Rejoin", "Didn't understand that. Nothing was changed.", parent=d)
                return
            s.update(place=place, ps_raw=p.get().strip(), launch_delay=delay)
            store.save()
            log(f"Saved: place {place}, private server: {'yes' if ps[0] else 'no'}")
            d.destroy()

        button(fr, "Save", save, ACCENT, ACCENT_H, h=46).pack(fill="x", pady=(18, 0))

    button(actions, "Game / server", game_dialog, h=48).grid(row=0, column=1, sticky="ew", padx=6)

    # ---------------- heartbeat folders
    def folders_dialog():
        d = ctk.CTkToplevel(root, fg_color=BG)
        d.title("Heartbeat folders")
        d.geometry("640x460")
        d.transient(root)
        d.grab_set()
        fr = ctk.CTkFrame(d, fg_color=BG)
        fr.pack(fill="both", expand=True, padx=20, pady=16)
        ctk.CTkLabel(fr, text="Where the executor writes NovaHop/hb", font=F(15, True), text_color=TEXT).pack(anchor="w")
        ctk.CTkLabel(fr, text="Normally found by itself. If not, pick your executor's workspace folder.",
                     font=F(12), text_color=MUTED).pack(anchor="w")
        lst = ctk.CTkScrollableFrame(fr, fg_color=CARD, corner_radius=12, height=220)
        lst.pack(fill="both", expand=True, pady=10)

        def draw():
            for w in lst.winfo_children():
                w.destroy()
            dirs = store.settings["hb_dirs"]
            if not dirs:
                ctk.CTkLabel(lst, text="None yet", text_color=MUTED, font=F(13)).pack(anchor="w", padx=8, pady=6)
            for path in dirs:
                row = ctk.CTkFrame(lst, fg_color=CARD2, corner_radius=10)
                row.pack(fill="x", pady=3, padx=4)
                ctk.CTkLabel(row, text=path, font=F(12), text_color=TEXT, anchor="w").pack(side="left", padx=10, pady=8, fill="x", expand=True)
                ctk.CTkButton(row, text="Remove", width=70, height=28, fg_color=CARD, hover_color=RED_H,
                              command=lambda p=path: (store.settings["hb_dirs"].remove(p), store.save(), draw())).pack(side="right", padx=6)

        def scan():
            scan_btn.configure(state="disabled", text="Scanning…")

            def run():
                watcher.rescan()
                root.after(0, lambda: (draw(), scan_btn.configure(state="normal", text="Scan again")))
            threading.Thread(target=run, daemon=True).start()

        def pick():
            path = filedialog.askdirectory(parent=d, title="Pick your executor's workspace folder")
            if not path:
                return
            path = os.path.normpath(path)
            hb = path if path.lower().endswith(os.path.join("novahop", "hb")) else os.path.join(path, "NovaHop", "hb")
            os.makedirs(hb, exist_ok=True)
            if hb not in store.settings["hb_dirs"]:
                store.settings["hb_dirs"].append(hb)
                store.save()
                log(f"Watching {hb}")
            parent = os.path.dirname(path)
            ax = [os.path.join(parent, n) for n in os.listdir(parent)
                  if os.path.isdir(os.path.join(parent, n)) and n.lower().startswith("autoexec")]
            for a in core.install_heartbeat(ax):
                log(f"Heartbeat is in {a}")
            draw()

        row = ctk.CTkFrame(fr, fg_color=BG)
        row.pack(fill="x")
        row.grid_columnconfigure(0, weight=1, uniform="b")
        row.grid_columnconfigure(1, weight=1, uniform="b")
        scan_btn = button(row, "Scan again", scan)
        scan_btn.grid(row=0, column=0, sticky="ew", padx=(0, 6))
        button(row, "Pick folder…", pick, ACCENT, ACCENT_H).grid(row=0, column=1, sticky="ew", padx=(6, 0))
        draw()

    button(actions, "Folders", folders_dialog, h=48).grid(row=0, column=2, sticky="ew", padx=(6, 0))

    # ---------------- accounts
    ctk.CTkLabel(body, text="ACCOUNTS", font=F(12, True), text_color=MUTED).pack(anchor="w", pady=(18, 4))
    acc_box = ctk.CTkScrollableFrame(body, fg_color=BG, height=250)
    acc_box.pack(fill="both", expand=True)
    cards = {}
    cards_key = [None]

    def remove(a):
        if messagebox.askyesno("NovaHop Rejoin", f"Remove {a['name']}? Its saved login is deleted from this PC."):
            store.remove(a["uid"])

    def set_enabled(a, var):
        a["enabled"] = bool(var.get())
        store.save()

    def build_cards():
        for w in acc_box.winfo_children():
            w.destroy()
        cards.clear()
        if not store.accounts:
            ctk.CTkLabel(acc_box, text="No accounts yet. Click  + Add account  and log in.", font=F(13),
                         text_color=MUTED).pack(anchor="w", pady=8)
        for a in store.accounts:
            c = ctk.CTkFrame(acc_box, fg_color=CARD, corner_radius=14)
            c.pack(fill="x", pady=5)
            left = ctk.CTkFrame(c, fg_color=CARD)
            left.pack(side="left", fill="x", expand=True, padx=14, pady=10)
            title = a.get("display") or a["name"]
            ctk.CTkLabel(left, text=title, font=F(16, True), text_color=TEXT).pack(anchor="w")
            sub = ctk.CTkLabel(left, text="", font=F(12), text_color=MUTED)
            sub.pack(anchor="w")
            st = ctk.CTkLabel(left, text="", font=F(13, True), text_color=MUTED)
            st.pack(anchor="w")
            right = ctk.CTkFrame(c, fg_color=CARD)
            right.pack(side="right", padx=10)
            var = ctk.IntVar(value=1 if a.get("enabled", True) else 0)
            ctk.CTkSwitch(right, text="", variable=var, width=46, progress_color=ACCENT,
                          command=lambda a=a, v=var: set_enabled(a, v)).pack(side="left", padx=(0, 6))
            ctk.CTkButton(right, text="Relaunch", width=86, height=32, corner_radius=10, fg_color=CARD2,
                          hover_color="#3A3A4C", font=F(12, True),
                          command=lambda u=a["uid"]: watcher.launch_now(u)).pack(side="left", padx=4)
            ctk.CTkButton(right, text="✕", width=34, height=32, corner_radius=10, fg_color=CARD2,
                          hover_color=RED_H, font=F(13, True), command=lambda a=a: remove(a)).pack(side="left")
            cards[a["uid"]] = (sub, st)

    ctk.CTkLabel(body, text="LOG", font=F(12, True), text_color=MUTED).pack(anchor="w", pady=(12, 4))
    log_box = ctk.CTkTextbox(body, height=150, fg_color=CARD, corner_radius=12, font=ctk.CTkFont("Consolas", 12),
                             text_color=MUTED)
    log_box.pack(fill="x")
    shown = [0]

    def color(s):
        if s == "OK":
            return GREEN
        if s == "off":
            return MUTED
        if any(s.startswith(x) for x in ("loading", "starting", "launching", "waiting", "window closed")):
            return YELLOW
        return RED

    def refresh():
        on = watcher.alive
        dot.configure(text_color=GREEN if on else MUTED)
        status_lbl.configure(text="Running" if on else "Stopped")
        start_btn.configure(text="STOP" if on else "START", fg_color=RED if on else ACCENT,
                            hover_color=RED_H if on else ACCENT_H)
        s = store.settings
        game = "Bee Swarm Simulator" if s["place"] == core.BEE_SWARM else f"Place {s['place']}"
        info_lbl.configure(text=f"{game}  ·  {'private server' if s.get('ps_raw') else 'public servers'}"
                                f"  ·  {len(s['hb_dirs'])} heartbeat folder(s)")
        key = ",".join(a["uid"] for a in store.accounts)
        if key != cards_key[0]:
            cards_key[0] = key
            build_cards()
        for a in store.accounts:
            if a["uid"] in cards:
                sub, st = cards[a["uid"]]
                text = watcher.status.get(a["uid"], "off" if not a.get("enabled", True) else "not started")
                if not on and text not in ("off",) and not text.startswith("login expired"):
                    text = "not watching" if a.get("enabled", True) else "off"
                sub.configure(text=f"@{a['name']}  ·  restarts: {watcher.restarts.get(a['uid'], 0)}")
                st.configure(text=text, text_color=color(text) if on else MUTED)
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

    log("Ready. Close all Roblox windows before pressing START so several accounts can run at once.")
    tick()
    root.mainloop()


if __name__ == "__main__":
    if len(sys.argv) >= 3 and sys.argv[1] == "--login":
        login_mode(sys.argv[2])
    else:
        app_mode()
