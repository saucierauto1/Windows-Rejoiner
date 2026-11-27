# NovaHop Rejoin for Windows

Auto-rejoiner for Bee Swarm Simulator (Roblox) alts, made for NovaHop script users. Owner: Simon (saucierauto1).
Discord: https://discord.gg/brfJ2ZtegE. Android version: saucierauto1/Emulator-Rejoin.

## How to work with the owner
- Do exactly what is asked, minimum change for "fix". No refactoring, renaming or "improvements" that weren't asked for.
- No extra error handling, logging, comments, docstrings or tests unless asked. No new libraries unless asked.
- Never put Co-Authored-By, Claude-Session or "Generated with Claude" lines in commits or pull requests:
  the owner doesn't want Claude listed as a contributor.
- Short answers. Owner is not a programmer: explain in plain words, no jargon.
- Look: plain and basic, like a hobby project ("like I coded it myself"). Pure black background, white text fields,
  lowercase labels. The login window title must not mention NovaHop. No screenshot feature (antivirus flags it).

## Build / release
- Pushing to `main` runs `.github/workflows/build.yml` (windows-latest, Python 3.12, PyInstaller 6.11.1, `--noupx`,
  version info from `version_info.txt` made in the workflow) -> `NovaHopRejoin.exe` (onefile) and
  `NovaHopRejoin-folder.zip` (onedir, fewer antivirus false positives), released as tag `win-v1.0.<run number>`.
  The run number is written to `app/version.txt` and the app's update check compares it with the latest GitHub
  release, so every push = users get "update available". Put `[skip ci]` in commit messages that don't change the
  app; for a multi-commit change use `[skip ci]` on all but the last commit so users get one update.
- Testing: in a Linux sandbox nothing Windows runs, so test the watcher with fakes (swap
  `Watcher.procs/kill/open/now/ticket/resolve/mem/limit/is_updating/notify`, heartbeat files with `os.utime`) and the UI
  with a stand-in tkinter module. On a real Windows PC run the real Tk window with `core.DATA_DIR` pointed at a temp
  folder and `core.apply_fps` / `core.set_autorun` / `core.auth_ticket` stubbed, so the PC's own Roblox flags,
  startup list and Roblox account are never touched.

## Files
- `app/main.py` - started with no arguments it is the **supervisor**: holds the `Local\NovaHopRejoinApp` mutex
  (a second copy writes `show.flag` in the data folder and exits; the running copy sees it and shows its window),
  then runs itself with `--child` (the real app window) and runs it again with `--resume` if it crashes (Tk can kill
  the whole process on "Tk_GetPixmap ... CreateDIBSection" when RAM is low; `watching.flag` = it was watching, so it
  starts watching again). Tk's own fatal-error box would keep the dead child alive, so the child sets
  `Tcl_SetPanicProc` (`_exit_on_tk_panic`): log the message, exit code 3. Tested with a real `Tcl_Panic` on Tk 9.0.
  Exit code 0 = quit, 5 = reopen (new look). More than 5 crashes in 10 min = give up.
  The child: tkinter UI, main screen is one Canvas (text drawn onto the background picture with a 1 px shadow,
  picture never bigger than the screen), buttons via create_window. Dialogs: game/server, folders, settings
  (scrollable, all-or-nothing save, performance preset dropdown, export/import, open log folder), look,
  per-account server. Tray (pystray), update check. Log file `%APPDATA%\NovaHopRejoin\logs\rejoin-YYYY-MM-DD.txt`
  (4 days kept). `--login <file>` = pywebview login child process (reads .ROBLOSECURITY and the page's
  `meta[name=user-data]`). Only redraws text that changed (every 1.5 s); skips drawing while minimized.
- `app/core.py` - Store (accounts.json with DPAPI-encrypted cookies, settings.json in %APPDATA%; export/import:
  logins only decrypt on the same Windows account, otherwise the account gets `relogin` and must be added again),
  Roblox API (auth ticket with CSRF retry + 429 retry and plain-language errors, share-link resolve, game names
  looked up in the background), launching via roblox-player: URI with a browserTrackerId per account,
  ROBLOX_singletonMutex for multi-instance, Discord webhook (events + one live status message that is reposted and
  the old one deleted), executor workspace/autoexec scan, heartbeat install, Roblox log reader (`RobloxLogs`),
  window helpers (shrink/minimize without activating, hide/show), autorun (HKCU Run key), and the Watcher.
  ctypes calls go through private `_k`/`_u` WinDLLs with 64-bit argtypes/restype (never change `ctypes.windll`).
