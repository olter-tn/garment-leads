"""Text parsing, phone normalization, and intent classification."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Final

import phonenumbers
import yaml
from phonenumbers import PhoneNumber, PhoneNumberFormat, PhoneNumberType

ARABIC_DIGIT_TRANSLATION: Final[Mapping[int, int | str | None]] = str.maketrans(
    "٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹",
    "01234567890123456789",
)
VALID_TN_PREFIXES: Final[tuple[str, ...]] = (
    "20", "21", "22", "23", "24", "25", "26", "27", "28", "29",
    "30", "31", "32", "33", "34", "35", "36", "37", "38", "39",
    "40", "41", "42", "43", "44", "45", "46", "47", "48", "49",
    "50", "51", "52", "53", "54", "55", "56", "57", "58", "59",
    "70", "71", "72", "73", "74", "75", "76", "77", "78", "79",
    "90", "91", "92", "93", "94", "95", "96", "97", "98", "99",
)
PHONE_MARKER_RE: Final[re.Pattern[str]] = re.compile(
    r"(?i)(?:tel|tél|telephone|téléphone|phone|whatsapp|watsap|واتساب|هاتف|تلفون|اتصل|☎|📞)\s*[:：\-]?\s*$"
)
DATE_RE: Final[re.Pattern[str]] = re.compile(r"^\s*\d{1,2}[./\-]\d{1,2}[./\-](?:20)?\d{2}\s*$")
SIZE_CONTEXT_RE: Final[re.Pattern[str]] = re.compile(
    r"(?i)(taille|tailles|size|sizes|mesure|mesures|cm|mètre|metre|pcs|pi[eè]ces|quantit[eé]|quantity|مقاس|مقاسات|قياس|كمية|قطع)"
)
PRICE_CONTEXT_RE: Final[re.Pattern[str]] = re.compile(
    r"(?i)(prix|price|dt|tnd|dinars?|دينار|السوم|الثمن|tarif|د)"
)
LINK_RE: Final[re.Pattern[str]] = re.compile(r"https?://\S+", re.IGNORECASE)
WHITESPACE_RE: Final[re.Pattern[str]] = re.compile(r"\s+")
SENTENCE_SPLIT_RE: Final[re.Pattern[str]] = re.compile(r"(?<=[.!?؟؛\n])\s+")
NEED_RE: Final[re.Pattern[str]] = re.compile(
    r"(?i)(cherche|recherche|besoin|needed?|looking for|want(?:ed)?|مطلوب|نلوج|نحب|نحتاج|يلزم|انتداب|ننتدب|نشرى|نشتري|نبغي|نلقا|فما|شكون|وين|كيفاش|بقداش|قداش|سعر|ثمن|combien|prix|price|dispo\?|available\?)"
)
OFFER_RE: Final[re.Pattern[str]] = re.compile(
    r"(?i)(atelier|usine|factory|sous[- ]?trait|fa[çc]on|tissu|fabric|couture|confection|نخدم|نقبل|نوفر|متوفر|للبيع|نبيع|خياطة|خياط|خيط|مصنع|معمل|قماش|موداليست|modeliste|styliste|designer|couturier|needle|tricot|knit|crochet|تريكو|حياكة|تفصيل|تفصيلة|مرحبا\s+بيك|مرحبا\s+في|ااجا|انخيط|اخيط|نخيط|نخدم|na5dem|na5demlek|n5dm|disponible|available|j'ai|3andi|عندي|موجود)"
)
NAME_PATTERNS: Final[tuple[re.Pattern[str], ...]] = (
    re.compile(r"(?:^|\b)(?:atelier|soci[eé]t[eé]|ste|factory|usine|contact|chez)\s+([A-Za-zÀ-ÿ][A-Za-zÀ-ÿ' .\-]{2,60})", re.IGNORECASE),
    re.compile(r"(?:اسمي|أنا|انا|معكم|السيد|السيدة|شركة|مصنع|معمل|ورشة|أتيلي|اتيلي)\s*[:\-،]?\s*([\u0600-\u06FFA-Za-zÀ-ÿ][\u0600-\u06FFA-Za-zÀ-ÿ' .\-]{2,60})", re.IGNORECASE),
    re.compile(r"(?:name|nom)\s*[:\-]\s*([\u0600-\u06FFA-Za-zÀ-ÿ][\u0600-\u06FFA-Za-zÀ-ÿ' .\-]{2,60})", re.IGNORECASE),
)
VALID_INTENTS: Final[tuple[str, ...]] = (
    "atelier",
    "factory",
    "subcontractor",
    "fabric_supplier",
    "job_offer",
    "other",
)
INTENT_PRIORITY: Final[dict[str, int]] = {
    "job_offer": 5,
    "subcontractor": 4,
    "fabric_supplier": 3,
    "factory": 2,
    "atelier": 1,
    "other": 0,
}


@dataclass(frozen=True)
class PhoneCandidate:
    """A phone-like candidate with raw and normalized representations."""

    raw: str
    e164: str
    valid: bool
    region: str
    kind: str


@dataclass(frozen=True)
class IntentRule:
    """A transparent keyword rule loaded from the YAML rule file."""

    intent: str
    language: str
    keywords: tuple[str, ...]
    weight: float
    require_phone: bool
    exclude_keywords: tuple[str, ...]


@dataclass(frozen=True)
class IntentResult:
    """Intent classification result with confidence and rule evidence."""

    intent: str
    confidence: float
    matched_keywords: list[str]
    raw_excerpt: str


@dataclass(frozen=True)
class ParsedLead:
    """A normalized contact lead extracted from a single post."""

    phone_raw: str
    phone_normalized: str
    name: str | None
    what: str
    why: str
    intent: str
    intent_confidence: float
    intent_matched_keywords: list[str]
    intent_raw_excerpt: str
    links: list[str]
    source_group_id: str
    raw_text: str
    # v3 additions (logged-in HTML carries more data)
    author: str | None = None
    timestamp_unix: int | None = None
    permalink: str | None = None
    reactions_count: int | None = None
    comments_count: int | None = None


def convert_arabic_indic_digits(text: str) -> str:
    """Convert Arabic-Indic and Eastern Arabic-Indic digits to ASCII digits."""

    return text.translate(ARABIC_DIGIT_TRANSLATION)


def compact_text(text: str) -> str:
    """Normalize whitespace and remove links from a post text."""

    without_links = LINK_RE.sub(" ", text)
    return WHITESPACE_RE.sub(" ", without_links).strip()


# ---------------- v3: logged-in HTML enrichment helpers ----------------

# Best-effort patterns. FB markup changes often — keep these conservative.
TIMESTAMP_ABS_RE: Final[re.Pattern[str]] = re.compile(r"data-utime=\"(\d{10,11})\"")
TIMESTAMP_REL_HINT_RE: Final[re.Pattern[str]] = re.compile(
    r"(?i)(\d+\s*(?:min|minute|minutes|hour|hours|h|day|days|semaine|semaines|mois|ساعة|ساعات|دقيقة|دقائق|يوم|أيام)\b)"
)
ABBR_TITLE_RE: Final[re.Pattern[str]] = re.compile(r"<abbr[^>]*title=\"([^\"]+)\"")
NUMBER_PARENS_RE: Final[re.Pattern[str]] = re.compile(r"\(([\d,]+)\)")
REACTION_LABEL_RE: Final[re.Pattern[str]] = re.compile(
    r"(?i)(?:aria-label|title)=\"([^\"]*?reactions?[^\"]*?)\""
)
COMMENT_HINT_RE: Final[re.Pattern[str]] = re.compile(
    r"(?i)(\d+)\s*(?:comment|comments|تعليق|تعليقات)"
)


def extract_author_from_html(post_html: str) -> str | None:
    """Pull the author display name from a logged-in FB post block.

    Looks for href containing profile.php?id= or /user/ near the top of the block.
    Returns None when no plausible author is found.
    """

    if not post_html:
        return None
    # Look for the first anchor with a profile or hovercard-style href
    match = re.search(
        r"<a[^>]+href=\"[^\"]*(?:profile\.php\?id=|/user/|/groups/[^/]+/user/)[^\"]*\"[^>]*>([^<]{2,80})</a>",
        post_html,
        re.IGNORECASE,
    )
    if match:
        return compact_text(match.group(1)) or None
    # Fallback: data-hovercard-target-id (sometimes present)
    match = re.search(
        r"data-hovercard[^\"]*\"[^\"]*\"[^>]*>([^<]{2,80})<",
        post_html,
        re.IGNORECASE,
    )
    if match:
        return compact_text(match.group(1)) or None
    return None


def extract_permalink_from_html(post_html: str, group_id: str) -> str | None:
    """Find the permalink URL inside a logged-in post block."""

    if not post_html:
        return None
    patterns = [
        rf"href=\"(https?://[^\"]*?/{group_id}/permalink/[^\"]+)\"",
        rf"href=\"(https?://[^\"]*?/{group_id}/posts/[^\"]+)\"",
        r"href=\"(https?://[^\"]*?/story\.php\?[^\"]*story_fbid=[^\"]+)\"",
    ]
    for pat in patterns:
        match = re.search(pat, post_html, re.IGNORECASE)
        if match:
            return match.group(1)
    return None


def extract_timestamp_unix_from_html(post_html: str) -> int | None:
    """Pull the unix timestamp (seconds) from a logged-in FB post block."""

    if not post_html:
        return None
    match = TIMESTAMP_ABS_RE.search(post_html)
    if match:
        try:
            return int(match.group(1))
        except ValueError:
            pass
    # Fallback: <abbr title="..."> with a parseable ISO-ish string
    match = ABBR_TITLE_RE.search(post_html)
    if match:
        try:
            from datetime import datetime
            return int(datetime.fromisoformat(match.group(1).replace("Z", "+00:00")).timestamp())
        except (ValueError, ImportError):
            return None
    return None


def extract_reactions_count_from_html(post_html: str) -> int | None:
    """Pull the reactions count from a reactions tooltip / label."""

    if not post_html:
        return None
    match = REACTION_LABEL_RE.search(post_html)
    if match:
        # Find the first number in the matched label
        nums = re.findall(r"\d+", match.group(1))
        if nums:
            try:
                return int(nums[0])
            except ValueError:
                return None
    return None


def extract_comments_count_from_html(post_html: str) -> int | None:
    """Pull the comments count from a comment link/text."""

    if not post_html:
        return None
    match = COMMENT_HINT_RE.search(post_html)
    if match:
        try:
            return int(match.group(1))
        except ValueError:
            return None
    return None


def extract_links(text: str) -> list[str]:
    """Extract HTTP links from text while trimming trailing punctuation."""

    return [link.rstrip(".,؛،)؟]") for link in LINK_RE.findall(text)]


def _digits_only(value: str) -> str:
    return "".join(ch for ch in convert_arabic_indic_digits(value) if ch.isdigit())


def _context(text: str, start: int, end: int, radius: int = 28) -> str:
    return text[max(0, start - radius): min(len(text), end + radius)]


def _has_phone_marker_before(text: str, start: int) -> bool:
    before = text[max(0, start - 24): start]
    return PHONE_MARKER_RE.search(before) is not None


def _is_date(raw: str) -> bool:
    normalized = convert_arabic_indic_digits(raw).strip()
    if not DATE_RE.match(normalized):
        return False
    parts = re.split(r"[./\-]", normalized)
    if len(parts) != 3:
        return False
    day, month = int(parts[0]), int(parts[1])
    return 1 <= day <= 31 and 1 <= month <= 12


def _looks_like_grouped_sizes(raw: str, context: str) -> bool:
    normalized = convert_arabic_indic_digits(raw)
    groups = re.findall(r"\d+", normalized)
    if len(groups) >= 4 and all(len(group) == 2 for group in groups[:4]) and SIZE_CONTEXT_RE.search(context):
        return True
    return False


def _is_obvious_false_positive(raw: str, original_text: str, start: int, end: int, e164: str) -> bool:
    digits = _digits_only(raw)
    ctx = _context(original_text, start, end)
    if _is_date(raw):
        return True
    if len(digits) not in {8, 11, 13}:
        return True
    if e164.startswith("+216") and e164[4:6] not in VALID_TN_PREFIXES:
        return True
    if _looks_like_grouped_sizes(raw, ctx):
        return True
    if PRICE_CONTEXT_RE.search(ctx) and not _has_phone_marker_before(original_text, start):
        # Phone-like numbers immediately presented as a price are not contacts.
        return True
    return False


def _phone_kind(number: PhoneNumber) -> str:
    kind = phonenumbers.number_type(number)
    if kind == PhoneNumberType.MOBILE:
        return "mobile"
    if kind == PhoneNumberType.FIXED_LINE:
        return "landline"
    if kind == PhoneNumberType.FIXED_LINE_OR_MOBILE:
        return "fixed_or_mobile"
    return "unknown"


def _candidate_from_match(match: phonenumbers.PhoneNumberMatch, original_text: str, region: str) -> PhoneCandidate | None:
    e164 = phonenumbers.format_number(match.number, PhoneNumberFormat.E164)
    raw = original_text[match.start: match.end]
    if not phonenumbers.is_valid_number(match.number):
        return None
    if _is_obvious_false_positive(raw, original_text, match.start, match.end, e164):
        return None
    return PhoneCandidate(raw=raw.strip(), e164=e164, valid=True, region=region, kind=_phone_kind(match.number))


def extract_phones(text: str) -> list[PhoneCandidate]:
    """Extract validated phone candidates, normalized to E.164 when possible.

    The function uses libphonenumber as the primary recognizer with region fallbacks
    TN, MA, then ZZ. Arabic-Indic digits are converted before recognition while
    raw candidates preserve the original visible phone string.
    """

    normalized_text = convert_arabic_indic_digits(text)
    seen: set[str] = set()
    results: list[PhoneCandidate] = []
    for region in ("TN", "MA", "ZZ"):
        try:
            matches = phonenumbers.PhoneNumberMatcher(normalized_text, region)
        except Exception:
            continue
        for match in matches:
            candidate = _candidate_from_match(match, text, region)
            if candidate is None or candidate.e164 in seen:
                continue
            seen.add(candidate.e164)
            results.append(candidate)
    return results


@lru_cache(maxsize=1)
def load_intent_rules(path: str | Path | None = None) -> tuple[IntentRule, ...]:
    """Load transparent intent rules from garment_leads/data/intent_rules.yml."""

    rules_path = Path(path) if path is not None else Path(__file__).resolve().parent / "data" / "intent_rules.yml"
    data = yaml.safe_load(rules_path.read_text(encoding="utf-8")) or {}
    languages = data.get("languages", {})
    rules: list[IntentRule] = []
    if not isinstance(languages, dict):
        raise ValueError("intent_rules.yml must contain a 'languages' mapping")
    for language, items in languages.items():
        if not isinstance(items, list):
            continue
        for item in items:
            if not isinstance(item, dict):
                continue
            intent = str(item.get("intent", "other"))
            if intent not in VALID_INTENTS:
                raise ValueError(f"Invalid intent in rule file: {intent}")
            keywords = tuple(str(keyword) for keyword in item.get("keywords", []) if str(keyword).strip())
            rules.append(
                IntentRule(
                    intent=intent,
                    language=str(item.get("language", language)),
                    keywords=keywords,
                    weight=float(item.get("weight", 1.0)),
                    require_phone=bool(item.get("require_phone", False)),
                    exclude_keywords=tuple(str(keyword) for keyword in item.get("exclude_keywords", []) if str(keyword).strip()),
                )
            )
    return tuple(rules)


def _contains_keyword(haystack: str, keyword: str) -> bool:
    return keyword.casefold() in haystack


def _excerpt_for_keyword(text: str, keyword: str, radius: int = 60) -> str:
    lowered = text.casefold()
    idx = lowered.find(keyword.casefold())
    if idx < 0:
        return compact_text(text)[:160]
    start = max(0, idx - radius)
    end = min(len(text), idx + len(keyword) + radius)
    excerpt = text[start:end]
    if start > 0:
        excerpt = "…" + excerpt
    if end < len(text):
        excerpt += "…"
    return compact_text(excerpt)[:200]


def classify_intent(text: str, has_phone: bool | None = None) -> IntentResult:
    """Classify post intent using weighted YAML keyword rules and return evidence."""

    normalized = compact_text(convert_arabic_indic_digits(text)).casefold()
    phone_present = bool(extract_phones(text)) if has_phone is None else has_phone
    scores: dict[str, float] = {intent: 0.0 for intent in VALID_INTENTS}
    matched: dict[str, list[str]] = {intent: [] for intent in VALID_INTENTS}
    first_keyword = ""

    for rule in load_intent_rules():
        if rule.require_phone and not phone_present:
            continue
        if any(_contains_keyword(normalized, excluded.casefold()) for excluded in rule.exclude_keywords):
            continue
        rule_matches = [keyword for keyword in rule.keywords if _contains_keyword(normalized, keyword.casefold())]
        if not rule_matches:
            continue
        scores[rule.intent] += rule.weight * min(len(rule_matches), 2)
        matched[rule.intent].extend(rule_matches)
        if not first_keyword:
            first_keyword = rule_matches[0]

    best_intent = max(VALID_INTENTS, key=lambda intent: (scores[intent], INTENT_PRIORITY[intent]))
    if scores[best_intent] <= 0:
        return IntentResult(intent="other", confidence=0.0, matched_keywords=[], raw_excerpt=compact_text(text)[:200])
    total_score = sum(scores.values())
    confidence = round(scores[best_intent] / total_score if total_score else 0.0, 3)
    return IntentResult(
        intent=best_intent,
        confidence=confidence,
        matched_keywords=list(dict.fromkeys(matched[best_intent])),
        raw_excerpt=_excerpt_for_keyword(text, matched[best_intent][0] if matched[best_intent] else first_keyword),
    )


def extract_name(text: str) -> str | None:
    """Extract a likely person/company/atelier name from text."""

    cleaned = compact_text(text)
    for pattern in NAME_PATTERNS:
        match = pattern.search(cleaned)
        if not match:
            continue
        name = match.group(1).strip(" -،:؛.\n\t")
        name = re.split(r"(?:\s{2,}|[.!?؟؛]|tel|tél|phone|☎|📞)", name, flags=re.IGNORECASE)[0].strip()
        if 2 <= len(name) <= 80:
            return name
    return None


def _first_sentence_matching(text: str, pattern: re.Pattern[str]) -> str:
    cleaned = compact_text(text)
    sentences = [part.strip() for part in SENTENCE_SPLIT_RE.split(cleaned) if part.strip()]
    for sentence in sentences:
        if pattern.search(sentence):
            return sentence[:280]
    return cleaned[:280]


def extract_what(text: str, intent: str) -> str:
    """Extract a short description of what the poster offers or advertises."""

    sentence = _first_sentence_matching(text, OFFER_RE)
    if sentence:
        return sentence
    return f"Detected intent: {intent}"


def extract_why(text: str) -> str:
    """Extract a short description of the poster's need or motivation."""

    sentence = _first_sentence_matching(text, NEED_RE)
    return sentence or "Not explicitly stated"


