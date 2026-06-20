"""Multi-language keywords for FB comment/reply expansion buttons.

When a post has lots of comments, FB truncates them and shows buttons like:
- "View more comments"
- "View previous comments"
- "View 12 replies"

These keywords (adapted from FBScrapeIdeas) match those buttons
across English, French, Spanish, Arabic, German, Portuguese, Italian — the
target audience of the garment-leads use case is mostly French + Arabic.

Keyword matching folds accents (é → e) so "réponse" matches "reponse".
This is necessary because FB's UI is sometimes inconsistent about accents in
the same locale.
"""

from __future__ import annotations

import unicodedata

# "View more comments" / "View N more comments" / "Previous comments"
KW_VIEW_MORE_COMMENTS = (
    # English
    "view more comments", "view previous comments", "more comments",
    "previous comments", "view comments",
    # French
    "voir plus de commentaires", "afficher plus de commentaires",
    "afficher les commentaires precedents", "voir les commentaires precedents",
    "plus de commentaires", "commentaires precedents",
    "afficher d'autres commentaires", "voir d'autres commentaires",
    # Spanish
    "ver mas comentarios", "mas comentarios", "ver comentarios anteriores",
    # Arabic
    "عرض المزيد من التعليقات", "عرض تعليقات سابقة", "تعليقات سابقة",
    "عرض المزيد", "المزيد من التعليقات",
    # German
    "weitere kommentare anzeigen", "vorherige kommentare anzeigen",
    "mehr kommentare", "weitere kommentare",
    # Portuguese
    "ver mais comentarios", "comentarios anteriores",
    # Italian
    "visualizza altri commenti", "altri commenti", "commenti precedenti",
)

# "View N replies" / "View more replies" — keywords MUST contain a "view"
# verb (or its translation). Bare "Reply" / "Répondre" would match the
# per-comment composer button.
KW_VIEW_REPLIES = (
    # English
    "view replies", "view reply", "view more replies",
    "view previous replies", "view all replies",
    "show replies", "show more replies", "show all replies",
    "all replies", "more replies",
    # French (no accents for case-folded matching)
    "voir les reponses", "afficher les reponses", "voir plus de reponses",
    "afficher plus de reponses", "voir reponse", "afficher reponse",
    "voir d'autres reponses", "afficher d'autres reponses",
    "toutes les reponses", "plus de reponses",
    # "View N replies" — we also need patterns that match with a number
    # between the verb and the noun. Since substring matching can't handle
    # arbitrary numbers, the browser scraper's JS clicker uses regex_patterns
    # for these cases. But for text_matches_any_keyword, we add common forms.
    "view 1 reply", "view 2 replies", "view 3 replies",
    "view 4 replies", "view 5 replies", "view 6 replies",
    "view 7 replies", "view 8 replies", "view 9 replies",
    "view 10 replies", "view all replies",
    # French with numbers
    "voir 1 reponse", "voir 2 reponses", "voir 3 reponses",
    "voir 4 reponses", "voir 5 reponses", "voir 6 reponses",
    "voir 7 reponses", "voir 8 reponses", "voir 9 reponses",
    "voir 10 reponses", "voir les 1 reponse", "voir les 2 reponses",
    "voir les 3 reponses", "voir les 4 reponses", "voir les 5 reponses",
    "afficher 1 reponse", "afficher 2 reponses", "afficher 3 reponses",
    "afficher 4 reponses", "afficher 5 reponses",
    # Spanish
    "ver respuestas", "ver mas respuestas", "ver las respuestas",
    "mostrar respuestas", "mas respuestas",
    # Arabic
    "عرض الردود", "عرض الرد", "عرض المزيد من الردود",
    "عرض جميع الردود", "الردود السابقة", "المزيد من الردود",
    # German
    "antworten anzeigen", "weitere antworten anzeigen",
    "alle antworten anzeigen", "vorherige antworten anzeigen",
    "mehr antworten", "alle antworten",
    # Portuguese
    "ver respostas", "ver mais respostas", "mostrar respostas",
    "todas as respostas", "mais respostas",
    # Italian
    "visualizza risposte", "mostra risposte", "altre risposte",
    "tutte le risposte", "piu risposte",
)

# "All comments" sort-tab label / menu option
KW_ALL_COMMENTS_SORT = (
    "all comments", "tous les commentaires", "todos los comentarios",
    "alle kommentare", "todos os comentarios", "tutti i commenti",
    "جميع التعليقات", "كل التعليقات",
)

