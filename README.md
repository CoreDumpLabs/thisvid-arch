# thisvid-arch

Scrape and download your ThisVid favorite videos or your own uploaded videos.

```bash
# Download all your uploaded videos
python3 thisvid-arch.py --self

# Download all your favorites
python3 thisvid-arch.py --fav
```

Please read the [LICENSE](LICENSE) file for your rights and the licensor's rights.

## Requirements

- Python 3.8+
- `requests`, `python-dotenv`, `yt-dlp`, `camoufox`, `playwright==1.50.0` (all installed automatically via pip)

## Installation

```bash
# Create a virtualenv, install dependencies, and Camoufox:
make install
source .venv/bin/activate
```

Or manually:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python -m camoufox fetch
```

### CAPTCHA login

ThisVid currently presents a Cloudflare Turnstile challenge during login. The
script uses Camoufox in its Linux virtual-display mode. It first waits for
automatic verification; if Turnstile displays its checkbox, it clicks the
rendered widget and waits for the issued token. No desktop session is required:

```bash
sudo apt install xvfb
python3 thisvid-arch.py --fav
```

## Tip: use a terminal multiplexer

Downloads can take a long time. Running inside [tmux](https://github.com/tmux/tmux) or [GNU screen](https://www.gnu.org/software/screen/) prevents losing progress if your terminal or SSH session disconnects.

## Setup

Create an `env` file in the same directory (see `env.template`):

```
THISVID_USERNAME=your_username
THISVID_PASSWORD=your_password
```

Credentials can also be passed at runtime with `--username` / `--password`.

---

## Usage

```
python3 thisvid-arch.py --fav | --self [OPTIONS]
```

One of `--fav` or `--self` is always required.

---

## Default behaviour

**This app downloads by default.** Just run it — it will fetch the video listing (or reuse a cached one) and download everything automatically. You don't need to think about phases or steps unless you want to opt out of something.

## Modes

| Flag | Behaviour |
|---|---|
| `--fav` | Scrape and download your favorite videos |
| `--self` | Scrape and download your own uploaded videos |

Both modes scrape the listing, download every video, and fetch comments — all interleaved per video. Use the flags below to opt out of individual steps.

| Flag | Description |
|---|---|
| `--probe` | Force re-scrape the listing and save it — no video downloads |
| `--no-download` | Fetch the listing if needed, but skip downloading |
| `--manifest PATH` | Use this `.tsv` or `.json` as the video list instead of scraping or the cached listing |
| `--no-comments` | Skip comment fetching |
| `--no-manifest` | Skip writing the listing TSV/JSON entirely |
| `--download-only PATH` | Load a previously saved `.tsv` or `.json` and download without scraping |

**Cached listings:** on first run, the listing is scraped and saved as `<username>_favorites.tsv/.json` (for `--fav`) or `<username>_videos.tsv/.json` (for `--self`). Subsequent runs load from that file automatically. Use `--probe` to force a fresh scrape, or `--manifest PATH` to use a different file entirely (e.g. a subset of videos to re-download).

---

## Output files

### Video listing (root directory)

| Mode | Default TSV | Default JSON |
|---|---|---|
| `--fav` | `<username>_favorites.tsv` | `<username>_favorites.json` |
| `--self` | `<username>_videos.tsv` | `<username>_videos.json` |

Both formats are written by default. Use these flags to customise:

| Flag | Effect |
|---|---|
| `--tsv PATH` | Write TSV to PATH instead of the default name |
| `--json PATH` | Write JSON to PATH instead of the default name |
| `--no-tsv` | Skip TSV output |
| `--no-json` | Skip JSON output |
| `--no-manifest` | Skip all listing output |

If you write the manifest to a non-default path via `--tsv`/`--json`, you must use `--manifest` on subsequent runs — auto-detection only finds the default filenames.

### Downloaded videos

| Mode | Default directory |
|---|---|
| `--fav` | `<username>_favs/` |
| `--self` | `<username>_videos/` |

Override with `--output-dir DIR`.

### Comments

Written inside the video download directory:

| Mode | Default files |
|---|---|
| `--fav` | `<username>_favs/<username>_comments.tsv/json` |
| `--self` | `<username>_videos/<username>_comments.tsv/json` |

Both formats are written by default. Use these flags to customise:

| Flag | Effect |
|---|---|
| `--comments-tsv PATH` | Write comments TSV to PATH instead of the default name |
| `--comments-json PATH` | Write comments JSON to PATH instead of the default name |
| `--no-comments-tsv` | Skip comments TSV output |
| `--no-comments-json` | Skip comments JSON output |
| `--no-comments` | Skip comment fetching entirely |

These flags are per-invocation — specify them on whichever run performs the download phase.

---

## Listing fields

| Field | Description |
|---|---|
| `id` | Numeric video ID |
| `url` | Video page URL |
| `title` | Video title |
| `thumbnail` | Thumbnail image URL |
| `rating` | Rating percentage (e.g. `98%`) |
| `views` | View count |
| `favorites` | Number of times favorited |
| `comments` | Comment count |
| `date_added` | When you added it to favorites (or uploaded, for `--self`) |
| `visibility` | `public` or `private` |
| `duration` | ISO 8601 duration (e.g. `PT12M34S`) — populated during download |
| `upload_date` | Upload date — populated during download |
| `category` | Video category — populated during download |
| `tags` | Comma-separated tags — populated during download |
| `description` | Video description — populated during download |

The last five fields are fetched from each video's page during download and written to the manifest in a second pass after all downloads complete.

---

## Comment fields

| Field | Description |
|---|---|
| `video_id` | Numeric ID of the video |
| `comment_id` | Numeric comment ID |
| `user_id` | Numeric ID of the commenter |
| `username` | Commenter's username |
| `date` | When the comment was posted |
| `rating` | Comment like/dislike score |
| `text` | Comment text (emoticons rendered as `:name:`) |

---

## Download options

| Flag | Description |
|---|---|
| `--output-dir DIR` | Override the default output directory |
| `--resume` | Resume from last completed download (re-downloads last video, then continues) |
| `--from N` | Start at video N (1-indexed) |
| `--to N` | Stop after video N (1-indexed) |
| `--dry-run` | Fetch only the first 3 videos |
| `--no-warnings` | Pass `--no-warnings` to yt-dlp |

`--from` and `--to` can be combined to download any slice of the listing. They also work with `--download-only`.

Each successful download is recorded in `<output-dir>/.downloaded`. When `--resume` is used, the script finds the last completed video and continues from there.

Files are named `<video_id>_<title>.<ext>`.

---

## Delay

| Flag | Behaviour |
|---|---|
| *(none)* | Random 1–5 second delay between requests |
| `--delay 2` | Fixed 2-second delay |
| `--delay 0` | No delay |

---

## Examples

```bash
# Scrape and download favorites (listing + videos + comments)
python3 thisvid-arch.py --fav

# Scrape and download your own uploaded videos
python3 thisvid-arch.py --self

# Scrape favorites listing only — no download
python3 thisvid-arch.py --fav --no-download

# Download favorites, skip comments
python3 thisvid-arch.py --fav --no-comments

# Download videos 50–150 from your favorites
python3 thisvid-arch.py --fav --from 50 --to 150

# Resume an interrupted favorites download
python3 thisvid-arch.py --fav --resume

# Re-download a specific subset (e.g. missing videos)
python3 thisvid-arch.py --fav --manifest alice_favorites_missing.tsv

# Download from a previously saved listing
python3 thisvid-arch.py --download-only alice_favorites.json

# Download a specific range from a saved listing
python3 thisvid-arch.py --download-only alice_favorites.json --from 100 --to 200

# Suppress yt-dlp warnings
python3 thisvid-arch.py --fav --no-warnings

# Use different credentials at runtime
python3 thisvid-arch.py --fav --username other_user --password s3cr3t
```