def parse_post_text(
    text: str,
    source_group_id: str,
    link: str | None = None,
    *,
    author: str | None = None,
    timestamp_unix: int | None = None,
    permalink: str | None = None,
    reactions_count: int | None = None,
    comments_count: int | None = None,
) -> list[ParsedLead]:
    """Parse one post body into zero or more normalized leads.

    Posts without valid contact numbers intentionally return an empty list so they
    are skipped by the scraper without crashing.

    v3: author/timestamp/permalink/reactions/comments are propagated into the lead
    when the scraper extracts them from logged-in HTML. All are optional.
    """

    phones = extract_phones(text)
    if not phones:
        return []
    intent = classify_intent(text, has_phone=True)
    links = extract_links(text)
    if link:
        links.insert(0, link)
    if permalink and permalink not in links:
        links.insert(0, permalink)
    links = list(dict.fromkeys(links))
    name = extract_name(text)
    what = extract_what(text, intent.intent)
    why = extract_why(text)
    leads: list[ParsedLead] = []
    for phone in phones:
        leads.append(
            ParsedLead(
                phone_raw=phone.raw,
                phone_normalized=phone.e164,
                name=name,
                what=what,
                why=why,
                intent=intent.intent,
                intent_confidence=intent.confidence,
                intent_matched_keywords=intent.matched_keywords,
                intent_raw_excerpt=intent.raw_excerpt,
                links=links,
                source_group_id=source_group_id,
                raw_text=compact_text(text),
                author=author,
                timestamp_unix=timestamp_unix,
                permalink=permalink,
                reactions_count=reactions_count,
                comments_count=comments_count,
            )
        )
    return leads


def parse_fixture_payload(payload: dict[str, Any], source_group_id: str = "fixture") -> list[ParsedLead]:
    """Parse a fixture JSON payload into leads; useful for CLI validation."""

    return parse_post_text(str(payload.get("text", "")), source_group_id=source_group_id, link=None)


def intent_result_to_json(result: IntentResult) -> str:
    """Serialize an IntentResult for diagnostics."""

    return json.dumps(
        {
            "intent": result.intent,
            "confidence": result.confidence,
            "matched_keywords": result.matched_keywords,
            "raw_excerpt": result.raw_excerpt,
        },
        ensure_ascii=False,
    )
