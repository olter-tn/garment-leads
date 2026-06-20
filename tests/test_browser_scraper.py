"""Tests for the browser_scraper module.

Tests are designed to run without a real browser — they test the
HTML parsing functions, data structures, and keyword matching logic.
"""

from __future__ import annotations

import pytest

from garment_leads.browser_scraper import (
    BrowserScrapeResult,
    CommentData,
    PostData,
    _extract_comments_from_html,
    _extract_post_from_html,
    _extract_post_url_and_id,
)
from garment_leads.keywords import (
    KW_CLOSE_HIDE,
    KW_SEE_MORE_TEXT,
    KW_SORT_ALL,
    KW_SORT_CURRENT,
    KW_VIEW_MORE_COMMENTS,
    KW_VIEW_REPLIES,
    _fold,
    text_matches_any_keyword,
)


# ---------------- Dataclass tests ----------------


class TestPostData:
    def test_post_data_creation(self) -> None:
        post = PostData(
            post_fb_id="123456",
            permalink="https://www.facebook.com/groups/123/posts/456",
            author="Ahmed",
            author_fb_id=None,
            text="Looking for atelier subcontractor",
            timestamp="2025-01-15T10:00:00+00:00",
            timestamp_unix=1736935200,
            image_url=None,
            author_profile_pic=None,
            reactions_count=5,
            comments_count=3,
        )
        assert post.post_fb_id == "123456"
        assert post.author == "Ahmed"
        assert post.comments == []

    def test_post_data_with_comments(self) -> None:
        comment = CommentData(
            comment_fb_id="c1",
            author="Sami",
            author_fb_id=None,
            text="I have an atelier",
            timestamp=None,
            timestamp_unix=None,
            is_reply=False,
            parent_comment_fb_id=None,
            commenter_profile_pic=None,
        )
        post = PostData(
            post_fb_id="p1",
            permalink="https://www.facebook.com/groups/123/posts/456",
            author="Ahmed",
            author_fb_id=None,
            text="Looking for atelier",
            timestamp=None,
            timestamp_unix=None,
            image_url=None,
            author_profile_pic=None,
            reactions_count=None,
            comments_count=None,
            comments=[comment],
        )
        assert len(post.comments) == 1
        assert post.comments[0].author == "Sami"


class TestCommentData:
    def test_comment_data_defaults(self) -> None:
        c = CommentData(
            comment_fb_id=None,
            author=None,
            author_fb_id=None,
            text="",
            timestamp=None,
            timestamp_unix=None,
            is_reply=False,
            parent_comment_fb_id=None,
            commenter_profile_pic=None,
        )
        assert c.is_reply is False
        assert c.commenter_profile_pic is None

    def test_comment_is_reply(self) -> None:
        c = CommentData(
            comment_fb_id="r1",
            author="Replier",
            author_fb_id=None,
            text="Reply text",
            timestamp=None,
            timestamp_unix=None,
            is_reply=True,
            parent_comment_fb_id="c1",
            commenter_profile_pic=None,
        )
        assert c.is_reply is True
        assert c.parent_comment_fb_id == "c1"


# ---------------- URL extraction tests ----------------


class TestExtractPostUrlAndId:
    def test_posts_url(self) -> None:
        html = '<div><a href="https://www.facebook.com/groups/123/posts/456789">Post</a></div>'
        url, pid = _extract_post_url_and_id(html)
        assert url is not None
        assert "456789" in url or pid == "456789"
        assert pid == "456789"

    def test_permalink_url(self) -> None:
        html = '<div><a href="https://www.facebook.com/groups/123/permalink/987654321/">Post</a></div>'
        url, pid = _extract_post_url_and_id(html)
        assert pid == "987654321"

    def test_story_fbid_query(self) -> None:
        html = '<div><a href="https://www.facebook.com/story.php?story_fbid=111222333">Post</a></div>'
        url, pid = _extract_post_url_and_id(html)
        assert pid == "111222333"

    def test_no_link(self) -> None:
        html = "<div><p>No link here</p></div>"
        url, pid = _extract_post_url_and_id(html)
        assert url is None
        assert pid is None

    def test_photos_url(self) -> None:
        html = '<div><a href="https://www.facebook.com/groups/123/photos/4455667788">Photo post</a></div>'
        url, pid = _extract_post_url_and_id(html)
        assert pid == "4455667788"


# ---------------- Post HTML extraction tests ----------------


