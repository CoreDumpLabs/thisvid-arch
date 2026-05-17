#!/usr/bin/env python3
"""
thisvid_favs.py — scrape and download your ThisVid favourite videos or your own uploads.

Credentials are read from an env file (THISVID_USERNAME / THISVID_PASSWORD)
and can be overridden with --username / --password.

Default behaviour (no flags):
  • Log in, scrape all favourite videos, write <username>_favorites.tsv + .json,
    then optionally download into <username>_favorites/.

Add --self to operate on your own uploaded videos instead of your favourites.

See --help or README.md for the full option reference.
"""

import re
import sys
import json
import argparse
import os
import random
import subprocess
import tempfile
import time
import requests
from dotenv import load_dotenv

load_dotenv("env")

# ── Constants ────────────────────────────────────────────────────────────────

LOGIN_PAGE = "https://thisvid.com/login.php"
LOGIN_URL  = "https://thisvid.com/login/"
FAVS_URL   = "https://thisvid.com/my_favourite_videos/"
YTDLP      = os.path.join(os.path.dirname(os.path.abspath(__file__)), "yt-dlp")

VIDEO_FIELDS   = ["id", "url", "title", "thumbnail", "rating", "views",
                  "favorites", "comments", "date_added", "visibility"]
COMMENT_FIELDS = ["video_id", "comment_id", "user_id", "username", "date", "rating", "text"]
META_FIELDS    = ["video_id", "category", "tags", "description"]

# ── Helpers ──────────────────────────────────────────────────────────────────

def _re1(pattern, text):
    m = re.search(pattern, text, re.DOTALL)
    return m.group(1) if m else ""


def sleep(delay):
    """Sleep for `delay` seconds, or a random 1–5 s if delay is None."""
    secs = random.uniform(1, 5) if delay is None else float(delay)
    if secs > 0:
        time.sleep(secs)


def resolve_outputs(tsv_arg, json_arg, default_stem):
    """
    Work out which output paths to write based on --tsv / --json args.

    nargs='?' means:
      not given   → None
      given bare  → '' (use default name)
      given PATH  → that path

    If neither flag was given at all, write both with default names.
    """
    neither = tsv_arg is None and json_arg is None
    tsv_path  = (tsv_arg  or f"{default_stem}.tsv")  if (neither or tsv_arg  is not None) else None
    json_path = (json_arg or f"{default_stem}.json") if (neither or json_arg is not None) else None
    return tsv_path, json_path


def write_tsv(path, fields, rows):
    with open(path, "w", encoding="utf-8") as f:
        f.write("\t".join(fields) + "\n")
        for row in rows:
            f.write("\t".join(
                str(row.get(field, "")).replace("\t", " ").replace("\n", " ")
                for field in fields
            ) + "\n")


def write_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


# ── Parsing ──────────────────────────────────────────────────────────────────

def parse_videos(html):
    """Return a list of video dicts from a listing page."""
    videos = []
    for block in re.findall(
        r'<div class="thumb-holder">(.*?)</div>\s*</div>\s*</div>', html, re.DOTALL
    ):
        url   = _re1(r'<a href="(https://thisvid\.com/videos/[^"]+)"', block)
        if not url:
            continue
        thumb = (_re1(r'data-original="([^"]+)"', block)
                 or _re1(r'background: url\(([^)]+)\)', block))
        fav_m = re.search(r'Favorites:&nbsp;(\d+)', block)
        com_m = re.search(r'Comments:&nbsp;(\d+)', block)
        views = _re1(r'class="view"[^>]*>[^0-9]*([0-9,]+)', block)
        videos.append({
            "id":         _re1(r'<input[^>]+value="(\d+)"', block),
            "url":        url,
            "title":      _re1(r'<a href="[^"]+" title="([^"]+)"', block),
            "thumbnail":  ("https:" + thumb) if thumb and thumb.startswith("//") else (thumb or ""),
            "rating":     _re1(r'<span class="percent">([^<]+)</span>', block).strip(),
            "views":      views.replace(",", "") if views else "",
            "favorites":  fav_m.group(1) if fav_m else "",
            "comments":   com_m.group(1) if com_m else "",
            "date_added": _re1(r'<span class="date">([^<]+)</span>', block).strip(),
            "visibility": "private" if 'class="thumb private"' in block else "public",
        })
    return videos


