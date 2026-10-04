---
name: corral-update
description: Bring Corral Light up to date with its repository and restart the hub safely. Use when asked to update, upgrade or pull Corral, to check whether Corral is current, or after Corral changes were pushed to master and should go live.
---

# Updating Corral Light

`corral-light update` does the whole job: it fetches, fast-forwards the
checkout the running hub serves, reinstalls the adapters only when their
lockfile changed, and restarts the hub only when no other pane is mid-turn
or waiting on a permission. It refuses, changing nothing, when the checkout
has local changes or commits of its own.

1. Look first. This changes nothing:

   ```
   corral-light update --check
   ```

   It says how far behind origin/master the install is, lists the incoming
   commits, and says whether the running hub serves older code than the
   checkout (pulled but never restarted).

2. If it is current and the hub is not stale, say so and stop.

3. Otherwise tell the user what will happen before running it. You are
   probably running inside a Corral pane: the restart ends YOUR turn too.
   The pane comes back paused and resumes on the user's next message. So put
   everything you want the user to read in your reply BEFORE the update
   command, and make the command the last thing you do.

   ```
   corral-light update
   ```

4. Read the exit code:
   - 0: updated (or already current). The hub restart is queued when run
     from a pane.
   - 3: deferred. Other panes are mid-turn or waiting on a permission; the
     output names them. Nothing was changed. Offer `--wait 600` to wait for
     them to settle. Use `--now` only if the user explicitly says to
     interrupt them.
   - 1: refused or failed. Report the reason verbatim. Never "fix" a refusal
     by discarding local changes or commits in the checkout; that is the
     user's call.

Other flags: `--no-restart` pulls without restarting (the hub keeps the old
code until it restarts), `--json` for a machine-readable result,
`--install-timer` / `--remove-timer` for the daily unattended update (it runs
around 05:15 and only acts when the hub is idle).