class TestExtractPostFromHtml:
    def test_minimal_post(self) -> None:
        html = """
        <div role="article">
            <h2><strong>Ahmed Ben Ali</strong></h2>
            <div data-ad-rendering-role="story_message">
                <div>Cherche atelier sous-traitant pour 500 pieces</div>
            </div>
            <abbr title="January 15, 2025 at 10:00 AM">Jan 15</abbr>
        </div>
        """
        post = _extract_post_from_html(html, None, None)
        assert post is not None
        assert post.author == "Ahmed Ben Ali"
        assert "atelier" in post.text.lower() or "500" in post.text
        assert post.timestamp is not None

    def test_post_with_url_id(self) -> None:
        html = """
        <div role="article">
            <h2><strong>Sami</strong></h2>
            <div data-ad-rendering-role="story_message"><div>Hello world</div></div>
            <a href="https://www.facebook.com/groups/123/posts/456">link</a>
        </div>
        """
        post = _extract_post_from_html(
            html,
            "https://www.facebook.com/groups/123/posts/456",
            "456",
        )
        assert post is not None
        assert post.post_fb_id == "456"
        assert post.permalink is not None

    def test_empty_html_returns_none(self) -> None:
        post = _extract_post_from_html("<div></div>", None, None)
        assert post is None

    def test_generated_post_id_when_missing(self) -> None:
        html = """
        <div role="article">
            <h2><strong>Test Author</strong></h2>
            <div data-ad-rendering-role="story_message"><div>Some text content here</div></div>
        </div>
        """
        post = _extract_post_from_html(html, None, None)
        assert post is not None
        assert post.post_fb_id is not None
        assert post.post_fb_id.startswith("gen_")

    def test_arabic_text(self) -> None:
        html = """
        <div role="article">
            <h2><strong>محمد</strong></h2>
            <div data-ad-rendering-role="story_message">
                <div>نحتاج مصنع للخياطة بمقدار 1000 قطعة</div>
            </div>
        </div>
        """
        post = _extract_post_from_html(html, None, None)
        assert post is not None
        assert post.author == "محمد"
        assert "مصنع" in post.text


# ---------------- Comment HTML extraction tests ----------------


class TestExtractCommentsFromHtml:
    def test_empty_html(self) -> None:
        comments = _extract_comments_from_html("<div></div>")
        assert comments == []

    def test_single_comment(self) -> None:
        html = """
        <div aria-label="Comment by Sami">
            <a href="/user/123"><span>Sami</span></a>
            <div data-ad-preview="message"><span>I have an atelier in Tunis</span></div>
            <abbr title="January 15, 2025 at 2:00 PM">2h</abbr>
            <a href="/comment/?comment_id=888">link</a>
        </div>
        """
        comments = _extract_comments_from_html(html)
        assert len(comments) == 1
        assert comments[0].author == "Sami"
        assert "atelier" in comments[0].text.lower()
        assert comments[0].comment_fb_id == "888"

    def test_dedup_by_comment_id(self) -> None:
        html = """
        <div aria-label="Comment by Sami">
            <a href="/user/123"><span>Sami</span></a>
            <div data-ad-preview="message"><span>First comment</span></div>
            <a href="/comment/?comment_id=999">link</a>
        </div>
        <div aria-label="Comment by Sami">
            <a href="/user/123"><span>Sami</span></a>
            <div data-ad-preview="message"><span>First comment</span></div>
            <a href="/comment/?comment_id=999">link</a>
        </div>
        """
        comments = _extract_comments_from_html(html)
        assert len(comments) == 1

    def test_comment_without_name_kept_if_text(self) -> None:
        """A comment with text but no name should still be kept —
        the name selectors might not match all FB markup variants."""
        html = """
        <div aria-label="Comment by">
            <div data-ad-preview="message"><span>text only</span></div>
        </div>
        """
        comments = _extract_comments_from_html(html)
        # The function keeps comments with text even without a name
        # (matching FBScrapeIdeas behavior)
        assert len(comments) == 1
        assert comments[0].text == "text only"

    def test_comment_without_name_or_text_skipped(self) -> None:
        """A comment with neither name nor text should be skipped."""
        html = """
        <div aria-label="Comment by">
            <div></div>
        </div>
        """
        comments = _extract_comments_from_html(html)
        assert len(comments) == 0

    def test_multiple_comments(self) -> None:
        html = """
        <div aria-label="Comment by Ali">
            <a href="/user/1"><span>Ali</span></a>
            <div data-ad-preview="message"><span>Comment 1</span></div>
            <a href="/comment/?comment_id=1">l</a>
        </div>
        <div aria-label="Comment by Bob">
            <a href="/user/2"><span>Bob</span></a>
            <div data-ad-preview="message"><span>Comment 2</span></div>
            <a href="/comment/?comment_id=2">l</a>
        </div>
        <div aria-label="Comment by Carl">
            <a href="/user/3"><span>Carl</span></a>
            <div data-ad-preview="message"><span>Comment 3</span></div>
            <a href="/comment/?comment_id=3">l</a>
        </div>
        """
        comments = _extract_comments_from_html(html)
        assert len(comments) == 3
        authors = [c.author for c in comments]
        assert "Ali" in authors
        assert "Bob" in authors
        assert "Carl" in authors