def parse_comments(html, video_id):
    """Return a list of comment dicts from a video page or AJAX fragment."""
    comments = []
    for cid, body in re.findall(
        r'<div class="item comment[^"]*"[^>]*data-comment-id="(\d+)">(.*?)</div>',
        html, re.DOTALL
    ):
        raw = _re1(r'<p>(.*?)</p>', body)
        raw = re.sub(r"<img[^>]+alt=['\"]([^'\"]+)['\"][^>]*/?>", r'\1', raw)
        text = re.sub(r'\s+', ' ', re.sub(r'<[^>]+>', '', raw)).strip()
        comments.append({
            "video_id":   video_id,
            "comment_id": cid,
            "user_id":    _re1(r'/members/(\d+)/', _re1(r'class="author"[^>]*href="([^"]+)"', body)),
            "username":   _re1(r'class="author"[^>]*>([^<]+)<', body),
            "date":       _re1(r'<span class="date">([^<]+)</span>', body).strip(),
            "rating":     _re1(r'class="comment-rating[^"]*">([^<]+)<', body).strip(),
            "text":       text,
        })
    return comments


def get_total_pages(html, base_path):
    pages = re.findall(re.escape(base_path) + r'(\d+)/', html)
    return max(int(p) for p in pages) if pages else 1


# ── Network actions ──────────────────────────────────────────────────────────

