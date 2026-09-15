# Handoff: install wiki-privacy-audit's unattended check via home-manager

This is a self-contained prompt. Paste it into a Claude Code session running in
your NixOS config repo (the separate environment where you build your system config)
— that session has no access to the `agent-skills` repo or this conversation, so
everything it needs to know is below.

---

## Context for the receiving session

`agent-skills` (a separate git repo, not this one) contains a privacy-tooling skill
called `wiki-privacy-audit` that scans an Obsidian vault for leaked PII/secrets. It
ships a script, `skills/wiki-privacy-audit/scripts/cron_check.sh`, meant to run
unattended on a schedule (not from an interactive Claude session). It:

- Takes a vault path and an optional log-file path as arguments
- Shells out to `check_vault_privacy.py` (Python 3, stdlib only) and `scan_diff.py`
  (which itself shells out to `git rev-parse` even for non-git input — needs `git`
  on `PATH` even though the vault itself need not be a git repo)
- Writes one compact JSON line per run to the log file (never the actual matched
  PII/secret text — only labels and file:line locations, confirmed safe to log)
- Best-effort fires a desktop notification via `notify-send` (Linux) if a
  `high`-severity finding exists, falling back to stderr if no notifier is found
- Always exits 0 regardless of findings — the log/notification carry the signal,
  not the process exit code — so this is safe to run under any scheduler without
  special failure handling

It needs `python3`, `git`, `coreutils` (for `date`), and `libnotify` (for
`notify-send`) on its `PATH` at runtime. A plain `systemd.user` service does **not**
inherit a full interactive-shell `PATH` by default, so these must be supplied
explicitly via the service's `Environment`, not assumed.

## Your task

Add a **declarative** home-manager module (not an imperative `~/.config/systemd/user/`
file — the whole point of this handoff is to keep this reproducible via the user's
NixOS/home-manager config) that runs `cron_check.sh` on a `systemd.user` timer.

**Before writing anything, ask the user for:**
1. The absolute path to their local clone of the `agent-skills` repo (so the module
   can reference `<path>/skills/wiki-privacy-audit/scripts/cron_check.sh`)
2. The absolute path to their Obsidian vault
3. Desired schedule (daily / weekly / some other `OnCalendar` expression)
4. Where their home-manager config actually lives (a single `home.nix`? a modules
   directory? flake-based?) — don't assume a layout, find or ask where similar
   `systemd.user.services`/`.timers` entries (if any) already exist in their config
   and follow that convention.

**The module shape** (adapt to their actual file structure and Nix style):

```nix
{ config, pkgs, ... }:

{
  systemd.user.services.wiki-privacy-audit = {
    Unit = {
      Description = "wiki-privacy-audit unattended vault scan";
    };
    Service = {
      Type = "oneshot";
      Environment = [
        "PATH=${pkgs.python3}/bin:${pkgs.git}/bin:${pkgs.coreutils}/bin:${pkgs.libnotify}/bin"
      ];
      ExecStart = "${pkgs.bash}/bin/bash /ABSOLUTE/PATH/TO/agent-skills/skills/wiki-privacy-audit/scripts/cron_check.sh /ABSOLUTE/PATH/TO/vault";
    };
  };

  systemd.user.timers.wiki-privacy-audit = {
    Unit = {
      Description = "Run wiki-privacy-audit on a schedule";
    };
    Timer = {
      OnCalendar = "daily";
      Persistent = true;  # catch up on the next login if the machine was off/asleep
    };
    Install = {
      WantedBy = [ "timers.target" ];
    };
  };
}
```

**Notes for you (the receiving session) to actually verify, not assume:**

- `pkgs.libnotify` provides `notify-send` — confirm this package name is still
  correct for whatever nixpkgs version/channel this config pins.
- `Persistent = true` means a missed run (laptop asleep at the scheduled time) fires
  once the timer's target is next reachable, rather than being silently skipped —
  confirm this is the behavior wanted before assuming it.
- After adding the module and running `home-manager switch`, verify with:
  ```
  systemctl --user list-timers | grep wiki-privacy-audit
  systemctl --user start wiki-privacy-audit.service   # manual trigger to test
  journalctl --user -u wiki-privacy-audit.service      # check it actually ran clean
  ```
  Do not report this done without actually running that verification — a Nix
  module that evaluates cleanly is not the same as a systemd unit that actually
  executes successfully with the right `PATH`.
- If `notify-send` needs a live desktop session's D-Bus address to display anything
  (it does), confirm notifications actually show up when the timer fires while
  logged in — if this is a headless or server-mode NixOS machine, notifications
  will silently no-op (the log file is still the reliable signal either way).

## What this explicitly does NOT do

This only detects and logs/notifies. It does not propose fixes or write anything
back to the Obsidian vault — that requires a live Claude Code session with the
`obsidian-vault` MCP server connected, which a `systemd.user` service cannot provide.
The intended flow is: timer fires → notification (or log-check) → user runs
`/privacy-audit` interactively in a real session to actually deal with any finding.
