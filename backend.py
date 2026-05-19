"""
backend.py — shared utilities, session client, and download logic for thisvid.
"""

import json
import os
import random
import re
import shutil
import subprocess
import sys
import tempfile
import time

import requests
from dotenv import load_dotenv

load_dotenv("env")

# ── Constants ────────────────────────────────────────────────────────────────

LOGIN_PAGE = "https://thisvid.com/login.php"
LOGIN_URL  = "https://thisvid.com/login/"
YTDLP      = shutil.which("yt-dlp") or os.path.join(os.path.dirname(os.path.abspath(__file__)), "yt-dlp")

VIDEO_FIELDS = ["id", "url", "title", "thumbnail", "rating", "views",
                "favorites", "comments", "date_added", "visibility",
                "duration", "upload_date", "category", "tags", "description"]

DOWNLOADED_LOG = ".downloaded"

# ── Utilities ─────────────────────────────────────────────────────────────────

def _re1(pattern, text):
    m = re.search(pattern, text, re.DOTALL)
    return m.group(1) if m else ""


def sleep(delay):
    """Sleep for `delay` seconds, or a random 1–5 s if delay is None."""
    secs = random.uniform(1, 5) if delay is None else float(delay)
    if secs > 0:
        time.sleep(secs)


def get_total_pages(html, base_path):
    pages = re.findall(re.escape(base_path) + r'(\d+)/', html)
    return max(int(p) for p in pages) if pages else 1


def resolve_outputs(tsv_arg, json_arg, default_stem, no_tsv=False, no_json=False):
    """
    Work out which output paths to write.

    Both formats are written by default. Individual formats are suppressed with
    no_tsv / no_json. tsv_arg / json_arg override the output path:
      None or ''  → use default name (<default_stem>.tsv / .json)
      PATH        → write to that path
    """
    tsv_path  = None if no_tsv  else (tsv_arg  or f"{default_stem}.tsv")
    json_path = None if no_json else (json_arg or f"{default_stem}.json")
    return tsv_path, json_path


def write_tsv(path, fields, rows, append=False):
    mode = "a" if append and os.path.exists(path) else "w"
    with open(path, mode, encoding="utf-8") as f:
        if mode == "w":
            f.write("\t".join(fields) + "\n")
        for row in rows:
            f.write("\t".join(
                str(row.get(field, "")).replace("\t", " ").replace("\n", " ")
                for field in fields
            ) + "\n")


def write_json(path, data, append=False):
    if append and os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            existing = json.load(f)
        data = existing + data
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def load_videos(path):
    if path.endswith(".json"):
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    elif path.endswith(".tsv"):
        with open(path, encoding="utf-8") as f:
            headers = f.readline().rstrip("\n").split("\t")
            return [dict(zip(headers, line.rstrip("\n").split("\t"))) for line in f]
    else:
        sys.exit(f"ERROR: Cannot auto-detect format of '{path}' — must be .tsv or .json")


# ── ThisVidClient ─────────────────────────────────────────────────────────────

