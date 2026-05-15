"""
uploaded.py — scrape ThisVid uploaded (self) video listings.
"""

import re
import sys

from backend import _re1, get_total_pages, sleep

UPLOADED_URL = "https://thisvid.com/my_uploaded_videos/"
_BASE_PATH   = "/my_uploaded_videos/"


class UploadedScraper:
    """Scrapes all pages of the ThisVid uploaded-videos listing."""

    def __init__(self, client, delay):
        self.client = client
        self.delay = delay

    @staticmethod
    def _parse_blocks(html):
        """Return partial video dicts from the uploaded listing.

        URLs are empty — they must be resolved via the edit page.
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
                "url":        "",   # resolved later via edit page
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

    def resolve_url(self, vid_id):
        """Fetch the edit page and return the video's public URL."""
        r = self.client.get(f"https://thisvid.com/my_video_edit/{vid_id}/", timeout=20)
        r.raise_for_status()
        return _re1(r'(https://thisvid\.com/videos/[^/"]+/)', r.text)

    def _scrape_pages(self):
        """Scrape all listing pages and return partial video dicts (url='')."""
        all_videos = []
        seen = set()

        resp = self.client.get(UPLOADED_URL, timeout=20)
        resp.raise_for_status()
        total = get_total_pages(resp.text, _BASE_PATH)
        print(f"# Uploaded videos: {total} page(s)", file=sys.stderr)

        pages_html = [resp.text]
        for page in range(2, total + 1):
            sleep(self.delay)
            r = self.client.get(f"https://thisvid.com{_BASE_PATH}{page}/", timeout=20)
            r.raise_for_status()
            pages_html.append(r.text)

        for html in pages_html:
            for v in self._parse_blocks(html):
                if v["id"] not in seen:
                    seen.add(v["id"])
                    all_videos.append(v)

        return all_videos

    def fetch(self):
        """Scrape all uploaded videos and resolve every URL upfront.

        Use this for --no-download (manifest-only) so the manifest contains
        usable URLs. For download mode, use fetch_partials() instead so that
        URL resolution is interleaved with the per-video download loop.
        """
        partials = self._scrape_pages()
        print(f"# Resolving URLs for {len(partials)} uploaded videos...", file=sys.stderr)
        for i, v in enumerate(partials, 1):
            if i > 1:
                sleep(self.delay)
            url = self.resolve_url(v["id"])
            if url:
                v["url"] = url
            else:
                print(f"#   WARNING: could not resolve URL for video {v['id']} ({v['title']})",
                      file=sys.stderr)
            print(f"#  {i}/{len(partials)}: {v['title']} → {v['url']}", file=sys.stderr)
        print(f"# Total: {len(partials)} uploaded videos", file=sys.stderr)
        return partials

    def fetch_partials(self):
        """Scrape listing pages only — do NOT resolve URLs.

        Returns video dicts with url=''. Pass self.resolve_url as the
        url_resolver callback to Downloader.run() so each URL is resolved
        just before that video is downloaded.
        """
        partials = self._scrape_pages()
        print(f"# Total: {len(partials)} uploaded videos", file=sys.stderr)
        return partials