def login(username, password):
    """Log in and return (session, uid)."""
    session = requests.Session()
    session.headers.update({
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

    session.get(LOGIN_PAGE, timeout=20)

    session.headers.update({
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
    session.headers.pop("Upgrade-Insecure-Requests", None)
    session.headers.pop("Sec-Fetch-User", None)

    resp = session.post(LOGIN_URL, data={
        "username":    username,
        "pass":        password,
        "action":      "login",
        "email_link":  "https://thisvid.com/email/",
        "remember_me": "1",
    }, timeout=20, allow_redirects=True)
    resp.raise_for_status()

    m = re.search(r"userId:\s*'(\d+)'", resp.text)
    if not m:
        sys.exit("ERROR: Login failed — could not find user ID in response.")
    uid = m.group(1)
    print(f"# Logged in as {username} (uid={uid})", file=sys.stderr)

    session.headers.update({
        "Accept":         "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "same-origin",
        "Sec-Fetch-User": "?1",
        "Referer":        "https://thisvid.com/",
    })
    session.headers.pop("X-Requested-With", None)
    session.headers.pop("Content-Type", None)

    return session, uid


def parse_uploaded_blocks(html):
    """Parse thumb-holder blocks from the uploaded videos page.

    Returns a list of partial dicts — url is empty and must be resolved via
    the edit page (the listing only links to /my_video_edit/<id>/).
    """
    videos = []
    for block in re.findall(
        r'<div class="thumb-holder">(.*?)</div>\s*</div>\s*</div>', html, re.DOTALL
    ):
        vid_id = _re1(r'/my_video_edit/(\d+)/', block)
        if not vid_id:
            continue
        thumb = (_re1(r'data-original="([^"]+)"', block)
                 or _re1(r'background: url\(([^)]+)\)', block))
        fav_m = re.search(r'Favorites:&nbsp;(\d+)', block)
        com_m = re.search(r'Comments:&nbsp;(\d+)', block)
        views = _re1(r'class="view"[^>]*>[^0-9]*([0-9,]+)', block)
        title = (_re1(r'<span class="title"[^>]*>([^<]+)</span>', block)
                 or _re1(r'title="Click to edit video: ([^"]+)"', block))
        videos.append({
            "id":         vid_id,
            "url":        "",   # resolved below via edit page
            "title":      title,
            "thumbnail":  ("https:" + thumb) if thumb and thumb.startswith("//") else (thumb or ""),
            "rating":     _re1(r'<span class="percent">([^<]+)</span>', block).strip(),
            "views":      views.replace(",", "") if views else "",
            "favorites":  fav_m.group(1) if fav_m else "",
            "comments":   com_m.group(1) if com_m else "",
            "date_added": _re1(r'<span class="date">([^<]+)</span>', block).strip(),
            "visibility": "private" if 'class="thumb private"' in block else "public",
        })
    return videos


def resolve_uploaded_url(session, vid_id):
    """Fetch the edit page for a video and return its public URL."""
    r = session.get(f"https://thisvid.com/my_video_edit/{vid_id}/", timeout=20)
    r.raise_for_status()
    return _re1(r'(https://thisvid\.com/videos/[^/"]+/)', r.text)


def fetch_uploaded_videos(session, delay):
    """Scrape all pages of the uploaded-videos listing, resolving each video URL."""
    UPLOADED_URL = "https://thisvid.com/my_uploaded_videos/"
    base_path    = "/my_uploaded_videos/"
    all_videos   = []
    seen         = set()

    resp = session.get(UPLOADED_URL, timeout=20)
    resp.raise_for_status()
    total = get_total_pages(resp.text, base_path)
    print(f"# Uploaded videos: {total} page(s)", file=sys.stderr)

    pages_html = [resp.text]
    for page in range(2, total + 1):
        sleep(delay)
        r = session.get(f"https://thisvid.com{base_path}{page}/", timeout=20)
        r.raise_for_status()
        pages_html.append(r.text)

    # Collect all partial records first, then resolve URLs with progress
    partials = []
    for html in pages_html:
        for v in parse_uploaded_blocks(html):
            if v["id"] not in seen:
                seen.add(v["id"])
                partials.append(v)

    print(f"# Resolving URLs for {len(partials)} uploaded videos...", file=sys.stderr)
    for i, v in enumerate(partials, 1):
        if i > 1:
            sleep(delay)
        url = resolve_uploaded_url(session, v["id"])
        if url:
            v["url"] = url
        else:
            print(f"#   WARNING: could not resolve URL for video {v['id']} ({v['title']})",
                  file=sys.stderr)
        print(f"#  {i}/{len(partials)}: {v['title']} → {v['url']}", file=sys.stderr)
        all_videos.append(v)

    print(f"# Total: {len(all_videos)} uploaded videos", file=sys.stderr)
    return all_videos


def fetch_listing(session, listing_url, label, delay):
    """Scrape all pages of a video listing and return a list of video dicts.

    Works for any path-paginated ThisVid listing (favourites, member videos, etc.).
    `label` is used only for progress messages (e.g. 'favourites', 'videos').
    """
    all_videos = []
    seen = set()

    resp = session.get(listing_url, timeout=20)
    resp.raise_for_status()

    # base_path is the URL path portion, e.g. /my_favourite_videos/ or /members/123/videos/
    base_path = "/" + listing_url.split("thisvid.com/", 1)[1]
    total = get_total_pages(resp.text, base_path)
    print(f"# {label.capitalize()}: {total} page(s)", file=sys.stderr)

    def collect(html):
        for v in parse_videos(html):
            if v["url"] not in seen:
                seen.add(v["url"])
                all_videos.append(v)

    collect(resp.text)
    print(f"#  page 1/{total}: {len(all_videos)} videos", file=sys.stderr)

    for page in range(2, total + 1):
        sleep(delay)
        resp = session.get(f"https://thisvid.com{base_path}{page}/", timeout=20)
        resp.raise_for_status()
        before = len(all_videos)
        collect(resp.text)
        print(f"#  page {page}/{total}: +{len(all_videos) - before} ({len(all_videos)} total)",
              file=sys.stderr)

    print(f"# Total: {len(all_videos)} {label}", file=sys.stderr)
    return all_videos


def fetch_video_data(session, video, delay):
    """Fetch a video page and return (comments, meta).

    comments — list of comment dicts (all pages)
    meta     — dict with video_id, category, tags, description
    """
    url = video["url"]
    vid = video["id"]
    all_comments = []

    resp = session.get(url, timeout=20)
    resp.raise_for_status()
    all_comments.extend(parse_comments(resp.text, vid))

    # Extract video-level metadata from the first (full) page
    meta = {
        "video_id":    vid,
        "category":    _re1(r"video_categories:\s*'([^']*)'", resp.text),
        "tags":        _re1(r"video_tags:\s*'([^']*)'", resp.text),
        "description": re.sub(r'\s+', ' ',
                               re.sub(r'<[^>]+>', '',
                                      _re1(r'<ul class="description">.*?<li>\s*<p>(.*?)</p>',
                                           resp.text))).strip(),
    }

    page = 2
    while True:
        m = re.search(r'data-parameters="sort_by:[^;]*;from:(\d+)"', resp.text)
        if not m or int(m.group(1)) < page:
            break
        sleep(delay)
        resp = session.get(url, params={
            "mode": "async", "function": "get_block",
            "block_id": "video_comments_video_comments",
            "sort_by": "", "from": m.group(1),
        }, timeout=20)
        resp.raise_for_status()
        new = parse_comments(resp.text, vid)
        if not new:
            break
        all_comments.extend(new)
        page = int(m.group(1)) + 1

    return all_comments, meta


def write_cookie_file(session, path):
    """Write session cookies to a Netscape-format file for yt-dlp."""
    with open(path, "w") as f:
        f.write("# Netscape HTTP Cookie File\n")
        for c in session.cookies:
            prefix = "#HttpOnly_" if c.has_nonstandard_attr("HttpOnly") else ""
            f.write(
                f"{prefix}{c.domain or 'thisvid.com'}\tTRUE\t{c.path or '/'}\t"
                f"{'TRUE' if c.secure else 'FALSE'}\t{int(c.expires or 0)}\t{c.name}\t{c.value}\n"
            )


# ── Load from file ───────────────────────────────────────────────────────────

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


# ── Download helper ──────────────────────────────────────────────────────────

DOWNLOADED_LOG = ".downloaded"


def _resume_start(target, output_dir):
    """Return the index in target to start from when resuming.

    Reads <output_dir>/.downloaded (one URL per line), finds the last URL that
    appears in target, and returns that index so it gets re-downloaded.
    Returns 0 if the log is missing or no match is found.
    """
    log = os.path.join(output_dir, DOWNLOADED_LOG)
    if not os.path.exists(log):
        print("# --resume: no .downloaded log found, starting from beginning.", file=sys.stderr)
        return 0

    with open(log, encoding="utf-8") as f:
        done = [line.strip() for line in f if line.strip()]

    if not done:
        return 0

    url_to_idx = {v["url"]: i for i, v in enumerate(target)}
    start = 0
    for url in reversed(done):
        if url in url_to_idx:
            start = url_to_idx[url]
            print(f"# --resume: restarting from video {start + 1}: {target[start]['title']}",
                  file=sys.stderr)
            return start

    print("# --resume: no matching videos found in log, starting from beginning.", file=sys.stderr)
    return 0


def _download(session, videos, args, comments_stem):
    """Download videos and (optionally) their comments + metadata, interleaved per video."""
    target = videos[:3] if args.dry_run else videos
    if args.dry_run:
        print(f"# Dry run — limiting to first {len(target)} videos.", file=sys.stderr)

    os.makedirs(args.output_dir, exist_ok=True)

    start = 0
    if args.resume:
        start = _resume_start(target, args.output_dir)
    target = target[start:]

    total = len(target)
    all_comments = []
    all_meta = []
    failed = []

    log_path = os.path.join(args.output_dir, DOWNLOADED_LOG)

    c_tsv, c_json = None, None
    m_tsv, m_json = None, None
    if args.comments:
        c_tsv, c_json = resolve_outputs(args.comments_tsv, args.comments_json, comments_stem)
        m_tsv, m_json = resolve_outputs(args.meta_tsv, args.meta_json,
                                        comments_stem.replace("_comments", "_metadata"))

    cookie_file = tempfile.NamedTemporaryFile(suffix=".txt", delete=False).name
    try:
        write_cookie_file(session, cookie_file)

        for i, v in enumerate(target, 1):
            if i > 1:
                sleep(args.delay)

            if args.comments:
                print(f"# Fetching comments {i}/{total}: {v['title']}", file=sys.stderr)
                comments, meta = fetch_video_data(session, v, args.delay)
                all_comments.extend(comments)
                all_meta.append(meta)
                sleep(args.delay)

            print(f"# Downloading {i}/{total}: {v['title']} [{v['visibility']}]", file=sys.stderr)
            result = subprocess.run([
                YTDLP,
                "--cookies", cookie_file,
                "--output", os.path.join(args.output_dir, "%(id)s_%(title)s.%(ext)s"),
                "--no-playlist",
                "--quiet", "--progress",
                v["url"],
            ])
            if result.returncode != 0:
                print(f"#   FAILED: {v['url']}", file=sys.stderr)
                failed.append(v["url"])
            else:
                with open(log_path, "a", encoding="utf-8") as lf:
                    lf.write(v["url"] + "\n")

    finally:
        os.unlink(cookie_file)

    if failed:
        print(f"# {len(failed)}/{total} downloads failed:", file=sys.stderr)
        for u in failed:
            print(f"#   {u}", file=sys.stderr)
    else:
        print(f"# All {total} downloads complete.", file=sys.stderr)

    if args.comments:
        if c_tsv:
            write_tsv(c_tsv, COMMENT_FIELDS, all_comments)
            print(f"# Written: {c_tsv} ({len(all_comments)} comments)", file=sys.stderr)
        if c_json:
            write_json(c_json, all_comments)
            print(f"# Written: {c_json}", file=sys.stderr)


# ── Main ─────────────────────────────────────────────────────────────────────

def main(args):
    username = args.username or os.getenv("THISVID_USERNAME")
    password = args.password or os.getenv("THISVID_PASSWORD")

    if not username:
        sys.exit("ERROR: No username. Use --username or set THISVID_USERNAME in env")
    if not password:
        sys.exit("ERROR: No password. Use --password or set THISVID_PASSWORD in env")

    # ── Download-only mode ───────────────────────────────────────────────────
    if args.download_only:
        if args.output_dir is None:
            args.output_dir = f"{username}_favorites"
        videos = load_videos(args.download_only)
        print(f"# Loaded {len(videos)} videos from {args.download_only}", file=sys.stderr)
        session, _ = login(username, password)
        _download(session, videos, args, f"{username}_favorites_comments")
        return

    session, uid = login(username, password)

    # ── Resolve mode-specific defaults ───────────────────────────────────────
    if args.self_videos:
        listing_url   = "https://thisvid.com/my_uploaded_videos/"
        label         = "videos"
        listing_stem  = f"{username}_videos"
        comments_stem = f"{username}_videos_comments"
        default_dir   = f"{username}_videos"
    else:
        listing_url   = FAVS_URL
        label         = "favourites"
        listing_stem  = f"{username}_favorites"
        comments_stem = f"{username}_favorites_comments"
        default_dir   = f"{username}_favorites"

    if args.output_dir is None:
        args.output_dir = default_dir

    # ── Scrape listing ────────────────────────────────────────────────────────
    if args.self_videos:
        videos = fetch_uploaded_videos(session, args.delay)
    else:
        videos = fetch_listing(session, listing_url, label, args.delay)

    tsv_path, json_path = resolve_outputs(args.tsv, args.json, listing_stem)
    if tsv_path:
        write_tsv(tsv_path, VIDEO_FIELDS, videos)
        print(f"# Written: {tsv_path}", file=sys.stderr)
    if json_path:
        write_json(json_path, videos)
        print(f"# Written: {json_path}", file=sys.stderr)

    if args.download:
        _download(session, videos, args, comments_stem)


# ── CLI ──────────────────────────────────────────────────────────────────────

def build_parser():
    parser = argparse.ArgumentParser(
        prog="thisvid_favs.py",
        description="Scrape and download your ThisVid favourite videos or your own uploads.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
MODES
  (default)               Scrape favourites listing, write TSV + JSON.
  --self                  Operate on your own uploaded videos instead of favourites.
  --download              Also download every video after scraping.
  --comments              Fetch comments for each video when downloading.
  --download-only PATH    Load a previously saved .tsv or .json and download.

  --download and --comments can be combined with --self.

OUTPUT FILES (video listing)
  Defaults to <username>_favorites.tsv/.json, or <username>_videos.tsv/.json with --self.
  Use --tsv or --json to write only one format, with an optional custom path.

  --tsv                   Write TSV to default path.
  --tsv results.tsv       Write TSV to results.tsv
  --json                  Write JSON to default path.
  --json results.json     Write JSON to results.json

OUTPUT FILES (comments)  — requires --comments
  Defaults to <username>_favorites_comments.tsv/.json, or <username>_videos_comments with --self.

EXAMPLES
  python3 thisvid_favs.py
  python3 thisvid_favs.py --self
  python3 thisvid_favs.py --download
  python3 thisvid_favs.py --download --comments
  python3 thisvid_favs.py --self --download --comments
  python3 thisvid_favs.py --download-only GassesAndSolids_favorites.json
  python3 thisvid_favs.py --download-only GassesAndSolids_favorites.json --resume
  python3 thisvid_favs.py --download-only GassesAndSolids_favorites.json --comments --dry-run
  python3 thisvid_favs.py --delay 2
  python3 thisvid_favs.py --delay 0
""",
    )

    auth = parser.add_argument_group("Authentication")
    auth.add_argument("--username", metavar="USER",
                      help="ThisVid username (overrides THISVID_USERNAME in env)")
    auth.add_argument("--password", metavar="PASS",
                      help="ThisVid password (overrides THISVID_PASSWORD in env)")

    mode = parser.add_argument_group("Mode")
    mode.add_argument("--self", dest="self_videos", action="store_true",
                      help="Operate on your own uploaded videos instead of favourites.")
    mode.add_argument("--download", action="store_true",
                      help="Download videos after scraping.")
    mode.add_argument("--download-only", metavar="PATH",
                      help="Load video list from .tsv or .json and download (skip scraping).")

    out = parser.add_argument_group("Output — video listing")
    out.add_argument("--tsv",  nargs="?", const="", metavar="PATH",
                     help="Write TSV. Omit PATH to use default name.")
    out.add_argument("--json", nargs="?", const="", metavar="PATH",
                     help="Write JSON. Omit PATH to use default name.")

    com = parser.add_argument_group("Comments")
    com.add_argument("--comments", action="store_true",
                     help="Fetch comments for each video when downloading.")
    com.add_argument("--comments-tsv",  nargs="?", const="", metavar="PATH",
                     help="Write comments TSV. Omit PATH to use default name.")
    com.add_argument("--comments-json", nargs="?", const="", metavar="PATH",
                     help="Write comments JSON. Omit PATH to use default name.")

    dl = parser.add_argument_group("Downloads")
    dl.add_argument("--output-dir", metavar="DIR", default=None,
                    help="Directory for downloaded videos (default: <username>_favorites/ or <username>_videos/ with --self)")
    dl.add_argument("--resume", action="store_true",
                    help="Resume from last completed download (re-downloads last video, then continues).")

    misc = parser.add_argument_group("General")
    misc.add_argument("--delay", metavar="SECS", type=float, default=None,
                      help="Seconds to wait between requests (default: random 1–5). Use 0 to disable.")
    misc.add_argument("--dry-run", action="store_true",
                      help="When downloading, fetch only the first 3 videos.")

    return parser


if __name__ == "__main__":
    main(build_parser().parse_args())
