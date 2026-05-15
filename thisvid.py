#!/usr/bin/env python3
"""
thisvid.py — scrape and download your ThisVid favourite videos or your own uploads.

Credentials are read from a .env file (THISVID_USERNAME / THISVID_PASSWORD)
and can be overridden with --username / --password.

See --help or README.md for the full option reference.
"""

import argparse
import os
import sys

from backend import ThisVidClient, Downloader, load_videos, resolve_outputs, write_tsv, write_json, VIDEO_FIELDS
from favorites import FavoritesScraper, FAVS_URL
from uploaded import UploadedScraper


def _apply_range(videos, args):
    """Slice videos to the --from / --to range (both 1-indexed, inclusive)."""
    start = (args.from_idx - 1) if args.from_idx else 0
    end   = args.to_idx if args.to_idx else None
    sliced = videos[start:end]
    if args.from_idx or args.to_idx:
        total = len(videos)
        actual_end = min(args.to_idx, total) if args.to_idx else total
        actual_start = min((args.from_idx or 1), total)
        print(f"# Range: videos {actual_start}–{actual_end} of {total}", file=sys.stderr)
    return sliced


def _check_comments_conflict(args, comments_stem):
    """Abort if comment output files already exist and --resume was not given."""
    if not args.comments or args.resume:
        return
    c_tsv, c_json = resolve_outputs(
        args.comments_tsv, args.comments_json, comments_stem,
        no_tsv=args.no_comments_tsv, no_json=args.no_comments_json,
    )
    existing = [p for p in [c_tsv, c_json] if p and os.path.exists(p)]
    if existing:
        sys.exit(
            f"ERROR: Comments file(s) already exist: {', '.join(existing)}\n"
            "       Use --resume to append to existing files, or delete them first."
        )


def main(args):
    username = args.username or os.getenv("THISVID_USERNAME")
    password = args.password or os.getenv("THISVID_PASSWORD")

    if not username:
        sys.exit("ERROR: No username. Use --username or set THISVID_USERNAME in .env")
    if not password:
        sys.exit("ERROR: No password. Use --password or set THISVID_PASSWORD in .env")

    # ── Download-only mode ───────────────────────────────────────────────────
    if args.download_only:
        if args.output_dir is None:
            # Default: strip extension from the input file and use as directory.
            args.output_dir = os.path.splitext(os.path.basename(args.download_only))[0]
        comments_stem = os.path.join(args.output_dir, f"{username}_comments")
        _check_comments_conflict(args, comments_stem)
        videos = load_videos(args.download_only)
        videos = _apply_range(videos, args)
        print(f"# Loaded {len(videos)} videos from {args.download_only}", file=sys.stderr)
        client = ThisVidClient(username, password).login()
        Downloader(client, args).run(videos, comments_stem)
        return

    client = ThisVidClient(username, password).login()

    # ── Resolve mode-specific defaults ───────────────────────────────────────
    if args.mode == "self":
        listing_stem = f"{username}_videos"
        default_dir  = f"{username}_videos"
    else:  # "fav"
        listing_stem = f"{username}_favorites"
        default_dir  = f"{username}_favs"

    if args.output_dir is None:
        args.output_dir = default_dir

    # Comments go inside the video download directory.
    comments_stem = os.path.join(args.output_dir, f"{username}_comments")

    # Check for existing comment files before any scraping or downloading.
    if not args.probe and args.download:
        _check_comments_conflict(args, comments_stem)

    # ── Resolve listing source ───────────────────────────────────────────────
    url_resolver = None

    if args.manifest:
        # Explicit manifest always wins — skip scraping entirely.
        if not os.path.exists(args.manifest):
            sys.exit(f"ERROR: Manifest file not found: {args.manifest}")
        cached_listing = args.manifest
    else:
        # --probe forces re-scrape even when a cached listing exists.
        cached_listing = None if args.probe else next(
            (listing_stem + ext for ext in (".tsv", ".json")
             if os.path.exists(listing_stem + ext)),
            None,
        )

    if cached_listing:
        videos = load_videos(cached_listing)
        source = "manifest" if args.manifest else "cached listing"
        print(f"# Using {source}: {cached_listing} ({len(videos)} videos)", file=sys.stderr)
        if args.mode == "self" and args.download:
            scraper = UploadedScraper(client, args.delay)
            url_resolver = scraper.resolve_url  # only fires for entries with url=""
    else:
        if args.mode == "self":
            scraper = UploadedScraper(client, args.delay)
            if args.probe:
                # Probe-only: resolve all URLs now so the manifest is complete.
                videos = scraper.fetch()
            else:
                # Downloading immediately after: resolve URLs lazily during the download loop.
                videos = scraper.fetch_partials()
                url_resolver = scraper.resolve_url
        else:
            videos = FavoritesScraper(client, args.delay).fetch(FAVS_URL, label="favourites")

    videos = _apply_range(videos, args)

    # ── Write manifest (always both formats unless overridden) ───────────────
    tsv_path, json_path = (None, None)
    if not args.no_manifest:
        tsv_path, json_path = resolve_outputs(
            args.tsv, args.json, listing_stem,
            no_tsv=args.no_tsv, no_json=args.no_json,
        )

    def write_manifest():
        if tsv_path:
            write_tsv(tsv_path, VIDEO_FIELDS, videos)
            print(f"# Written: {tsv_path}", file=sys.stderr)
        if json_path:
            write_json(json_path, videos)
            print(f"# Written: {json_path}", file=sys.stderr)

    # Write manifest when we produced fresh data. When loading from cache the
    # file is unchanged, so skip the redundant write (post-download will update it).
    if not cached_listing:
        write_manifest()

    if args.probe:
        print(f"# Probe complete — {len(videos)} videos. Run without --probe to download.",
              file=sys.stderr)
        return

    if args.download:
        Downloader(client, args).run(videos, comments_stem, url_resolver=url_resolver)
        # Rewrite manifest to capture enriched metadata and (for --self) resolved URLs.
        write_manifest()


