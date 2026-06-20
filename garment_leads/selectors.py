"""FB DOM selectors.

Selectors are inspired by /home/omar/Projects/FBScrapeIdeas (scraper/facebook_scraper.py).
Adapted for our Playwright-based pipeline and the current FB markup (2024-2026).
"""

from __future__ import annotations

# ---------------- Feed / scroller ----------------

FEED_OR_SCROLLER_CSS = "div[role='feed'], div[data-testid='post_scroller']"

# ---------------- Post-level selectors ----------------

POST_CONTAINER_CSS = (
    'div.x1yztbdb.x1n2onr6.xh8yej3.x1ja2u2z, '
    'div[role="article"], '
    'div[data-ad-preview="message"], '
    'div[data-pagelet^="FeedUnit_"]'
)

POST_PERMALINK_XPATH = (
    ".//a[contains(@href, '/posts/')] | "
    ".//a[contains(@href, '/permalink/')] | "
    ".//a[contains(@href, '/videos/')] | "
    ".//a[contains(@href, '/photos/')] | "
    ".//abbr/ancestor::a"
)

POST_TIMESTAMP_FALLBACK_XPATH = ".//abbr | .//a/span[@data-lexical-text='true']"

SEE_MORE_BUTTON_XPATH = (
    ".//div[@role='button'][contains(., 'See more') or contains(., 'Show more') "
    "or contains(., 'Voir plus') or contains(., 'Afficher plus') "
    "or contains(., 'عرض المزيد')] | "
    ".//a[contains(., 'See more') or contains(., 'Show more') "
    "or contains(., 'Voir plus') or contains(., 'Afficher plus') "
    "or contains(., 'عرض المزيد')]"
)

# Author name selectors (BeautifulSoup CSS)
AUTHOR_NAME_BS = (
    "h2 strong, "
    "h2 a[role='link'] strong, "
    "h3 strong, "
    "h3 a[role='link'] strong, "
    "a[aria-label][href*='/user/'] > strong, "
    "a[aria-label][href*='/profile.php'] > strong, "
    # anonymous author fallback
    "h2[id^=\"«r\"] strong object div, "
    # general fallback
    "a[href*='/groups/'][href*='/user/'] span, "
    "a[href*='/profile.php'] span, "
    "span > strong > a[role='link']"
)

# Author profile picture
AUTHOR_PIC_SVG_IMG_BS = "div:first-child svg image"
AUTHOR_PIC_IMG_BS = (
    'div:first-child img[alt*="profile picture"], '
    'div:first-child img[data-imgperflogname*="profile"]'
)
SPECIFIC_AUTHOR_PIC_BS = 'div[role="button"] svg image'
AUTHOR_PROFILE_PIC_BS = (
    f"{AUTHOR_PIC_SVG_IMG_BS}, {AUTHOR_PIC_IMG_BS}, {SPECIFIC_AUTHOR_PIC_BS}"
)

# Post text
POST_TEXT_CONTAINER_BS = (
    'div[data-ad-rendering-role="story_message"], '
    'div[data-ad-preview="message"], '
    'div[data-ad-comet-preview="message"]'
)
GENERIC_TEXT_DIV_BS = (
    'div[dir="auto"]:not([class*=" "]):not(:has(button)):not(:has(a[role="button"]))'
)

# Post image
POST_IMAGE_BS = (
    'img.x168nmei, '
    'div[data-imgperflogname="MediaGridPhoto"] img, '
    'div[style*="background-image"]'
)

# Post timestamp
POST_TIMESTAMP_ABBR_BS = "abbr[title]"
POST_TIMESTAMP_LINK_TEXT_BS = (
    'a[href*="/posts/"] span[data-lexical-text="true"], '
    'a[href*="/videos/"] span[data-lexical-text="true"], '
    'a[href*="/photos/"] span[data-lexical-text="true"]'
)

# ---------------- Comment-level selectors ----------------

COMMENT_CONTAINER_BS = (
    'div[aria-label*="Comment by"], '
    'div[aria-label*="Commentaire de"], '
    'div[aria-label*="تعليق بواسطة"], '
    'ul > li div[role="article"]'
)