# ---------------- Keyword matching tests ----------------


class TestKeywordMatching:
    def test_fold_accent(self) -> None:
        assert _fold("réponse") == "reponse"
        assert _fold("RÉPONSE") == "reponse"
        assert _fold("café") == "cafe"

    def test_fold_arabic(self) -> None:
        assert _fold("الردود") == "الردود"

    def test_view_more_comments_en(self) -> None:
        assert text_matches_any_keyword("View 12 more comments", KW_VIEW_MORE_COMMENTS)

    def test_view_more_comments_fr(self) -> None:
        assert text_matches_any_keyword("Voir plus de commentaires", KW_VIEW_MORE_COMMENTS)
        assert text_matches_any_keyword("Afficher plus de commentaires", KW_VIEW_MORE_COMMENTS)

    def test_view_more_comments_ar(self) -> None:
        assert text_matches_any_keyword("عرض المزيد من التعليقات", KW_VIEW_MORE_COMMENTS)

    def test_view_replies_en(self) -> None:
        assert text_matches_any_keyword("View 3 replies", KW_VIEW_REPLIES)
        assert text_matches_any_keyword("View all replies", KW_VIEW_REPLIES)

    def test_view_replies_fr_accent(self) -> None:
        assert text_matches_any_keyword("Voir plus de réponses", KW_VIEW_REPLIES)
        assert text_matches_any_keyword("Voir les 3 réponses", KW_VIEW_REPLIES)

    def test_view_replies_ar(self) -> None:
        assert text_matches_any_keyword("عرض الردود", KW_VIEW_REPLIES)

    def test_close_hide_en(self) -> None:
        assert text_matches_any_keyword("Close", KW_CLOSE_HIDE)
        assert text_matches_any_keyword("Cancel", KW_CLOSE_HIDE)

    def test_close_hide_fr(self) -> None:
        assert text_matches_any_keyword("Fermer", KW_CLOSE_HIDE)
        assert text_matches_any_keyword("Annuler", KW_CLOSE_HIDE)

    def test_close_hide_ar(self) -> None:
        assert text_matches_any_keyword("إغلاق", KW_CLOSE_HIDE)

    def test_see_more_en(self) -> None:
        assert text_matches_any_keyword("See more", KW_SEE_MORE_TEXT)

    def test_see_more_fr(self) -> None:
        assert text_matches_any_keyword("Voir plus", KW_SEE_MORE_TEXT)

    def test_see_more_ar(self) -> None:
        assert text_matches_any_keyword("عرض المزيد", KW_SEE_MORE_TEXT)

    def test_sort_all_comments_en(self) -> None:
        assert text_matches_any_keyword("All comments", KW_SORT_ALL)

    def test_sort_all_comments_fr(self) -> None:
        assert text_matches_any_keyword("Tous les commentaires", KW_SORT_ALL)

    def test_sort_all_comments_ar(self) -> None:
        assert text_matches_any_keyword("كل التعليقات", KW_SORT_ALL)

    def test_sort_current_en(self) -> None:
        assert text_matches_any_keyword("Most relevant", KW_SORT_CURRENT)
        assert text_matches_any_keyword("Newest", KW_SORT_CURRENT)

    def test_negative_match(self) -> None:
        assert not text_matches_any_keyword("Submit reply", KW_VIEW_MORE_COMMENTS)
        assert not text_matches_any_keyword("Write a comment", KW_VIEW_REPLIES)

    def test_empty_text(self) -> None:
        assert not text_matches_any_keyword("", KW_VIEW_MORE_COMMENTS)
        assert not text_matches_any_keyword(None, KW_VIEW_REPLIES)  # type: ignore[arg-type]


# ---------------- BrowserScrapeResult tests ----------------


class TestBrowserScrapeResult:
    def test_empty_result(self) -> None:
        result = BrowserScrapeResult(posts=[], total_comments=0, raw_html_saved=None)
        assert result.posts == []
        assert result.total_comments == 0
        assert result.error is None

    def test_with_error(self) -> None:
        result = BrowserScrapeResult(
            posts=[], total_comments=0, raw_html_saved=None, error="ConnectionError"
        )
        assert result.error == "ConnectionError"

    def test_with_posts(self) -> None:
        post = PostData(
            post_fb_id="p1",
            permalink=None,
            author=None,
            author_fb_id=None,
            text="test",
            timestamp=None,
            timestamp_unix=None,
            image_url=None,
            author_profile_pic=None,
            reactions_count=None,
            comments_count=None,
        )
        result = BrowserScrapeResult(posts=[post], total_comments=0, raw_html_saved="/tmp/x.html")
        assert len(result.posts) == 1
        assert result.raw_html_saved == "/tmp/x.html"