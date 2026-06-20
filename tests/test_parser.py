from __future__ import annotations

import json
from pathlib import Path

import pytest

from garment_leads.parser import (
    PhoneCandidate,
    classify_intent,
    convert_arabic_indic_digits,
    extract_phones,
    parse_fixture_payload,
    parse_post_text,
)

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "posts"
FIXTURE_FILES = sorted(FIXTURE_DIR.glob("*.json"))


def load_fixture(path: Path) -> dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))


def test_fixture_corpus_has_at_least_30_posts() -> None:
    assert len(FIXTURE_FILES) >= 30


@pytest.mark.parametrize("fixture_path", FIXTURE_FILES, ids=lambda p: p.name)
def test_parser_against_realistic_fixture_corpus(fixture_path: Path) -> None:
    payload = load_fixture(fixture_path)
    leads = parse_fixture_payload(payload)
    actual_phones = sorted(lead.phone_normalized for lead in leads)
    assert actual_phones == sorted(payload["expected_phones"])
    assert len(leads) == payload["expected_leads"]
    if payload["expected_leads"]:
        assert {lead.intent for lead in leads} == {payload["expected_intent"]}
        for lead in leads:
            assert lead.phone_raw
            assert lead.phone_normalized.startswith("+216")
            assert lead.intent_confidence > 0
            assert lead.intent_raw_excerpt


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Tel +216 54 123 456", ["+21654123456"]),
        ("Tel 00216 54 123 456", ["+21654123456"]),
        ("Tel 21654123456", ["+21654123456"]),
        ("Tel 54 123 456", ["+21654123456"]),
        ("Tel 54.123.456", ["+21654123456"]),
        ("Tel (54) 123 456", ["+21654123456"]),
        ("Tel 54-123-456", ["+21654123456"]),
        ("Tel 54/123/456", ["+21654123456"]),
        ("☎ ٥٤١٢٣٤٥٦", ["+21654123456"]),
        ("WhatsApp 24 555 666 / 58 777 888", ["+21624555666", "+21658777888"]),
    ],
)
def test_extract_phones_supported_formats(text: str, expected: list[str]) -> None:
    assert [candidate.e164 for candidate in extract_phones(text)] == expected


@pytest.mark.parametrize(
    "text",
    [
        "Livraison le 12/06/2026 sans téléphone",
        "Tailles 36 38 40 42 disponibles",
        "Prix 54.123 dinars seulement",
        "group_id=284751225383775",
        "Quantité 100 000 pièces",
    ],
)
def test_extract_phones_rejects_false_positives(text: str) -> None:
    assert extract_phones(text) == []


def test_phone_candidate_dataclass_shape() -> None:
    candidate = extract_phones("📞 54 123 456")[0]
    assert isinstance(candidate, PhoneCandidate)
    assert candidate.raw == "54 123 456"
    assert candidate.valid is True
    assert candidate.region == "TN"
    assert candidate.kind in {"mobile", "fixed_or_mobile", "unknown"}


def test_arabic_indic_digit_conversion() -> None:
    assert convert_arabic_indic_digits("٥٤١٢٣٤٥٦") == "54123456"


def test_intent_result_contains_evidence() -> None:
    result = classify_intent("Grossiste tissu popeline satin. Tel 71 222 333", has_phone=True)
    assert result.intent == "fabric_supplier"
    assert result.confidence > 0
    assert "tissu" in result.matched_keywords
    assert result.raw_excerpt


def test_post_without_phone_is_skipped() -> None:
    assert parse_post_text("مطلوب خياطات بدون رقم هاتف", source_group_id="test") == []