class ThisVidClient:
    """Authenticated HTTP session for thisvid.com."""

    def __init__(self, username, password):
        self.username = username
        self.password = password
        self.uid = None
        self.session = requests.Session()

    def login(self):
        """Log in and populate self.uid. Returns self for chaining."""
        self.session.headers.update({
            "User-Agent":                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36",
            "Accept":                    "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
            "Accept-Language":           "en-US,en;q=0.9,fr;q=0.8,de;q=0.7",
            "Accept-Encoding":           "gzip, deflate, br, zstd",
            "Connection":                "keep-alive",
            "Upgrade-Insecure-Requests": "1",
            "Sec-Fetch-Dest":            "document",
            "Sec-Fetch-Mode":            "navigate",
            "Sec-Fetch-Site":            "none",
            "Sec-Fetch-User":            "?1",
            "sec-ch-ua":                 '"Chromium";v="146", "Not-A.Brand";v="24", "Google Chrome";v="146"',
            "sec-ch-ua-mobile":          "?0",
            "sec-ch-ua-platform":        '"macOS"',
        })

        self.session.get(LOGIN_PAGE, timeout=20)

        self.session.headers.update({
            "Accept":                      "*/*",
            "Content-Type":                "application/x-www-form-urlencoded; charset=UTF-8",
            "Origin":                      "https://thisvid.com",
            "Referer":                     LOGIN_PAGE,
            "Priority":                    "u=1, i",
            "X-Requested-With":            "XMLHttpRequest",
            "sec-ch-ua-arch":              '"arm"',
            "sec-ch-ua-bitness":           '"64"',
            "sec-ch-ua-full-version":      '"146.0.7680.153"',
            "sec-ch-ua-full-version-list": '"Chromium";v="146.0.7680.153", "Not-A.Brand";v="24.0.0.0", "Google Chrome";v="146.0.7680.153"',
            "sec-ch-ua-model":             '""',
            "sec-ch-ua-platform-version":  '"26.1.0"',
            "Sec-Fetch-Dest":              "empty",
            "Sec-Fetch-Mode":              "cors",
            "Sec-Fetch-Site":              "same-origin",
        })
        self.session.headers.pop("Upgrade-Insecure-Requests", None)
        self.session.headers.pop("Sec-Fetch-User", None)

        resp = self.session.post(LOGIN_URL, data={
            "username":    self.username,
            "pass":        self.password,
            "action":      "login",
            "email_link":  "https://thisvid.com/email/",
            "remember_me": "1",
        }, timeout=20, allow_redirects=True)
        resp.raise_for_status()

        m = re.search(r"userId:\s*'(\d+)'", resp.text)
        if not m:
            print(f"DEBUG: Login response status={resp.status_code}, url={resp.url}", file=sys.stderr)
            print(f"DEBUG: Response length={len(resp.text)}", file=sys.stderr)
            print(f"DEBUG: History={[r.status_code for r in resp.history]}", file=sys.stderr)
            print(f"DEBUG: First 2000 chars of response:\n{resp.text[:2000]}", file=sys.stderr)
            sys.exit(
                "ERROR: Login failed — check that your username and password are correct.\n"
                "Make sure you have a file called 'env' with your credentials.\n"
                "See env.template for the correct format."
            )
        self.uid = m.group(1)
        print(f"# Logged in as {self.username} (uid={self.uid})", file=sys.stderr)

        self.session.headers.update({
            "Accept":         "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "same-origin",
            "Sec-Fetch-User": "?1",
            "Referer":        "https://thisvid.com/",
        })
        self.session.headers.pop("X-Requested-With", None)
        self.session.headers.pop("Content-Type", None)

        return self

    def get(self, url, **kwargs):
        return self.session.get(url, **kwargs)

    def write_cookie_file(self, path):
        """Write session cookies to a Netscape-format file for yt-dlp."""
        with open(path, "w") as f:
            f.write("# Netscape HTTP Cookie File\n")
            for c in self.session.cookies:
                prefix = "#HttpOnly_" if c.has_nonstandard_attr("HttpOnly") else ""
                f.write(
                    f"{prefix}{c.domain or 'thisvid.com'}\tTRUE\t{c.path or '/'}\t"
                    f"{'TRUE' if c.secure else 'FALSE'}\t{int(c.expires or 0)}\t{c.name}\t{c.value}\n"
                )


# ── Downloader ────────────────────────────────────────────────────────────────