def build_parser():
    parser = argparse.ArgumentParser(
        prog="thisvid.py",
        description=(
            "Scrape and download your ThisVid favourite videos or your own uploads.\n\n"
            "By default this app downloads. Just run it and it will fetch the video listing\n"
            "(or reuse a cached one) and download everything. Use --probe or --no-download\n"
            "if you want to stop short of downloading."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
NOTE: By default this app downloads. Run it and it will do the right thing.
      Use --probe to fetch the listing only, --no-download to skip downloading.

MODES
  --fav                   Scrape/download your favourite videos.
  --self                  Scrape/download your own uploaded videos.
  --download-only PATH    Load a previously saved .tsv or .json and download (skip scraping).

  --probe                 Fetch (or re-fetch) the listing and save it — no video downloads.
  --no-download           Fetch the listing if needed, but skip downloading.

OUTPUT FILES (video listing)
  Both TSV and JSON are written by default.
  Defaults to <username>_favorites.tsv/.json (--fav) or <username>_videos.tsv/.json (--self).

  --tsv PATH              Write TSV to PATH (JSON still written unless --no-json).
  --json PATH             Write JSON to PATH (TSV still written unless --no-tsv).
  --no-tsv                Skip TSV listing output.
  --no-json               Skip JSON listing output.
  --no-manifest           Skip all listing output.

OUTPUT FILES (comments)
  Both TSV and JSON are written by default inside the video download directory.

  --comments-tsv PATH     Write comments TSV to PATH (JSON still written unless --no-comments-json).
  --comments-json PATH    Write comments JSON to PATH (TSV still written unless --no-comments-tsv).
  --no-comments-tsv       Skip comments TSV output.
  --no-comments-json      Skip comments JSON output.
  --no-comments           Skip comment fetching entirely.

WORKFLOW (--fav)
  python3 thisvid.py --fav                                    # fetch listing + download (the default)
  python3 thisvid.py --fav                                    # subsequent runs reuse cached listing
  python3 thisvid.py --fav --probe                            # refresh listing only, no download
  python3 thisvid.py --fav --no-download                      # fetch listing if needed, no download
  python3 thisvid.py --fav --manifest missing.tsv             # download a specific subset

EXAMPLES
  python3 thisvid.py --fav
  python3 thisvid.py --fav --probe
  python3 thisvid.py --fav --from 50 --to 100
  python3 thisvid.py --fav --manifest GassesAndSolids_favorites_missing.tsv
  python3 thisvid.py --self
  python3 thisvid.py --self --no-comments
  python3 thisvid.py --download-only GassesAndSolids_favorites.json
  python3 thisvid.py --download-only GassesAndSolids_favorites.json --resume
  python3 thisvid.py --fav --delay 2
""",
    )

    auth = parser.add_argument_group("Authentication")
    auth.add_argument("--username", metavar="USER",
                      help="ThisVid username (overrides THISVID_USERNAME in .env)")
    auth.add_argument("--password", metavar="PASS",
                      help="ThisVid password (overrides THISVID_PASSWORD in .env)")

    target = parser.add_argument_group("Target (required unless using --download-only)")
    grp = target.add_mutually_exclusive_group()
    grp.add_argument("--fav",  dest="mode", action="store_const", const="fav",
                     help="Scrape and download favourite videos.")
    grp.add_argument("--self", dest="mode", action="store_const", const="self",
                     help="Scrape and download your own uploaded videos.")

    mode = parser.add_argument_group("Mode")
    mode.add_argument("--probe", action="store_true",
                      help="Fetch (or re-fetch) the video listing and save it — no download. "
                           "Use this to review or refresh the listing before downloading.")
    mode.add_argument("--no-download", dest="download", action="store_false",
                      help="Fetch the listing if needed, but skip downloading.")
    mode.add_argument("--manifest", metavar="PATH",
                      help="Load video list from this .tsv or .json instead of scraping or "
                           "using any cached listing.")
    mode.add_argument("--download-only", metavar="PATH",
                      help="Load video list from .tsv or .json and download (skip scraping).")
    parser.set_defaults(download=True, mode=None, probe=False, manifest=None,
                        no_tsv=False, no_json=False)

    out = parser.add_argument_group("Output — video listing (both formats written by default)")
    out.add_argument("--tsv",  metavar="PATH", default=None,
                     help="Write the TSV listing to PATH. JSON is still written unless --no-json.")
    out.add_argument("--json", metavar="PATH", default=None,
                     help="Write the JSON listing to PATH. TSV is still written unless --no-tsv.")
    out.add_argument("--no-tsv",      action="store_true", help="Do not write the TSV listing.")
    out.add_argument("--no-json",     action="store_true", help="Do not write the JSON listing.")
    out.add_argument("--no-manifest", action="store_true", help="Do not write any listing file.")

    com = parser.add_argument_group("Comments (fetched by default when downloading)")
    com.add_argument("--no-comments",      dest="comments", action="store_false",
                     help="Skip fetching comments entirely.")
    com.add_argument("--comments-tsv",     metavar="PATH", default=None,
                     help="Write comments TSV to PATH. JSON is still written unless --no-comments-json.")
    com.add_argument("--comments-json",    metavar="PATH", default=None,
                     help="Write comments JSON to PATH. TSV is still written unless --no-comments-tsv.")
    com.add_argument("--no-comments-tsv",  action="store_true", help="Do not write comments TSV.")
    com.add_argument("--no-comments-json", action="store_true", help="Do not write comments JSON.")
    parser.set_defaults(comments=True, no_comments_tsv=False, no_comments_json=False)

    dl = parser.add_argument_group("Downloads")
    dl.add_argument("--output-dir", metavar="DIR", default=None,
                    help="Directory for downloaded videos.")
    dl.add_argument("--resume", action="store_true",
                    help="Resume from last completed download (re-downloads last video, then continues).")
    dl.add_argument("--from", dest="from_idx", metavar="N", type=int, default=None,
                    help="Start at video N (1-indexed). Default: 1.")
    dl.add_argument("--to", dest="to_idx", metavar="N", type=int, default=None,
                    help="Stop after video N (1-indexed). Default: last video.")

    misc = parser.add_argument_group("General")
    misc.add_argument("--delay", metavar="SECS", type=float, default=None,
                      help="Seconds to wait between requests (default: random 1–5). Use 0 to disable.")
    misc.add_argument("--dry-run", action="store_true",
                      help="When downloading, fetch only the first 3 videos.")
    misc.add_argument("--no-warnings", dest="no_warnings", action="store_true",
                      help="Pass --no-warnings to yt-dlp (suppress download warnings).")

    return parser


def parse_args(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)

    if not args.download_only and args.mode is None:
        parser.error("one of --fav or --self is required")

    # Phase conflicts
    if args.manifest and args.probe:
        parser.error("--manifest and --probe are mutually exclusive: "
                     "--manifest loads a file, --probe re-scrapes from the site")
    if args.probe and args.no_manifest:
        parser.error("--probe --no-manifest would scrape the listing and immediately discard it")

    # Listing output conflicts
    if args.tsv and args.no_tsv:
        parser.error("--tsv PATH and --no-tsv are mutually exclusive")
    if args.json and args.no_json:
        parser.error("--json PATH and --no-json are mutually exclusive")
    if args.no_tsv and args.no_json:
        parser.error("--no-tsv --no-json: use --no-manifest to suppress all listing output")

    # Comment output conflicts
    if args.comments_tsv and args.no_comments_tsv:
        parser.error("--comments-tsv PATH and --no-comments-tsv are mutually exclusive")
    if args.comments_json and args.no_comments_json:
        parser.error("--comments-json PATH and --no-comments-json are mutually exclusive")
    if args.no_comments_tsv and args.no_comments_json:
        parser.error("--no-comments-tsv --no-comments-json: use --no-comments to skip comment fetching entirely")
    if not args.comments and (args.comments_tsv or args.comments_json):
        parser.error("--no-comments disables comment fetching; --comments-tsv/--comments-json have no effect")

    return args


if __name__ == "__main__":
    main(parse_args())
