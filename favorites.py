"""
favorites.py — scrape ThisVid favourite video listings.
"""

import re
import sys

from backend import _re1, get_total_pages, sleep

FAVS_URL = "https://thisvid.com/my_favourite_videos/"


class FavoritesScraper:
    """Scrapes all pages of a ThisVid favourite (or any path-paginated) listing."""

    def __init__(self, client, delay):
        self.client = client
        self.delay = delay

    @staticmethod
    def _parse_videos(html):
        """Return a list of video dicts from a listing page."""
        videos = []
        for block in re.findall(
            r'<div class="thumb-holder">(.*?)</div>\s*</div>\s*</div>', html, re.DOTALL
        ):
            url = _re1(r'<a href="(https://thisvid\.com/videos/[^"]+)"', block)
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

    def fetch(self, listing_url=FAVS_URL, label="favourites"):
        """Scrape all pages of a listing. Returns a list of video dicts."""
        all_videos = []
        seen = set()

        resp = self.client.get(listing_url, timeout=20)
        resp.raise_for_status()

        base_path = "/" + listing_url.split("thisvid.com/", 1)[1]
        total = get_total_pages(resp.text, base_path)
        print(f"# {label.capitalize()}: {total} page(s)", file=sys.stderr)

        def collect(html):
            for v in self._parse_videos(html):
                if v["url"] not in seen:
                    seen.add(v["url"])
                    all_videos.append(v)

        collect(resp.text)
        print(f"#  page 1/{total}: {len(all_videos)} videos", file=sys.stderr)

        for page in range(2, total + 1):
            sleep(self.delay)
            resp = self.client.get(f"https://thisvid.com{base_path}{page}/", timeout=20)
            resp.raise_for_status()
            before = len(all_videos)
            collect(resp.text)
            print(f"#  page {page}/{total}: +{len(all_videos) - before} ({len(all_videos)} total)",
                  file=sys.stderr)

        print(f"# Total: {len(all_videos)} {label}", file=sys.stderr)
        return all_videos
