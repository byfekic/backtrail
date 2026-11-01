# Backtrail — User Guide

> **What it is:** Backtrail is a Windows desktop tool that takes point-in-time backups of production folders (local or network), archives them as ZIP or RAR, and tracks exactly **what changed** between backups — added, modified, and deleted files, plus key-level changes inside configuration (`.json`) files. Every backup records **who** took it and **why**, any two backups can be **compared**, any backup can be **restored** (whole or single files, into a folder or over the source with a safety backup first), and old backups can be **pruned** automatically.
>
> **Who it's for:** Anyone who needs a traceable history of a machine's / line's PRODUCTION folder and a record of configuration changes over time.

---

## At a glance

| | |
|---|---|
| **Application** | Backtrail 1.0 (`Backtrail.exe`) |
| **Platform** | Windows 10 / 11 (desktop GUI) |
| **Backup sources** | One or many local or UNC network folders |
| **Archive formats** | ZIP (built-in) or RAR (requires WinRAR) |
| **Integrity** | SHA-256 hash per file (manifest) |
| **Change tracking** | Added / Modified / Deleted list + JSON key-level changes, between consecutive backups or between **any two** |
| **Traceability** | Each backup carries the Windows user, machine, mode (manual / scheduled) and a note, and is **digitally signed** (Ed25519) so that authorship and integrity can be verified |
| **Restore** | Any backup, or selected files of it, into a new folder or over the live source; every file is verified against the signed manifest and a safety backup is taken first |
| **Retention** | Keep the newest N backups and/or the last N days per source |
| **Python required?** | No — the built `.exe` is self-contained |

---

## Key concepts

