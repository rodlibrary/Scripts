# Speech to text (transcription)

Transcribe audio and video files on UNI's High Performance Computing (HPC) system with OpenAI's Whisper.

| File | What it does | Runs on |
|---|---|---|
| [`whisperx.script`](whisperx.script) | WhisperX: transcription **with speaker labels** (who said what) | HPC (SLURM) |
| [`whisper.script`](whisper.script) | Whisper: transcription without speaker labels; simpler setup | HPC (SLURM) |

Use **WhisperX** for interviews and oral histories where speakers matter. Use **Whisper** when you only need the text, or don't want to set up a Hugging Face account.

Both scripts transcribe every audio/video file in an `audio` folder, several at once, and write the results to `audio/transcripts/`. Subfolders aren't read, so put all files directly in `audio/`.

---

## whisperx.script

### 1. Get a Hugging Face token (once)

Speaker labeling uses a model on Hugging Face that requires you to accept its terms.

1. Create a [Hugging Face](https://huggingface.co/) account.
2. Open [pyannote/speaker-diarization-community-1](https://huggingface.co/pyannote/speaker-diarization-community-1) and accept the user conditions.
3. Go to **Profile > Access Tokens > Create new token**:
   - Name: `Transcription`
   - Repositories: *Read access to contents of all public gated repos you can access*
4. Click **Create** and copy the token.

### 2. Install WhisperX on the HPC (once)

```bash
export TMPDIR="/home/$USER/data/tmp"
mkdir -p $TMPDIR
module load python3.11
python -m venv pve
pve/bin/pip install -U whisperx
```

### 3. Edit the script

Download `whisperx.script` and edit these lines:

| Setting | Change to |
|---|---|
| `export HF_TOKEN="insert token here"` | Your Hugging Face token |
| `MODEL="large"` | Optional: `tiny`, `base`, `small` or `medium` for faster, less accurate results |

> **Edit only the copy you upload to the HPC.** Never commit a version containing your token to GitHub, because this repository is public.

The script assumes English (`--language en`). For other languages, change that line.

### 4. Run

1. Create an `audio` folder in your HPC directory.
2. Upload the edited `whisperx.script` to your HPC directory.
3. Upload your files (`.mp3 .wav .m4a .aac .flac .mp4 .mkv .mov`) to `audio/`.
4. Open Terminal and enter:

   ```bash
   sbatch whisperx.script
   ```

**Output:** transcripts (text, subtitles and JSON with speaker labels) in `audio/transcripts/`; a log per file in `audio/logs/`.

---

## whisper.script

### 1. Install Whisper on the HPC (once)

```bash
export TMPDIR="/home/$USER/data/tmp"
mkdir -p $TMPDIR
module load python3.11
python -m venv pve
pve/bin/pip install -U openai-whisper
```

### 2. Run

1. Create an `audio` folder in your HPC directory.
2. Upload `whisper.script` to your HPC directory. It finds your environment at `/data/$USER/pve` automatically; to use a smaller model, change `MODEL_SIZE="large"`.
3. Upload your files (`.mp3 .wav .m4a .aac .flac .ogg .wma .mp4 .mkv .mov .webm`) to `audio/`.
4. Open Terminal and enter:

   ```bash
   sbatch whisper.script
   ```

**Output:** transcripts in `audio/transcripts/`.