class Downloader:
    """Downloads a list of videos via yt-dlp, optionally fetching comments."""

    def __init__(self, client, args):
        self.client = client
        self.args = args

    def _load_skip_set(self, log_path):
        """Return the set of video IDs already in a final state (downloaded/private/unavailable).

        These are skipped on --resume. Videos absent from the log are retried,
        so transient failures (server errors, timeouts) are automatically retried.
        """
        if not os.path.exists(log_path):
            print("# --resume: no download log found, starting from beginning.", file=sys.stderr)
            return set()
        with open(log_path, encoding="utf-8") as f:
            lines = [line.rstrip("\n") for line in f if line.strip()]
        skip = {parts[0] for line in lines for parts in [line.split("\t")]
                if parts and parts[0] != "id"}
        print(f"# --resume: {len(skip)} videos already in a final state, skipping.", file=sys.stderr)
        return skip

    def _log_status(self, log_path, video_id, status):
        """Append a single id/status row to the download log."""
        write_header = not os.path.exists(log_path)
        with open(log_path, "a", encoding="utf-8") as f:
            if write_header:
                f.write("id\tstatus\n")
            f.write(f"{video_id}\t{status}\n")

    def run(self, videos, comments_stem, url_resolver=None):
        """Download videos and (optionally) comments, interleaved per video.

        url_resolver: optional callable(vid_id) -> str used to resolve the
        public URL for uploaded videos that have url='' (--self mode). When
        provided, each URL is resolved just before that video is processed so
        that no batch of N edit-page requests precedes the downloads.

        When comments are enabled, each video dict is enriched in place with
        metadata from the video page (duration, upload_date, category, tags,
        description). The caller should re-write the manifest afterwards.
        """
        from comments import CommentFetcher, COMMENT_FIELDS  # noqa: E402 (avoid circular at module level)

        args = self.args
        target = videos[:3] if args.dry_run else videos
        if args.dry_run:
            print(f"# Dry run — limiting to first {len(target)} videos.", file=sys.stderr)

        os.makedirs(args.output_dir, exist_ok=True)

        log_path = os.path.join(args.output_dir, DOWNLOADED_LOG)

        if args.resume:
            skip_ids = self._load_skip_set(log_path)
            target = [v for v in target if v["id"] not in skip_ids]
            print(f"# --resume: {len(target)} videos remaining.", file=sys.stderr)

        total = len(target)
        all_comments = []

        c_tsv, c_json = None, None
        if args.comments:
            c_tsv, c_json = resolve_outputs(
                args.comments_tsv, args.comments_json, comments_stem,
                no_tsv=getattr(args, "no_comments_tsv", False),
                no_json=getattr(args, "no_comments_json", False),
            )
            fetcher = CommentFetcher(self.client, args.delay)

        def flush_comments():
            if args.comments and all_comments:
                if c_tsv:
                    write_tsv(c_tsv, COMMENT_FIELDS, all_comments, append=True)
                    print(f"# Written: {c_tsv} ({len(all_comments)} comments)", file=sys.stderr)
                if c_json:
                    write_json(c_json, all_comments, append=True)
                    print(f"# Written: {c_json}", file=sys.stderr)

        cookie_file = tempfile.NamedTemporaryFile(suffix=".txt", delete=False).name
        try:
            self.client.write_cookie_file(cookie_file)

            MAX_RETRIES = 10
            RETRY_WAIT = 10

            for i, v in enumerate(target, 1):
                if i > 1:
                    sleep(args.delay)

                print(f"# [{i}/{total}] {v['title']} [{v['visibility']}]", file=sys.stderr)

                for attempt in range(1, MAX_RETRIES + 1):
                    last_attempt = attempt == MAX_RETRIES
                    error_msg = None

                    try:
                        if url_resolver and not v.get("url"):
                            print("#   resolving URL...", file=sys.stderr)
                            v["url"] = url_resolver(v["id"]) or ""
                            if not v["url"]:
                                print("#   WARNING: could not resolve URL, skipping.", file=sys.stderr)
                                break
                            sleep(args.delay)

                        if args.comments:
                            comments = fetcher.fetch(v)  # enriches v in place; also fetches metadata
                            all_comments.extend(comments)
                            sleep(args.delay)
                        else:
                            resp = self.client.get(v["url"], timeout=20)
                            resp.raise_for_status()
                            if CommentFetcher.is_unavailable(resp.text):
                                v["unavailable"] = True
                    except requests.HTTPError as e:
                        if e.response is not None and e.response.status_code == 404:
                            print(f"#   WARNING: video not found (404), skipping.", file=sys.stderr)
                            self._log_status(log_path, v["id"], "unavailable")
                            break
                        error_msg = f"ERROR: Network error fetching '{v['title']}': {e}"
                    except requests.RequestException as e:
                        error_msg = f"ERROR: Network error fetching '{v['title']}': {e}"

                    if error_msg:
                        if last_attempt:
                            flush_comments()
                            sys.exit(f"{error_msg}\n       Failed after {MAX_RETRIES} attempts.")

                        print(f"#   Attempt {attempt}/{MAX_RETRIES} failed: {error_msg}", file=sys.stderr)
                        print(f"#   Retrying in {RETRY_WAIT}s...", file=sys.stderr)
                        time.sleep(RETRY_WAIT)
                        continue

                    if v.get("unavailable"):
                        print("#   WARNING: video removed (no player), skipping.", file=sys.stderr)
                        self._log_status(log_path, v["id"], "unavailable")
                        break

                    print("#   downloading...", file=sys.stderr)
                    ytdlp_cmd = [
                        YTDLP,
                        "--cookies", cookie_file,
                        "--output", os.path.join(args.output_dir, "%(id)s_%(title)s.%(ext)s"),
                        "--no-playlist",
                        "--quiet", "--progress",
                    ]
                    if getattr(args, "no_warnings", False):
                        ytdlp_cmd.append("--no-warnings")
                    ytdlp_cmd.append(v["url"])
                    result = subprocess.run(ytdlp_cmd, stderr=subprocess.PIPE, text=True)
                    if result.returncode != 0:
                        if result.stderr:
                            sys.stderr.write(result.stderr)
                        if "private" in result.stderr.lower():
                            print("#   WARNING: video is private, skipping.", file=sys.stderr)
                            self._log_status(log_path, v["id"], "private")
                            break
                        if last_attempt:
                            flush_comments()
                            sys.exit(
                                f"ERROR: Download failed: {v['title']}\n"
                                f"       Failed after {MAX_RETRIES} attempts."
                            )
                        print(f"#   Attempt {attempt}/{MAX_RETRIES} failed: download error", file=sys.stderr)
                        print(f"#   Retrying in {RETRY_WAIT}s...", file=sys.stderr)
                        time.sleep(RETRY_WAIT)
                        continue
                    flush_comments()
                    all_comments.clear()
                    self._log_status(log_path, v["id"], "downloaded")
                    break

        finally:
            os.unlink(cookie_file)

        print(f"# All {total} downloads complete.", file=sys.stderr)