- **Source** — a PRODUCTION folder you want to back up. Each source has a **Display name**, a **Backup name** (used in file names), and a **PRODUCTION path**.
- **Destination (backup root)** — the folder where Backtrail writes all backups. Each source gets its own sub-folder inside it.
- **Backup** — one run produces three files: the **archive**, a **manifest**, and a **changelog** (see [What each backup produces](#what-each-backup-produces)).
- **Manifest** — a JSON list of every file with its size and SHA-256 hash. Backtrail compares the newest manifest against the previous one to detect changes.
- **Changelog** — a human-readable text report of what changed since the last backup.

---

## Getting started

### 1. Launch Backtrail
Double-click **`Backtrail.exe`**. The main window opens with a dashboard, a list of sources, settings, and an activity log.

> The application folder must be kept together — `Backtrail.exe` needs the `_internal` folder next to it. Copy/move the **whole `Backtrail` folder**, not just the `.exe`.

### 2. Set the destination, archive format and compression
In the **Settings** bar:
1. **Destination** → *Browse* to the folder where backups should be stored (a fast local disk or a reliable network share).
2. **Archive** → choose **ZIP** or **RAR**.
3. **Compression** → **Fast**, **Normal** (default) or **Maximum**.
4. **Parallel** → how many sources to back up at the same time (default **3**).
5. Click **Save**.

> RAR requires **WinRAR** installed on the machine. If WinRAR isn't found, use ZIP.

**Which compression should I pick?** Compression is where nearly all the backup time goes, so this is the main speed/size trade-off:
>
| Setting | Speed | Archive size |
|---|---|---|
| **Fast** | ~2x faster than Normal | ~15–20% larger |
| **Normal** *(default)* | Balanced | Baseline |
| **Maximum** | Slowest | Smallest |
>
Pick **Fast** for large folders you back up often; **Maximum** only when storage is tight.

### 3. Add a backup source
Click **+ Add** and fill in:
- **Display name** — a friendly label (e.g. `Sorter PLC`).
- **Backup name** — used in the backup file names (letters, numbers, `. _ -`; other characters become `_`).
- **PRODUCTION path** — *Browse* to the folder (local drive or `\\server\share\...`).

Click **Save**. The source appears in the list with a status of **● Ready** (reachable) or **● Offline** (not reachable right now).

### 4. Run a backup
- **Note** — type why you are taking this backup (*"before firmware update"*, a ticket number). It is stored with the backup and shown in Version History and in comparisons. Leave it empty for a routine run.
- **RUN ALL BACKUPS NOW** — backs up every configured source.
- **RUN SELECTED** — select a row first, then back up just that source.

Every backup is **signed** with the Windows user and machine that took it (`By:` in the changelog, `user` / `host` in the manifest) and with its mode, *manual* or *scheduled*. The **Last Backup · by** column of the sources table shows the time and the author of the latest run.

Progress and results appear in the **Activity log** at the bottom. A finished run ends with a line like:

```
SUCCESS: +3 ~2 -0 !1
```

meaning **+**added, **~**modified, **-**deleted, and **!**skipped (files that were in use). When several sources run at once, each line is prefixed with the source's backup name (e.g. `[sorter] SUCCESS: …`) so you can tell them apart. The Run buttons stay disabled until the whole run has finished.

---

## Automatic daily backups

Backtrail can back up every configured source automatically, once a day, without anyone opening it.

1. Click **Schedule…** (next to *Remove*).
2. Pick the hour and minute, then click **Enable / Update**.
3. The dialog then shows *"Currently scheduled daily at HH:MM"*.

To stop it, open the same dialog and click **Remove schedule**.

**How it works.** Backtrail registers a Windows Scheduled Task called **`Backtrail Daily Backup`** that launches itself with the `--run` switch. In that mode it runs every source (honouring your Parallel and Compression settings) with **no window at all**, then exits.

- It runs **as you, while you are logged on to this PC** — no administrator rights and no password are needed. If the PC is off or you are logged out at that time, Windows runs the task at the next opportunity.
- Results go to **`backtrail_scheduled.log`**, next to `Backtrail.exe`. Each line is timestamped and tagged with the source, ending with a summary such as `Scheduled run finished: 3 ok, 0 failed.` The file is trimmed automatically so it can't grow without limit.
- Backups made by the schedule are ordinary backups — they appear in the dashboard, Version History and Search History next time you open Backtrail.

> You can also trigger a silent run yourself at any time from a command line or another scheduler:
> ```
> "C:\path\to\Backtrail\Backtrail.exe" --run
> "C:\path\to\Backtrail\Backtrail.exe" --run --note "after sorter firmware 6.2.1"
> ```
> It exits with code `0` if every source succeeded and `1` if any failed, so it slots into other automation. Runs started this way are signed `Mode: scheduled`; `--note` stamps them with a reason like the Note box does in the window.

---

## Closing while a backup is running

If you close Backtrail mid-backup it asks **"A backup is still running. Quit now?"**

- **No** — keeps the window open and the backup carries on.
- **Yes** — the running backups are stopped and their **incomplete archives are deleted**, so a half-written `.zip`/`.rar` is never left behind that could be mistaken for a good backup. No manifest or changelog is written for a cancelled run, so the next backup still compares against the last *complete* one.

---

## The main window

| Area | What it shows |
|---|---|
| **Dashboard cards** | Number of sources, how many are online, total backups taken, changes in the latest backup, and total files indexed. |
| **Sources table** | Each source with its names, path, live **Ready/Offline** status, and last-backup time. |
| **Settings** | Destination (backup root), archive format, compression level, how many sources run in parallel, and the **retention** (keep the newest N backups and/or the last N days, with *Prune now*). |
| **Actions** | The **Note** for this run, Run All / Run Selected. |
| **Activity log** | Live, colour-coded messages: white = info, green = success, amber = warning, red = error. |

### Reviewing history
- **Version History** — a table of every backup (source, when, by whom, mode, signature, note), filterable by source; pick one to read its full changelog. Comparisons and restores are listed too, as `COMPARE` and `RESTORE` rows. The buttons under the table restore the selected backup, verify its archive or open its folder.
- **Search History** — search across **all** changelogs, comparisons and restore reports for a file name, a parameter, or an old/new value (great for answering *"when did this setting change?"*).
- **Compare Backups…** — the changelog between **any two** backups, not just consecutive ones (see [Comparing two backups](#comparing-two-backups)).
- **Restore…** — put a backup back, see [Restoring a backup](#restoring-a-backup).

---

## Comparing two backups

A changelog normally covers one step: the backup against the one before it. **Compare Backups…** covers any distance:

1. Pick the **Source**.
2. Select a **FROM** (older) and a **TO** (newer) backup. Each line shows the date, who took it, the mode and the note.
3. Click **Compare selected**.

Or click **Compare two archive files…** and pick two `.rar` / `.zip` files from anywhere — two of Backtrail's own archives, or the nightly rars the plant makes with its own batch file. When a Backtrail manifest lies next to an archive it is used as is; otherwise the archive is **unpacked into a temporary folder and every file hashed**, which takes about as long as extracting it (RAR needs WinRAR, 7-Zip or the `tar.exe` of Windows 11; ZIP needs nothing). The order does not matter: if the newer backup is given first, the two are swapped.

The result is written into the source's backup folder as

```
<from stamp>_to_<to stamp>_COMPARE.txt
```

(for two archive files picked by hand it lands next to the newer archive, with the source name in the file name)

and shown in the dialog. It has the usual sections plus a header naming both backups, their authors and notes, and **BACKUP RUNS IN BETWEEN** — every backup taken after FROM up to TO, with who took it, its mode, its `+ ~ -` counts and its note. That is the answer to *"what happened between the backup before the shutdown and the one after, and who did it?"*

Comparisons are never counted as backups and are never removed by the retention.

---

## Digital signatures

Every backup is **digitally signed**, so "By: alice" is not just a claim: anyone with the backups can check that the manifest and the archive are exactly what that person's Backtrail wrote.

**How it works.**
- On first use Backtrail creates an **Ed25519 key pair** for the Windows user on that machine, in `%APPDATA%\Backtrail\keys\<user@host>.key` (private, never leaves the profile) and `.pub` (public).
- Each backup's manifest gets a `_meta.signature` block: the signer, the key id (a fingerprint), the public key, the SHA-256 of the archive, the time, and the signature over the metadata, every file hash and that archive hash. The changelog header shows `Signature: ed25519 key <id> by <user@host>`.
- The public key is published into `<Destination>\_keys\<user@host>.pub`, the **trust store**. A different key already registered for the same signer is never overwritten.

**Checking.** Version History marks every backup:

| Mark | Meaning |
|---|---|
| ✔ signed | The signature verifies and the key is the one the trust store holds for that signer. |
| ✖ INVALID | The manifest was changed after signing (any field, any file hash, the author claim), the key id does not match, or the archive is missing. |
| ? untrusted key | The signature verifies, but the trust store has no key for that signer, or a different one (a reinstalled PC, or someone signing in another person's name). |
| unsigned | A the original tool backup, or signing switched off. |
| ? unverified | The `cryptography` package is missing on this machine, so nothing can be checked. |

That quick check reads the manifest only. **Verify archive of selected** (Version History) re-hashes the archive and compares it with the signed value — the deep check, which takes about as long as reading the archive. Comparisons show the mark of both sides in their header.

From a command line or a scheduler, `Backtrail.exe --verify` (optionally followed by a backup root or one source folder) runs the deep check on every backup, writes the report to `backtrail_verify.log` next to the exe, and exits with `1` if any backup is INVALID or untrusted.

**When a key changes.** After a Windows reinstall the same user gets a new key, and the old `.pub` in `_keys` makes the new backups *untrusted*: delete that `.pub` (or replace it with the new one from `%APPDATA%\Backtrail\keys`) once you have confirmed the change is legitimate. Treat an unexpected *untrusted key* or *INVALID* as a reason to ask who touched the backup folder.

Signing needs the `cryptography` package, which `build.bat` installs; without it Backtrail still takes backups, unsigned, and says so in the activity log. `"sign": false` in `config.json` switches signing off deliberately.

---

## Retention

Backups are kept forever unless you say otherwise. In **Settings → Retention**, set **keep the newest N backups** and/or **the last N days** per source (0 = no limit), then **Save**. The rule is applied at the end of every backup run and when you click **Prune now**; each removal is logged with the backup's date and author. A pruned backup loses its archive, manifest and changelog together. The **newest complete backup of a source is never removed**, because the next run needs it to detect changes.

---

## What each backup produces

For every run, Backtrail writes to `…\<Destination>\<BackupName>\`:

| File | Purpose |
|---|---|
| `YYYY-MM-DD_HH-MM-SS_<name>_PRODUCTION.zip` (or `.rar`) | The actual archive of the folder. |
| `YYYY-MM-DD_HH-MM-SS_<name>_MANIFEST.json` | `{"_meta": {...}, "files": {...}}` — SHA-256 + size of every file under `files`, used for change detection; under `_meta` the date, source, **user**, **host**, **mode**, **note**, archive name, the previous backup and the **digital signature** (see below). (Manifests written by the original tool are a bare file map and are still read.) |
| `YYYY-MM-DD_HH-MM-SS_<name>_CHANGELOG.txt` | Human-readable report of what changed since the previous backup. |
| `<from>_to_<to>_COMPARE.txt` | Written by **Compare Backups…**: the changelog between two chosen backups. |
| `<when>_from_<stamp>_RESTORE.txt` | Written by **Restore**: which files of which backup were put where, by whom, and the safety backup that was taken. |

The timestamp makes every backup uniquely named and naturally sorted by date.

---

## Reading a changelog

A changelog starts with a header: `Date`, `Name`, `Source`, **`By`** (user @ machine), **`Mode`** (manual / scheduled), **`Note`** (if one was given), `Archive` and `Previous` (the backup it is compared with). Then these sections:

- **ADDED** — files that are new since the previous backup (`+ path`).
- **DELETED** — files that disappeared (`- path`).
- **MODIFIED** — files whose contents changed. Each shows the old and new size + SHA-256.
- **CONFIG (JSON) CHANGES** — for modified `.json` files, the exact keys that changed:
  - `+ $.key: value` — a key was added
  - `- $.key: value` — a key was removed
  - `~ $.key: old -> new` — a value changed
- **NOT BACKED UP (FILE IN USE / UNREADABLE)** — files that were locked/open and could not be read. The backup still completes; these files are simply listed so you know they were skipped.
- **SUMMARY** — counts of added / modified / deleted / unchanged.

> **By design, Backtrail does not store line-by-line text diffs.** File-level changes (size + hash) are always tracked, and for `.json` files you get precise key-level changes. For a full line-by-line comparison of other text files, use a dedicated diff tool (e.g. WinMerge) on the two archived versions.

---

## Restoring a backup

Any backup can be put back from inside Backtrail: all of it or just the files you pick, into a new folder or straight over the live PRODUCTION folder.

**Where to start**
- **Restore…** in the main window (the newest backup of the selected source is preselected),
- **Restore…** under the table in **Version History** (the selected backup),
- **Restore changed files from FROM…** in **Compare Backups…** (the FROM backup, with the files that differ between FROM and TO already selected — the usual *"one CSV went wrong"* case).

**The dialog**
1. **Backup**: switch to any other backup of the same source from the dropdown; its signature state is shown at the bottom.
2. **Files**: every file of the backup with its size and, after a moment, how it compares with the live source: *identical*, *differs (would be replaced)* or *missing in the source (would be added)*. Type in the filter box to narrow the list; **Select all** / **Select changed** / **Clear selection** do what they say. Selecting every file is a full restore.
3. **Restore to**:
   - *A new folder* (default `<Destination>\_restore\<stamp>_<name>`) — nothing on the plant is touched. Use this to inspect a version or to copy files over by hand.
   - *Over the live source folder* — Backtrail first takes a **signed safety backup** of the source with the note shown (default `safety backup before restore of <stamp>`), then writes the files. Retention is not applied during that safety run, so the backup you are restoring cannot be pruned away by it.
   - **Exact restore** (full restore over the source only): files in the source that the backup does not have are removed too. Otherwise extra files are left alone.
4. **▶ RESTORE**. A restore over the source asks for confirmation and states how many files will be replaced, added, left unchanged and, for an exact restore, removed.

**What is guaranteed**
- Every file is checked against the SHA-256 in the signed manifest **before** anything is written. If a single file of the archive does not match, the restore is refused and nothing changes. A backup without a manifest (the original tool without one, or a deleted manifest) can still be restored, but is reported as *not verified*.
- Files that are already identical are left untouched, so their timestamps stay.
- A backup whose signature is **INVALID** or **untrusted** can be restored, but only after an explicit extra confirmation.
- Each restore writes `<when>_from_<stamp>_RESTORE.txt` beside the backups: the backup, its signature state, who restored it, where, which files, and the name of the safety backup. It appears in Version History as a `RESTORE` row and is searchable.
- The next regular backup's changelog shows exactly what the restore changed, relative to the safety backup.

> A restore over the source while a backup run is in progress is refused; wait for the run to finish.

---

## Good practices

- **Run before and after any planned change** so the changelog captures exactly what you did.
- **Keep the Destination on reliable storage** with enough space for the retained history.
- **Close/stop applications that lock files** before backing up if you need those files captured — otherwise they appear under *NOT BACKED UP*.
- **Use meaningful Backup names** — they become the folder and file names.

---

## Performance

Backtrail is multi-threaded and does most of its work in parallel:

- Files are **hashed in parallel** across CPU cores.
- The **archive is built at the same time as the scan**, not after it, so a run costs roughly as long as the compression alone.
- Only the handful of `.json` files that actually changed are read back from the previous archive (it no longer unpacks the whole thing every run).
- **Several sources are backed up at the same time** (the **Parallel** setting), so *Run All* across many folders takes roughly the time of the slowest few rather than the sum of all of them. If many sources live on the same slow share or disk, lower **Parallel** to 1–2 so they don't compete for bandwidth.

Because of that, **compression is the remaining bottleneck** — if backups feel slow, switch **Compression** to **Fast** before anything else. On a ~200 MB / 1,500-file folder that takes a run from roughly 6.5 s to 2.5 s.

Other factors: backing up over a slow network share is limited by the link, not by Backtrail; and antivirus on-access scanning of the destination folder can slow archive writing noticeably.

---

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| A source shows **● Offline** | The path (often a `\\server\share`) is unreachable. Check network/VPN and permissions. Status refreshes automatically. |
| App feels slow to refresh status | It is contacting an unreachable network path. Status checks run in the background and time out on their own; the window stays usable. |
| **"RAR selected but WinRAR/Rar.exe was not found"** | Install WinRAR, or switch Archive to **ZIP**, or set the correct `rar_path` (see Configuration). |
| A run reports **"Source unavailable"** | The PRODUCTION path was offline when the backup started. Reconnect and run again. |
| Some files appear under **NOT BACKED UP** | Those files were in use/locked during the run. Close the app holding them and re-run, or accept that live-locked files are skipped. |
| No backups listed on the dashboard | Check that the **Destination** is set correctly and reachable, and that at least one backup has run. |
| Backups take too long | Set **Compression** to **Fast** (see [Performance](#performance)). Also check whether the source or destination is on a slow network share, and whether antivirus is scanning the destination folder. |
| The daily backup didn't run | It only runs while you are logged on to that PC. Check `backtrail_scheduled.log` next to `Backtrail.exe`, and confirm the task **Backtrail Daily Backup** still exists in Windows *Task Scheduler*. |
| Scheduled run logged errors | Open `backtrail_scheduled.log` — each failure is tagged with its source, e.g. `[sorter] ERROR: Source unavailable`. Usually the share was offline at that hour. |
| The app was moved to a new folder | The scheduled task still points at the old path. Open **Schedule…** and click **Enable / Update** to re-point it. |

---

## Configuration (advanced)

Backtrail keeps two small files **next to `Backtrail.exe`**:

- **`config.json`** — settings and sources:
  ```json
  {
    "backup_root": "D:\\Backtrail\\backups",
    "archive_format": "zip",
    "compress_level": 6,
    "parallel_backups": 3,
    "schedule_time": "20:00",
    "workers": 0,
    "rar_path": "C:\\Program Files\\WinRAR\\Rar.exe",
    "locations": [
      { "name": "Sorter PLC", "backup_name": "sorter", "path": "\\\\server\\line1\\PRODUCTION" }
    ]
  }
  ```
  | Key | Meaning |
  |---|---|
  | `compress_level` | ZIP compression 0–9. The **Compression** dropdown sets 1 (Fast), 6 (Normal) or 9 (Maximum). |
  | `keep_last` / `keep_days` | Retention per source: keep the newest N backups / the last N days (0 = unlimited). Applied after each run and by **Prune now**. |
  | `sign` | `false` switches the digital signature off (default: on). |
  | `keys_dir` | Where the signing key pair lives (default `%APPDATA%\Backtrail\keys`). |
  | `parallel_backups` | How many sources are backed up simultaneously (the **Parallel** dropdown). Sources that share a backup name are always run one after the other. |
  | `schedule_time` | The time shown in the **Schedule…** dialog. The actual trigger lives in the Windows task `Backtrail Daily Backup`. |
  | `workers` | Threads used for hashing. `0` = automatic (based on CPU count, shared out across the parallel sources). Lower it only if the tool is saturating a slow network share. |
- **`state.json`** — remembers the last backup time, author, mode, note and changelog per source (managed by the app; no need to edit).

You can edit `config.json` by hand for bulk changes, but the in-app **+ Add / Edit / Remove** buttons are the normal way to manage sources.

---

## For maintainers — building the executable

The tool is a small Python (Tkinter) application built with **PyInstaller**.

- **Source files:** `backtrail.py` (UI) and `backup_engine.py` (backup/compare/retention logic).
- **Run from source:** `run.bat` (needs Python 3 with Tkinter, which is included in standard Windows Python).
- **Tests:** `python -m pytest -q tests` — runs the engine on copies of the demo folder with ZIP archives; no WinRAR needed.
- **Build the exe:** run `build.bat`. Output: **`dist\Backtrail\`** — distribute this whole folder.
- **Icon:** `Backtrail.ico`.

> **Why a folder build (one-dir) and not a single `.exe`?**
> The real-time antivirus on the build/target environment flags PyInstaller's single-file self-extractor as a false positive and removes the `.exe`. The one-dir build (a small launcher plus an `_internal` folder) is not flagged and runs normally. If a single portable `.exe` is ever required, it would need the antivirus team to allow-list it (or code-signing).

---

## FAQ

**Does it back up open/locked files?**
It backs up everything it can read. Any file locked by another process is skipped and listed under *NOT BACKED UP* in the changelog — the rest of the backup still completes.

**Can it back up network shares?**
Yes. Use a UNC path (`\\server\share\...`). If the share is offline, the source simply shows *Offline* and can't be backed up until it's reachable.

**How much history does it keep?**
Every run is retained as its own timestamped archive + manifest + changelog until the **Retention** setting removes it (keep the newest N and/or the last N days; off by default). Plan Destination disk space accordingly.

**Who took a backup, and why?**
Open **Version History**: every line shows the date, the Windows user, the mode and the note. The same facts are in the changelog header (`By:`, `Mode:`, `Note:`) and in the manifest's `_meta`.

**Can I see what changed between two backups that are weeks apart?**
Yes — **Compare Backups…**, or pick two archive files directly, even ones not made by Backtrail.

**Is anything sent anywhere / to the cloud?**
No. Backtrail only reads the source folders and writes to the Destination you configure. It's entirely local/on-network.

**Can it run unattended / as a service?**
It runs daily via the built-in **Schedule…** feature while you're logged on. It is not a Windows service, so it won't run on a logged-out or powered-off PC — for 24/7 unattended operation it should be installed on a machine that stays logged on.

**Where do I see what the automatic backups did?**
`backtrail_scheduled.log` next to `Backtrail.exe`, or simply open Backtrail — scheduled backups show up in the dashboard and Version History like any other.
