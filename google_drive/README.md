# Google Drive to archives (rclone)

Copy Google Drive folders to archival storage on UNI's High Performance Computing (HPC) system with [rclone](https://rclone.org/), keeping each file's modification date and recording its creation date and other Drive metadata in a manifest.

Use this when Google Takeout isn't an option and rclone can't be run on a local computer. Everything except signing in to Google happens on the HPC.

> **Check first.** Confirm with IT and the University Archives that running this transfer on the HPC is approved. Keep that approval with the collection's documentation.

---

## 1. Install rclone on the HPC (once)

First check whether the HPC already provides it:

```bash
module avail rclone
```

If it's listed, `module load rclone` and skip to step 2. Otherwise, install it in your home directory (no admin rights needed):

```bash
mkdir -p ~/bin && cd ~/bin
curl -O https://downloads.rclone.org/rclone-current-linux-amd64.zip
unzip -j rclone-current-linux-amd64.zip '*/rclone'
chmod +x rclone
~/bin/rclone version
```

Folder dates are copied only by **rclone 1.66 or newer**, so check the version.

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

A Workspace administrator creates a service account with domain-wide delegation and gives you its key file. No browser sign-in is needed, and the token won't expire partway through a long job. Add these options to the `rclone` commands below:

```bash
--drive-service-account-file /data/$USER/gdrive/key.json --drive-impersonate owner@uni.edu
```

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

---

## 3. Set up folders

```bash
mkdir -p /data/$USER/gdrive/{staging,manifests,logs}
cd /data/$USER/gdrive
```

Create `folders.txt` with one Drive folder path per line, exactly as `rclone lsd` shows them:

```text
Department Records/2019
Department Records/2020
Photographs
```

Each line becomes one task in the job, so a large Drive is split into pieces that can be retried separately.

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

# RCLONE -> rclone command ("rclone" if loaded as a module, else "$HOME/bin/rclone")
RCLONE="$HOME/bin/rclone"

# DEST   -> where copies go (use project/data storage, not scratch)
DEST="/data/$USER/gdrive/staging"

######################################
## No need to modify anything below ##
######################################

export RCLONE_CONFIG="$HOME/.config/rclone/rclone.conf"
FOLDER=$(sed -n "${SLURM_ARRAY_TASK_ID}p" folders.txt)
[ -n "$FOLDER" ] || { echo "No line ${SLURM_ARRAY_TASK_ID} in folders.txt"; exit 1; }
SAFE_NAME=$(echo "$FOLDER" | tr '/ ' '__')

# 1. Manifest: created and modified dates, owner, MIME type, MD5 and Drive ID
"$RCLONE" lsjson -R --metadata --hash "gdrive:${FOLDER}" \
    > "manifests/${SAFE_NAME}.json"

# 2. Copy (never "sync", which deletes files at the destination)
"$RCLONE" copy "gdrive:${FOLDER}" "${DEST}/${FOLDER}" \
    --drive-export-formats docx,xlsx,pptx,pdf \
    --metadata \
    --transfers 4 --checkers 8 --tpslimit 10 \
    --log-file "logs/${SAFE_NAME}.log" --log-level INFO

# 3. Check: a dry run should find nothing left to copy
"$RCLONE" copy "gdrive:${FOLDER}" "${DEST}/${FOLDER}" \
    --drive-export-formats docx,xlsx,pptx,pdf \
    --dry-run --log-file "logs/${SAFE_NAME}_verify.log" --log-level INFO
```

| Setting | Change to |
|---|---|
| `#SBATCH --array=1-3%2` | `1-` the number of lines in `folders.txt` (`wc -l folders.txt`). Keep `%2`: more parallel tasks mostly cause Google rate-limit errors, not speed |
| `RCLONE=` | `rclone` if you used `module load rclone`; also add `module load rclone` above this line |
| `DEST=` | Optional: a different destination |

If you're using a service account, add the two `--drive-service-account-file` / `--drive-impersonate` options to all three `rclone` commands.

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