- `app/mouselock.py` - hotkeys (RegisterHotKey): `MouseLock` locks the cursor to one monitor (ClipCursor re-applied
  10x/s), `Hotkey` runs a function (hide / show all Roblox windows).

## Watcher rules (core.Watcher.tick, every 5 s)
- Heartbeat: NovaHop/hb/<UserId>.txt `time|ok|msg|name`, written every 3 s by the executor autoexec line
  (gist saucierauto1/4e760d58e39c190bb537f599d9d788a8). Its age comes from the file's modified time (PC clock), not
  the time inside it (executor clocks can be off). A file caught half written keeps the last good read. A fresh
  heartbeat (<30 s) counts as running even if the window was lost.
- Window per account (`_adopt`, under `Watcher.lock`): tracker id in the command line; a window with an account's
  tracker is never given to another account; other new windows go to the accounts waiting for one, oldest launch
  first, within 180 s of their launch. One account never gets a second window (launch refuses while its window is
  open, except "launched by hand", which closes the old one first). Every automatic launch checks the cap: never
  more windows than enabled accounts (windows of disabled accounts don't count). `kill` waits for the window to close.
  Launch queue with launch_delay between launches; boot_wait (180 s) after a launch. Each Start gets a new `gen`,
  so threads from an earlier Start stop by themselves. Window owners, launch times and hidden windows are saved in
  `windows.json` (pid + create time) and restored on the next start, so a crash/restart of the app window keeps them.
  If an account needs a new window but every slot is taken by windows the app can't tell apart (opened before the
  app), it is NOT restarted (that would loop): status "can't tell which roblox window...", logged once.
- Restart on: error screen > error_wait (teleport/full-server errors, `HOPPING` incl. codes 769-773, wait hop_wait =
  90 s and the timer starts again when NovaHop moves to a different hop error, but one error spell never lasts more
  than 5 x hop_wait), no heartbeat > stale (120 s), window
  gone > 10 s, RAM over ram_limit (3 quick tries, then a Discord alert and once per 15 min). After 3 failed tries:
  Discord "stuck" alert, retry every 15 min. Every restart is logged with its reason and the Discord message has
  the reason and the game name. While Roblox's installer/updater has been running for over 30 s, launches and
  "window closed" restarts wait (a normal launch passes through RobloxPlayerLauncher in seconds).
- Roblox logs (`read_logs`, off by default): reads new lines of `%LOCALAPPDATA%\Roblox\logs\*_Player_*.log`
  (only (account, event, time, code) is kept, never the lines). Disconnect codes count as an error even without
  the executor (a fresh ok heartbeat wins); 264/273 (same account elsewhere) and 285 (left on purpose) put the
  account on hold (no rejoin until Launch is pressed or it joins again); a recent teleport stops "no heartbeat"
  restarts for hop_wait. The log line formats are guesses from Bloxstrap's watcher: check them on a real PC.
- Performance (all off by default): fps_cap (executor autoexec `novahop_fps.lua` from `fps_lua()`: caps 5 s after
  the game loaded, uncaps to 240 on LocalPlayer.OnTeleport so hops load at full speed, caps again on a failed
  teleport; autoexec runs again on the new server. Roblox's own DFIntTaskSchedulerTargetFps is only written when no
  autoexec folder could be written, because it caps loading too), low_gfx (allowlisted lowest-graphics flags, also written to
  Bloxstrap/Fishstrap Modifications), ram_trim minutes (K32EmptyWorkingSet, only accounts with status OK so a
  loading / hopping window isn't slowed down), ram_limit MB, eco (below-normal
  priority + EcoQoS + very low memory priority), cpu_cap % (job object hard cap; tried once per window, job handles
  closed when the window closes). Flags are written again before every launch (a Roblox update makes a new
  folder); `flag_backup` remembers the user's own values, which are put back when a setting is turned off.
  Other options: shrink (640x400), launch_min, hide hotkey (hide_on / hide_key), win_start, read_logs.

## Untested on real hardware
Real Windows runs of: login, multi-instance launch, tray, mouse lock, hide hotkey, shrink, start with Windows,
the Roblox log formats (guessed), the update detection, Tk 8.6 (the build's Python 3.12; local tests ran Tk 9.0),
and the performance settings with real Roblox (CPU cap may be blocked by Roblox's anti-cheat). Tested on a real PC
with stand-ins: CPU cap, efficiency mode, RAM trim and RAM display on a dummy process; flag write + restore on real
Roblox/Bloxstrap files; every dialog in the real Tk window; supervisor crash restart and single copy, also in a
locally built onefile exe.
