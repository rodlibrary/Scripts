# Google Drive to archives (rclone)

Copy Google Drive folders to archival storage on UNI's High Performance Computing (HPC) system with [rclone](https://rclone.org/), keeping each file's modification date and recording its creation date and other Drive metadata in a manifest.

Use this when Google Takeout isn't an option and rclone can't be run on a local computer. Everything except signing in to Google happens on the HPC.

> **Check first.** Confirm with IT and the University Archives that running this transfer on the HPC is approved. Keep that approval with the collection's documentation.

Working from Windows? Each step below can be done with WinSCP; see [Using WinSCP](#using-winscp-windows).

---

## 1. Install rclone on the HPC (once)

Install it into your Python environment in `/data/$USER/pve`, like the other HPC scripts in this repository:

```bash
python -m venv /data/$USER/pve
/data/$USER/pve/bin/pip install rclone-bin
/data/$USER/pve/bin/rclone version
```

Use **`rclone-bin`**, not `rclone`: the pip package named `rclone` doesn't include the rclone program. [`rclone-bin`](https://pypi.org/project/rclone-bin/) repackages the official rclone releases for pip. It isn't made by the rclone project; if you'd rather use the official download, see [rclone.org/install](https://rclone.org/install/) and change `RCLONE=` in the job script to wherever you put it.

Folder dates are copied only by **rclone 1.66 or newer**, so check the version.

So the `rclone` commands below work as typed, add the environment to your path. This lasts only until you log out:

```bash
export PATH="/data/$USER/pve/bin:$PATH"
```

To make it permanent, so `rclone` works in every new session, add it to your login settings once:

```bash
echo 'export PATH="/data/$USER/pve/bin:$PATH"' >> ~/.bashrc
source ~/.bashrc
```

The job script doesn't need either step, because it uses the full path `/data/$USER/pve/bin/rclone`.

**If you get `rclone: command not found`:** run the `export` line above (or add it to `~/.bashrc`), then `rclone version`. If it still isn't found, check that the program is where pip put it:

```bash
ls -l /data/$USER/pve/bin/rclone
```

If that file is missing, the install didn't finish; run `/data/$USER/pve/bin/pip install rclone-bin` again.

---

## 2. Connect rclone to Google Drive (once)

Pick **one** of these. Option A is quickest; option B is better for large or ongoing transfers because it doesn't depend on a personal sign-in.

### Option A: Sign in through an SSH tunnel

This lets you sign in with your browser while rclone itself runs only on the HPC.

1. Connect to the HPC with port forwarding:

   ```bash
   ssh -L 53682:localhost:53682 <your catID>@<HPC login address>
   ```

2. Run `rclone config` and answer:

   | Prompt | Answer |
   |---|---|
   | New remote / name | `n`, then `gdrive` |
   | Storage | `drive` |
   | client_id / client_secret | Your own OAuth client (see below), or leave blank |
   | scope | `drive.readonly` (read-only is enough for copying) |
   | Use auto config? | `y` |

3. rclone prints a link. Open it in your browser and sign in with the Google account that can see the folders. Google sends you back to `localhost:53682`, which the tunnel passes to the HPC.

**Recommended:** create your own OAuth client ID in [Google Cloud Console](https://console.cloud.google.com/) ([rclone instructions](https://rclone.org/drive/#making-your-own-client-id)). rclone's built-in client is shared by everyone who uses rclone and is heavily rate-limited.

### Option B: Service account (Google Workspace admin needed)

A Workspace administrator creates a service account with domain-wide delegation and gives you its key file. No browser sign-in is needed, and the token won't expire partway through a long job. Add these options to the `rclone` commands below, naming the person whose Drive you're copying (in the job script, put them in `DRIVE_OPTS`):

```bash
--drive-service-account-file /data/$USER/gdrive/key.json --drive-impersonate owner@uni.edu
```

See [Other staff members' Drives](#other-staff-members-drives) for when to use this.

### Protect your credentials

`~/.config/rclone/rclone.conf` (and `key.json`, if you use one) gives access to the Drive account.

```bash
chmod 600 ~/.config/rclone/rclone.conf
```

> **Never commit `rclone.conf` or a key file to GitHub.** This repository is public.

### Test the connection

```bash
rclone lsd gdrive:
```

You should see your top-level Drive folders. For a **shared drive**, add `--drive-team-drive <ID>` to every command (the ID is the last part of the shared drive's URL). For files **shared with you**, add `--drive-shared-with-me`.

### Other staff members' Drives

rclone never asks for anyone's Google password. Passwords are typed only on Google's own sign-in page; rclone keeps the resulting access token in `rclone.conf`, or uses a service account's key file.

To copy folders from other staff members on campus, use one of these. In every case, **never ask staff for their passwords**, and make sure the transfer is covered by your records schedule or transfer agreement with that office.

| | How | Best for |
|---|---|---|
| **1. Staff share folders with you** | Staff share the folder with your account (**Viewer** is enough). You sign in as yourself | A few offices, one-time transfers. Simplest; no admin needed |
| **2. Staff sign in themselves** | A separate remote for each person; they complete Google's sign-in | Copying everything a person owns, when sharing isn't practical |
| **3. Service account** | IT sets up read-only, domain-wide access; you name the person to copy from | Many staff or ongoing transfers |

#### 1. Folders shared with you

1. Ask each staff member to share their folder with your Google account as **Viewer**.
2. Sign in as yourself (Option A above). The folders appear under "Shared with me":

   ```bash
   rclone lsd gdrive: --drive-shared-with-me
   ```

3. Add `--drive-shared-with-me` to every `rclone` command you type, including the `folders.txt` and export checks in step 3.
4. In the job script, set `DRIVE_OPTS=(--drive-shared-with-me)`.

The original owner and creation and modified dates are still recorded in the manifest. You get only files you've been given access to: if some files inside a shared folder were restricted separately, they're skipped, and the log shows them.

#### 2. Each staff member signs in

1. In your tunnel session (Option A), run `rclone config` and create a new remote named for the person, such as `gdrive-jsmith`.
2. When the sign-in link appears, have that person open it and sign in themselves.
3. Use that name in place of `gdrive` in your commands (`rclone lsd gdrive-jsmith:`) and set `REMOTE="gdrive-jsmith"` in the job script.
4. When their transfer is finished and checked, remove their access:

   ```bash
   rclone config delete gdrive-jsmith
   ```

   They can also revoke it at [myaccount.google.com/permissions](https://myaccount.google.com/permissions).

While the remote exists, your `rclone.conf` gives full read access to their Drive, so keep it protected (`chmod 600`).

#### 3. Service account

Ask IT (Google Workspace administrators) for a service account with domain-wide delegation, limited to the `drive.readonly` scope. It can read any campus account, so it needs their approval. Store the key as described in Option B, then name the person you're copying from:

```bash
rclone lsd gdrive: --drive-service-account-file /data/$USER/gdrive/key.json --drive-impersonate jsmith@uni.edu
```

In the job script, set:

```bash
DRIVE_OPTS=(--drive-service-account-file "/data/$USER/gdrive/key.json" --drive-impersonate jsmith@uni.edu)
```

#### Keeping each person's files separate

Use a separate folder list and job for each staff member, so folders with the same name (two people's `Photographs`, say) don't mix. In a copy of the job script, set for example:

```bash
FOLDERS="folders_jsmith.txt"
PERSON="jsmith"
```

Their copies then go to `staging/jsmith/`, and their manifests and logs start with `jsmith__`.

---

## 3. Set up folders

```bash
mkdir -p /data/$USER/gdrive/{staging,manifests,logs}
cd /data/$USER/gdrive
```

### folders.txt

Create `folders.txt` with one Drive folder per line. Each line becomes one task in the job, so a large Drive is split into pieces that can be retried separately. Example:

```text
Department Records/2019
Department Records/2020
Department Records/Committee Minutes
Photographs
Shared Projects/Oral History Project
```

| Rule | Why |
|---|---|
| Write paths from the top of the Drive, exactly as `rclone lsd gdrive:` shows them, with `/` between folders and no leading or trailing `/` | rclone looks for the folder at exactly that path |
| Leave spaces as they are; no quotes or escape characters | The script adds the quoting |
| Match spelling and capitals exactly | Otherwise rclone won't find the folder |
| No blank lines | Each line number is a task number, so a blank line becomes a task with nothing to copy |
| All folders from one place: your Drive, one shared drive, or "shared with me" | Each needs different rclone options, so use a separate list and job for each |

The number of lines sets the `--array` range in the job script (five lines: `--array=1-5%2`). Count them with:

```bash
wc -l folders.txt
```

**Generate the list instead of typing it.** One line for each top-level folder in the Drive:

```bash
rclone lsf gdrive: --dirs-only | sed 's:/$::' > folders.txt
```

For subfolders, list inside a parent folder and add the parent's name back to each line:

```bash
rclone lsf "gdrive:Department Records" --dirs-only | sed 's:/$::; s:^:Department Records/:' >> folders.txt
```

Open the file afterward and delete any folders you don't want copied.

### Check for files that can't be exported

Google Docs, Sheets, Slides and Drawings are converted to Office/PDF files. **Google Forms, Sites, My Maps and Jamboards can't be exported by rclone.** List them before you start so they can be handled another way:

```bash
rclone lsjson -R "gdrive:Photographs" | grep -E 'google-apps\.(form|site|map|jam)'
```

---

## 4. The job script

Save this as `gdrive_copy.script` in `/data/$USER/gdrive/`:

```bash
#!/usr/bin/env bash
#SBATCH --job-name=gdrive_copy
#SBATCH --output=logs/%x_%A_%a.out
#SBATCH --error=logs/%x_%A_%a.err
#SBATCH --time=24:00:00
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=4
#SBATCH --mem=8G
#SBATCH --array=1-3%2
# --array: 1 to the number of lines in folders.txt; %2 = at most 2 folders at once.
# If the HPC has a data transfer partition, add:  #SBATCH --partition=<name>

cd "/data/$USER/gdrive" || exit 1

# RCLONE -> The full path to rclone in your python
#           virtual environment

RCLONE="/data/$USER/pve/bin/rclone"

# DEST   -> where copies go (use project/data storage, not scratch)
DEST="/data/$USER/gdrive/staging"

# FOLDERS -> the list of Drive folders to copy
FOLDERS="folders.txt"

# REMOTE -> the rclone remote name from "rclone config"
REMOTE="gdrive"

# PERSON -> optional short name for whose Drive this is (e.g. jsmith).
#           Copies go to DEST/PERSON, and manifests and logs start with it.
PERSON=""

# DRIVE_OPTS -> where the folders are. Leave empty for your own Drive.
#   Shared with you:  DRIVE_OPTS=(--drive-shared-with-me)
#   Service account:  DRIVE_OPTS=(--drive-service-account-file "/data/$USER/gdrive/key.json" --drive-impersonate jsmith@uni.edu)
#   Shared drive:     DRIVE_OPTS=(--drive-team-drive <ID>)
DRIVE_OPTS=()

######################################
## No need to modify anything below ##
######################################

export RCLONE_CONFIG="$HOME/.config/rclone/rclone.conf"
FOLDER=$(sed -n "${SLURM_ARRAY_TASK_ID}p" "$FOLDERS")
[ -n "$FOLDER" ] || { echo "No line ${SLURM_ARRAY_TASK_ID} in $FOLDERS"; exit 1; }
SAFE_NAME="${PERSON:+${PERSON}__}$(echo "$FOLDER" | tr '/ ' '__')"
OUT="${DEST}${PERSON:+/$PERSON}/${FOLDER}"

# 1. Manifest: created and modified dates, owner, MIME type, MD5 and Drive ID
"$RCLONE" lsjson -R --metadata --hash "${DRIVE_OPTS[@]}" "${REMOTE}:${FOLDER}" \
    > "manifests/${SAFE_NAME}.json"

# 2. Copy (never "sync", which deletes files at the destination)
"$RCLONE" copy "${REMOTE}:${FOLDER}" "$OUT" "${DRIVE_OPTS[@]}" \
    --drive-export-formats docx,xlsx,pptx,pdf \
    --metadata \
    --transfers 4 --checkers 8 --tpslimit 10 \
    --log-file "logs/${SAFE_NAME}.log" --log-level INFO

# 3. Check: a dry run should find nothing left to copy
"$RCLONE" copy "${REMOTE}:${FOLDER}" "$OUT" "${DRIVE_OPTS[@]}" \
    --drive-export-formats docx,xlsx,pptx,pdf \
    --dry-run --log-file "logs/${SAFE_NAME}_verify.log" --log-level INFO
```

| Setting | Change to |
|---|---|
| `#SBATCH --array=1-3%2` | `1-` the number of lines in `folders.txt` (`wc -l folders.txt`). Keep `%2`: more parallel tasks mostly cause Google rate-limit errors, not speed |
| `RCLONE=` | Only if rclone isn't in `/data/$USER/pve` |
| `DEST=` | Optional: a different destination |
| `FOLDERS=` | Optional: a different list, e.g. `folders_jsmith.txt` |
| `REMOTE=` | Only if you named the remote something other than `gdrive` (e.g. `gdrive-jsmith`) |
| `PERSON=` | When copying another staff member's folders, a short name such as `jsmith` |
| `DRIVE_OPTS=` | For folders shared with you, a service account or a shared drive; see [Other staff members' Drives](#other-staff-members-drives) |

---

## 5. Run

```bash
cd /data/$USER/gdrive
sbatch gdrive_copy.script
squeue -u $USER
```

**Output:**

| Folder | Contents |
|---|---|
| `staging/` | The copied files, in the same folder structure as Drive |
| `manifests/` | One JSON file per Drive folder with every file's metadata |
| `logs/` | rclone logs per folder, plus SLURM output |

If a task fails (for example, a rate-limit error), resubmit just that task; rclone skips files already copied:

```bash
sbatch --array=2 gdrive_copy.script
```

---

## Using WinSCP (Windows)

[WinSCP](https://winscp.net/) moves files between your computer and the HPC and can run commands there. One step, signing in to Google, needs an interactive terminal, which WinSCP doesn't provide; use Windows Terminal or PuTTY for that.

### Connect

1. Open WinSCP. In **Login**, choose **New Site**.
2. **File protocol:** `SFTP`. **Host name:** the HPC login address. **User name:** your catID.
3. Click **Save**, then **Login**.

### Run commands

**Commands > Open Terminal** (`Ctrl+T`) runs one command at a time on the HPC and shows the output. Use it for the install commands in step 1, the `mkdir` in step 3, and `sbatch` / `squeue` in step 5. It can't run programs that ask questions, such as `rclone config`.

### Sign in to Google (step 2, Option A)

Use either of these. Both create the SSH tunnel so the browser sign-in reaches rclone on the HPC.

- **Windows Terminal** (built into Windows 10 and 11): open it and run the `ssh -L 53682:localhost:53682 ...` command from step 2, then continue with `rclone config`.
- **PuTTY** ([putty.org](https://www.putty.org/)):
  1. In **Session**, enter the HPC login address.
  2. Go to **Connection > SSH > Tunnels**. **Source port:** `53682`. **Destination:** `localhost:53682`. Leave **Local** selected and click **Add**.
  3. Back in **Session**, type a name under **Saved Sessions** and click **Save** so you only do this once.
  4. Click **Open**, log in, and run the `export PATH=...` and `rclone config` commands from step 1 and 2.

If you use a service account (Option B) instead, no sign-in is needed: upload `key.json` to `/data/<your catID>/gdrive/` with WinSCP, then in **Open Terminal** run `chmod 600 /data/$USER/gdrive/key.json`.

### Upload the folder list and job script

1. In WinSCP's right-hand (HPC) panel, go to `/data/<your catID>/gdrive/`.
2. Create `folders.txt` and `gdrive_copy.script` on your computer (Notepad is fine) and drag them across, or use **Files > New > File** to create them directly on the HPC.
3. Windows text files use different line endings from Linux, and `sbatch` refuses scripts that have them. In **Open Terminal**, run:

   ```bash
   cd /data/$USER/gdrive && sed -i 's/\r$//' gdrive_copy.script folders.txt
   ```

### Submit and check progress

In **Open Terminal**:

```bash
cd /data/$USER/gdrive && sbatch gdrive_copy.script
squeue -u $USER
```

Open `logs/` in WinSCP and double-click a log to read it. Press `Ctrl+R` to refresh the file list.

### Download the copies (optional)

If the archive copy goes to a Windows computer or network drive rather than staying on the HPC, change these settings **before** downloading, or WinSCP can alter the files:

1. **Options > Preferences > Transfer > Edit** (the **Default** preset).
2. **Transfer mode:** **Binary**. The default (Automatic) rewrites line endings in text files such as `.txt`, `.html` and `.xml`, which changes their checksums.
3. **Preserve timestamp:** checked (it is by default). If **Including directories** is shown, check it too.
4. Click **OK**.

Then drag the folder from `staging/` in the right-hand panel to the destination in the left-hand panel. Download `manifests/` and `logs/` with it.

Windows sets each downloaded file's **Created** date to the day you download it; the original creation date is in the manifest. Run the [checksums](../checksums/) script on the downloaded copy to record fixity.

---

## Timestamps

| Date | Kept? | How |
|---|---|---|
| File modified date | Yes, automatically | Set from Drive's "last modified" date, including exported Google Docs/Sheets/Slides. Don't add `--no-update-modtime` |
| Folder modified date | Yes, rclone 1.66+ | Automatic in recent versions |
| File created date | In the manifest only | Linux file systems don't let any program set a file's creation date, so it's recorded as `createdTime` in `manifests/*.json` |

**Check them:** the verify step in the script (`*_verify.log`) should report no files to transfer, because rclone compares size and modified date. Exported Google Docs/Sheets/Slides may still be listed, because Drive doesn't report their size until they're exported; anything else listed needs a closer look. To spot-check by hand:

```bash
rclone lsl "gdrive:Photographs" | head
ls -l --time-style=full-iso staging/Photographs | head
```

**Keep them after the transfer.** Ordinary `cp` resets dates. When moving files from `staging/` to their final location, use `rsync -a`, `cp -p` or `tar`. BagIt and zip tools generally keep modified dates, but check the tool's settings. Don't stage on scratch storage: files keep their original (old) dates and may be removed by automatic cleanup.

---

## Fixity

- **Uploaded files** (PDFs, images, etc.) have MD5 hashes in Drive, recorded in the manifest. Compare them with `rclone check "gdrive:Photographs" staging/Photographs --one-way --drive-export-formats docx,xlsx,pptx,pdf`. Exported Google files show up as having no hash to compare; that's expected.
- **Exported Google files** have no hash in Drive, so nothing can be compared. Generate checksums for the local copies after the transfer, for example with the [checksums](../checksums/) script or `rclone sha256sum staging/Photographs > Photographs.sha256`.

---

## Notes

- **Daily limit:** Google allows about 10 TB of downloads per account per day.
- **Shortcuts** in Drive are links, not files. The files they point to are copied only if they're inside a listed folder.
- **Alternative:** if UNI's Globus subscription includes the Google Drive connector, Globus can do the same transfer from a web page with built-in integrity checks. Ask Research Computing.