# Reply / nested comment containers
REPLY_CONTAINER_CSS = (
    'div[aria-label^="Reply by"], '
    'div[aria-label^="Reponse de"], '
    'div[aria-label^="رد بواسطة"], '
    'ul ul > li div[role="article"]'
)

# Commenter profile pic
COMMENTER_PIC_SVG_IMG_BS = "svg image"
COMMENTER_PIC_IMG_BS = (
    'img[alt*="profile picture"], '
    'img[data-imgperflogname*="profile"]'
)
SPECIFIC_COMMENTER_PIC_BS = 'a[role="link"] svg image'
COMMENTER_PROFILE_PIC_BS = (
    f"{COMMENTER_PIC_SVG_IMG_BS}, {COMMENTER_PIC_IMG_BS}, {SPECIFIC_COMMENTER_PIC_BS}"
)

# Commenter name
COMMENTER_NAME_BS = (
    "a[href*='/user/'] span, "
    "a[href*='/profile.php'] span, "
    "span > a[role='link'] > span > span[dir='auto'], "
    "div[role='button'] > strong > span, "
    "a[aria-hidden='false'][role='link']"
)

# Comment text
COMMENT_TEXT_PRIMARY_BS = (
    'div[data-ad-preview="message"] > span, '
    "div[dir='auto'][style='text-align: start;']"
)
COMMENT_TEXT_CONTAINER_FALLBACK_BS = ".xmjcpbm.xtq9sad + div, .xv55zj0 + div"
COMMENT_ACTUAL_TEXT_FALLBACK_BS = "div[dir='auto'], span[dir='auto']"
COMMENT_TEXT_FALLBACK_BS = "div[dir='auto'], span[dir='auto']"

# Comment ID / timestamp
COMMENT_ID_LINK_BS = "a[href*='comment_id=']"
COMMENT_TIMESTAMP_ABBR_BS = "abbr[title]"
COMMENT_TIMESTAMP_LINK_BS = "a[aria-label*='Comment permalink']"

# ---------------- Overlay / dialog dismissal ----------------

OVERLAY_DIALOG_XPATHS = (
    "//div[@data-testid='dialog']",
    "//div[contains(@role, 'dialog') and contains(@aria-hidden, 'false')]",
    "//div[contains(@aria-label, 'Save your login info') and @role='dialog']",
    "//div[contains(@aria-label, 'Turn on notifications') and @role='dialog']",
    "//div[@aria-label='View site information' and @role='dialog']",
    "//div[@role='presentation' and contains(@class, 'overlay')]",
)

DISMISS_BUTTON_XPATHS = (
    ".//button[text()='Not Now']",
    ".//button[contains(text(),'Not now')]",
    ".//button[contains(text(),'Not Now')",
    ".//a[@aria-label='Close']",
    ".//button[@aria-label='Close']",
    ".//button[contains(@aria-label, 'close')]",
    ".//div[@role='button'][@aria-label='Close']",
    ".//button[contains(text(), 'Close')]",
    ".//button[contains(text(), 'Dismiss')]",
    ".//button[contains(text(), 'Later')]",
    ".//div[@role='button'][contains(text(), 'Not Now')]",
    ".//div[@role='button'][contains(text(), 'Later')]",
    ".//div[@aria-label='Close' and @role='button']",
    ".//i[@aria-label='Close dialog']",
)

# Permalink post scope selectors (modal dialog vs standalone)
POST_SCOPE_DIALOG_CSS = "div[role='dialog']"
POST_SCOPE_STANDALONE_CSS = "div[role='main'] div[role='article']"

# CSS for comment container counting (multilingual aria-labels)
COMMENT_CONTAINER_COUNT_JS = """
'div[aria-label*="Comment"], div[aria-label*="commentaire"], '
+ 'div[aria-label*="تعليق"], div[aria-label*="comentario"], '
+ 'div[aria-label*="Kommentar"], ul > li div[role="article"]'
"""