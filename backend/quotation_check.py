import re
from difflib import SequenceMatcher


NEAR_MATCH_THRESHOLD = 0.85


def normalize_quote(text: str | None) -> str:
    if not text:
        return ""

    text = text.lower()
    text = text.replace("\u2018", "'").replace("\u2019", "'")
    text = text.replace("\u201c", '"').replace("\u201d", '"')
    text = text.replace("\u2013", "-").replace("\u2014", "-")
    text = re.sub(r"\[\s*\.\.\.\s*\]|\.\.\.|\u2026", " ", text)
    text = re.sub(r"[^\w\s'\"-]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()

    return text


def _best_window_ratio(needle: str, haystack: str) -> float:
    if not needle or not haystack:
        return 0.0

    if len(haystack) <= len(needle):
        return SequenceMatcher(None, needle, haystack).ratio()

    window = len(needle)
    step = max(1, window // 4)
    best = 0.0

    for start in range(0, len(haystack) - window + 1, step):
        chunk = haystack[start:start + window]
        ratio = SequenceMatcher(None, needle, chunk).ratio()
        if ratio > best:
            best = ratio
            if best >= 0.99:
                break

    return best


# Checks whether a quote attributed to the case appears verbatim (or nearly so)
# in the judgment paragraphs. `paragraphs` is a list of {"para": int|None, "text": str}.
def check_quote(quote: str, paragraphs: list[dict]) -> dict:
    needle = normalize_quote(quote)

    if not needle:
        return {
            "quote": quote,
            "status": "empty",
            "para": None,
            "similarity": 0.0,
            "matched_text": None,
        }

    best = {
        "para": None,
        "similarity": 0.0,
        "matched_text": None,
    }

    for paragraph in paragraphs:
        haystack = normalize_quote(paragraph.get("text"))

        if not haystack:
            continue

        if needle in haystack:
            return {
                "quote": quote,
                "status": "exact",
                "para": paragraph.get("para"),
                "similarity": 1.0,
                "matched_text": paragraph.get("text"),
            }

        ratio = _best_window_ratio(needle, haystack)

        if ratio > best["similarity"]:
            best = {
                "para": paragraph.get("para"),
                "similarity": ratio,
                "matched_text": paragraph.get("text"),
            }

    if best["similarity"] >= NEAR_MATCH_THRESHOLD:
        status = "near_match"
    else:
        status = "not_found"

    return {
        "quote": quote,
        "status": status,
        "para": best["para"],
        "similarity": round(best["similarity"], 3),
        "matched_text": (
            best["matched_text"]
            if status == "near_match"
            else None
        ),
    }


def check_quotes(quotes: list[str], paragraphs: list[dict]) -> list[dict]:
    return [
        check_quote(quote, paragraphs)
        for quote in quotes
        if quote and quote.strip()
    ]