# Sort dropdown current label ("Most relevant", "Newest", "All comments")
KW_SORT_CURRENT = (
    "most relevant", "newest", "all comments", "most recent",
    "plus pertinents", "les plus recents", "tous les commentaires",
    "mas relevantes", "mas recientes", "todos los comentarios",
    "الأكثر صلة", "الأحدث", "كل التعليقات",
    "relevanteste", "neueste", "alle kommentare",
    "mais relevantes", "mais recentes", "todos os comentarios",
    "piu pertinenti", "piu recenti", "tutti i commenti",
)

# "All comments" menu option after opening the sort dropdown
KW_SORT_ALL = (
    "all comments",
    "tous les commentaires",
    "todos los comentarios",
    "كل التعليقات",
    "alle kommentare",
    "todos os comentarios",
    "tutti i commenti",
)

# Keywords that mean close/cancel/hide — NEVER click these.
KW_CLOSE_HIDE = (
    "close", "cancel", "hide", "dismiss",
    "fermer", "annuler", "masquer",
    "cerrar", "cancelar", "ocultar",
    "إغلاق", "الغاء", "إلغاء", "اخفاء",
    "schliessen", "abbrechen", "ausblenden",
    "fechar", "cancelar", "ocultar",
    "chiudi", "annulla", "nascondi",
)

# "See more" inside a long comment body or post text
KW_SEE_MORE_TEXT = (
    "see more", "show more",
    "voir plus", "afficher plus",
    "ver mas", "mostrar mas",
    "عرض المزيد", "اظهر المزيد",
    "mehr anzeigen",
    "ver mais", "mostrar mais",
    "altro", "mostra altro",
)

# Regex patterns for "View N more comments" style buttons (JS-compatible,
# case-insensitive). Used by the browser scraper's JS clicker.
REGEX_VIEW_MORE_COMMENTS = (
    r"view (\d+ )?(more |previous |all )?comments?",
    r"view comments?",
    r"voir (\d+ )?(plus de |precedents? |tous les )?commentaires?",
    r"afficher (\d+ )?(plus de |tous les )?commentaires?",
    r"ver (\d+ )?(mas |todos los |anteriores )?comentarios?",
    r"(\d+ )?previous comments?",
)

REGEX_VIEW_REPLIES = (
    r"\bview (all |more )?\d+ repl(y|ies)\b",
    r"\bview (all |more) repl(y|ies)\b",
    r"\bshow (all |more )?\d+ repl(y|ies)\b",
    r"\bshow (all |more) repl(y|ies)\b",
    r"\bvoir (les |toutes les )?\d+ reponses?\b",
    r"\bafficher (les |toutes les )?\d+ reponses?\b",
    r"\bver (las |todas las )?\d+ respuestas?\b",
    r"\bver (mais |todas as )?\d+ respostas?\b",
)

# Exclude keywords for view-more-comments — prevents matching reply buttons
EXCLUDE_FOR_COMMENTS = (
    "repl", "repon", "respu", "antwort", "ردود", "رد",
)

# Exclude keywords for see-more-in-comment — prevents matching comment/reply buttons
EXCLUDE_FOR_SEE_MORE = (
    "comment", "commentaire", "respuesta", "respuestas",
    "reply", "replies", "reponse", "antwort",
)

# Sort option excludes — "Newest" description often contains "all comments"
SORT_EXCLUDES = (
    "most relevant", "newest", "most recent",
    "plus pertinents", "les plus recents",
    "mas relevantes", "mas recientes",
    "الأكثر صلة", "الأحدث",
    "relevanteste", "neueste",
    "mais relevantes", "mais recentes",
    "piu pertinenti", "piu recenti",
)


def _fold(s: str) -> str:
    """NFD-normalize and strip combining marks so 'é' matches 'e'.

    Also lowercases for case-insensitive substring matching.
    """
    if not s:
        return ""
    nfkd = unicodedata.normalize("NFKD", s.casefold())
    return "".join(ch for ch in nfkd if not unicodedata.combining(ch))


def text_matches_any_keyword(text: str, keywords: tuple[str, ...]) -> bool:
    """True if text contains any of the keywords as a substring (case- and accent-insensitive)."""
    if not text:
        return False
    folded_text = _fold(text)
    for kw in keywords:
        if _fold(kw) in folded_text:
            return True
    return False