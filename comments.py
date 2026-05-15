"""
comments.py — fetch and parse ThisVid video comments and metadata.
"""

import re
import sys

from backend import _re1, sleep

COMMENT_FIELDS = ["video_id", "comment_id", "user_id", "username", "date", "rating", "text"]


class CommentFetcher:
    """Fetches all comments and metadata for a single video."""

    def __init__(self, client, delay):
        self.client = client
        self.delay = delay

    @staticmethod
    def parse_meta(html, video_id):
        """Return a metadata dict extracted from a video page.

        Fields are merged back into the video dict in fetch() so they appear
        in the main listing files alongside the basic scrape data.
        """
        duration_s = _re1(r'"duration"\s*:\s*"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?', html)
        if not duration_s:
            # Fallback: plain seconds in a JS variable
            duration_s = _re1(r"video_duration:\s*'?(\d+)'?", html)

        raw_desc = _re1(r'<ul class="description">.*?<li>\s*<p>(.*?)</p>', html)
        description = re.sub(r'\s+', ' ', re.sub(r'<[^>]+>', '', raw_desc)).strip()

        return {
            "video_id":    video_id,
            "duration":    _re1(r'"duration"\s*:\s*"(PT[^"]+)"', html) or duration_s,
            "upload_date": (_re1(r'"uploadDate"\s*:\s*"([^"]+)"', html)
                            or _re1(r'<meta[^>]+itemprop="uploadDate"[^>]+content="([^"]+)"', html)),
            "category":    _re1(r"video_categories:\s*'([^']*)'", html),
            "tags":        _re1(r"video_tags:\s*'([^']*)'", html),
            "description": description,
        }

    @staticmethod
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

    @staticmethod
    def is_unavailable(html):
        """Return True if the page has no video player (file removed from storage)."""
        return 'class="no-player"' in html

    def fetch(self, video):
        """Fetch all comment pages for a video.

        Merges metadata (duration, upload_date, category, tags, description)
        directly into the video dict. Sets video["unavailable"] = True if the
        video file has been removed (page exists but no player). Returns the
        list of comments.
        """
        url = video["url"]
        vid = video["id"]
        all_comments = []

        resp = self.client.get(url, timeout=20)
        resp.raise_for_status()

        if self.is_unavailable(resp.text):
            video["unavailable"] = True

        page_comments = self.parse_comments(resp.text, vid)
        all_comments.extend(page_comments)
        print(f"#   comments page 1: {len(page_comments)} comments", file=sys.stderr)

        meta = self.parse_meta(resp.text, vid)
        video.update({k: v for k, v in meta.items() if k != "video_id"})

        page = 2
        while True:
            m = re.search(r'data-parameters="sort_by:[^;]*;from:(\d+)"', resp.text)
            if not m or int(m.group(1)) < page:
                break
            sleep(self.delay)
            resp = self.client.get(url, params={
                "mode": "async", "function": "get_block",
                "block_id": "video_comments_video_comments",
                "sort_by": "", "from": m.group(1),
            }, timeout=20)
            resp.raise_for_status()
            new = self.parse_comments(resp.text, vid)
            if not new:
                break
            all_comments.extend(new)
            print(f"#   comments page {page}: {len(new)} comments", file=sys.stderr)
            page = int(m.group(1)) + 1

        return all_comments
