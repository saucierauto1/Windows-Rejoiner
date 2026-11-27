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
- Pushing to `main` runs `.github/workflows/build.yml` (windows-latest, Python 3.12, PyInstaller onefile)
  -> `NovaHopRejoin.exe` released as tag `win-v1.0.<run number>`. The run number is written to `app/version.txt`
  and the app's update check compares it with the latest GitHub release, so every push = users get "update available".
  Put `[skip ci]` in commit messages that don't change the app.
- Nothing here can run on Windows/tkinter in a Linux sandbox: test the watcher logic with fakes
  (swap `Watcher.procs/kill/open/now/ticket/resolve/mem/limit`) and the UI with a stand-in tkinter module.

## Files
- `app/main.py` - tkinter UI. Main screen is one Canvas: text drawn straight onto the background picture
  (dark outline when a picture is set), buttons via create_window. Dialogs: game/server, folders, settings
  (scrollable), look (colours, background picture, alpha), per-account server. Tray (pystray), update check,
  restart via PYINSTALLER_RESET_ENVIRONMENT. `--login <file>` = pywebview login child process (reads .ROBLOSECURITY
  and the page's `meta[name=user-data]`). Only redraws text that changed; skips drawing while minimized.
- `app/core.py` - Store (accounts.json with DPAPI-encrypted cookies, settings.json in %APPDATA%), Roblox API
  (auth ticket with CSRF retry, share-link resolve, game names), launching via roblox-player: URI with a
  browserTrackerId per account, ROBLOX_singletonMutex for multi-instance, Discord webhook (events + one live status
  message that is reposted and the old one deleted), executor workspace/autoexec scan, heartbeat install,
  and the Watcher.
- `app/mouselock.py` - hotkey (RegisterHotKey) that locks the cursor to one monitor (ClipCursor re-applied 10x/s).

## Watcher rules (core.Watcher.tick, every 5 s)
- Heartbeat: NovaHop/hb/<UserId>.txt `time|ok|msg|name`, written every 3 s by the executor autoexec line
  (gist saucierauto1/4e760d58e39c190bb537f599d9d788a8). A fresh heartbeat (<30 s) counts as running even if the
  window was lost.
- Window per account: tracker id in the command line, else adopt the first unclaimed Roblox process started
  within 180 s of that account's launch. Never open more windows than enabled accounts. Launch queue with
  launch_delay between launches; boot_wait (180 s) after a launch.
- Restart on: error screen > error_wait (teleport/full-server errors wait hop_wait = 90 s, NovaHop retries
  those itself), no heartbeat > stale (120 s), window gone > 10 s, RAM over ram_limit. After 3 failed tries:
  Discord "stuck" alert, retry every 15 min.
- Performance (all off by default): fps_cap (executor autoexec `novahop_fps.lua` with setfpscap + Roblox
  ClientAppSettings.json DFIntTaskSchedulerTargetFps), low_gfx (allowlisted lowest-graphics flags, also written to
  Bloxstrap/Fishstrap Modifications), ram_trim minutes (K32EmptyWorkingSet), ram_limit MB, eco (below-normal
  priority + EcoQoS + very low memory priority), cpu_cap % (job object hard cap). Flags only affect windows
  opened afterwards; the user's own flags in that file are kept.

## Untested on real hardware
Real Windows runs of: login, multi-instance launch, tray, mouse lock, the performance settings (CPU cap may be
blocked by Roblox's anti-cheat).
