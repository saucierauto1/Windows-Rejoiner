# NovaHop Rejoin for Windows

Keeps your Roblox accounts farming with NovaHop. When an account gets an error screen (279, kicks, key system),
freezes, or its Roblox window closes, only that account is closed and launched back into the game.
Several accounts at once, no clone setup.

## Download

Get `NovaHopRejoin.exe` from the **Releases** page of this repo.
Windows may say "Windows protected your PC": click **More info**, then **Run anyway**.
If your antivirus deletes the exe, download `NovaHopRejoin-folder.zip` instead, unzip it anywhere and run
`NovaHopRejoin.exe` inside the folder (antivirus flags the folder version far less often).

If something goes wrong, Settings > **Open log folder** has the log of the last few days to send on Discord.

## Setup

1. Close all Roblox windows.
2. Open NovaHopRejoin.exe and click **+ Add account**. Log in to Roblox in the window that opens.
   Repeat for every account.
3. Optional: **Game / server** to pick another game or paste a private server link (default: Bee Swarm Simulator).
4. Click **START**. Accounts launch one by one.
5. Attach your executor to each Roblox window as usual. The app puts the NovaHop heartbeat into your
   executor's autoexec folder by itself. If an account never turns **OK**, open **Folders** and
   pick your executor's `workspace` folder.

## Your logins

Logins are saved encrypted with your Windows account, on your PC only
(`%APPDATA%\NovaHopRejoin`). They are only ever sent to roblox.com. Removing an account deletes its login.
Never send anyone the files in that folder.

## Building

Every push to `main` builds the .exe with GitHub Actions and publishes it as a release.
