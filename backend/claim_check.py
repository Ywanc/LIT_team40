import re
from concurrent.futures import ThreadPoolExecutor, as_completed

from playwright.sync_api import (
    sync_playwright,
    TimeoutError as PlaywrightTimeoutError,
)

from extract_case_items import DEFAULT_MODEL
from llm_client import chat_json
from quotation_check import (
    NEAR_MATCH_THRESHOLD,
    _best_window_ratio,
    check_quotes,
    normalize_quote,
)


LAWNET_ORIGIN = "https://www.lawnet.com"

MAX_SECTIONS_PER_STATEMENT = 6
MAX_CONTEXT_CHARS = 80_000
STATEMENT_WORKERS = 4

VERDICTS = {
    "supported",
    "partially_supported",
    "contradicted",
    "unsure",
}


###---###
# Judgment fetching / parsing
###---###

def resolve_case_url(href_or_url: str) -> str:
    if href_or_url.startswith("http"):
        return href_or_url
    if not href_or_url.startswith("/"):
        href_or_url = "/" + href_or_url
    return LAWNET_ORIGIN + href_or_url


def _parse_judgment_elements(elements: list[list[str]]) -> dict:
    title = None
    citations = []
    metadata = {}
    headnote = {"held_heading": None, "facts": [], "held": []}

    sections = []
    paragraphs = []
    heading_stack = {}
    current_section = None
    current_para = None
    hn_bucket = None

    def start_section(level: int, heading: str):
        nonlocal current_section
        heading_stack[level] = heading
        for deeper in [k for k in heading_stack if k > level]:
            del heading_stack[deeper]
        path = " > ".join(
            heading_stack[k] for k in sorted(heading_stack)
        )
        current_section = {
            "id": len(sections),
            "level": level,
            "heading": heading,
            "path": path,
            "paragraphs": [],
        }
        sections.append(current_section)

    def add_paragraph_text(text: str, number: int | None):
        nonlocal current_para, current_section
        if current_section is None:
            start_section(0, "Preamble")

        if number is not None or current_para is None:
            current_para = {
                "para": number,
                "text": text,
                "section_id": current_section["id"],
            }
            paragraphs.append(current_para)
            current_section["paragraphs"].append(current_para)
        else:
            current_para["text"] += "\n" + text

    for class_name, text in elements:
        text = (text or "").strip()
        if not text:
            continue

        if class_name == "lr_doc_title":
            lines = [l.strip() for l in text.split("\n") if l.strip()]
            if lines:
                title = lines[0]
            for line in lines[1:]:
                if re.match(r"^\[\d{4}\]", line) and line not in citations:
                    citations.append(line)
            continue

        if class_name.startswith("lr_citation_link"):
            if text not in citations:
                citations.append(text)
            continue

        if class_name == "HN-Heading":
            lowered = text.lower()
            if lowered.startswith("held"):
                hn_bucket = "held"
                headnote["held_heading"] = text
            elif lowered.startswith("fact"):
                hn_bucket = "facts"
            else:
                hn_bucket = None
            continue

        if class_name == "HN-Facts":
            headnote["facts"].append(text)
            continue

        if class_name == "HN-Held":
            headnote["held"].append(text)
            continue

        if class_name.startswith("HN-"):
            if hn_bucket:
                headnote[hn_bucket].append(text)
            continue

        if class_name in ("Judg-Author", "Judg-Hearing-Date", "Judg-Date-Reserved"):
            metadata[class_name] = text
            continue

        heading_match = re.match(r"Judg-Heading-(\d)", class_name)
        if heading_match:
            start_section(int(heading_match.group(1)), text)
            current_para = None
            continue

        if class_name.startswith("Judg-"):
            number = None
            num_match = re.match(r"^(\d{1,4})\s+(.*)", text, re.DOTALL)
            if num_match and not class_name.startswith("Judg-Quote"):
                number = int(num_match.group(1))
                text = num_match.group(2).strip()
            add_paragraph_text(text, number)
            continue

    for section in sections:
        nums = [
            p["para"] for p in section["paragraphs"]
            if p["para"] is not None
        ]
        section["first_para"] = min(nums) if nums else None
        section["last_para"] = max(nums) if nums else None
        section["char_count"] = sum(
            len(p["text"]) for p in section["paragraphs"]
        )

    return {
        "title": title,
        "citations": citations,
        "metadata": metadata,
        "headnote": headnote,
        "sections": sections,
        "paragraphs": paragraphs,
    }


# Opens a LawNet judgment page and returns its structured content
def fetch_judgment(href_or_url: str, headless: bool = True) -> dict:
    url = resolve_case_url(href_or_url)

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=headless,
            args=["--no-sandbox", "--disable-dev-shm-usage"],
        )
        page = browser.new_page(
            viewport={"width": 1920, "height": 1080}
        )

        try:
            page.goto(url, wait_until="domcontentloaded", timeout=30000)
            page.wait_for_selector(
                "p[class^=Judg-], p[class^=HN-]",
                timeout=30000,
            )
            page.wait_for_timeout(1000)

            elements = page.eval_on_selector_all(
                "div.lr_doc_title, div.lr_citation_link, "
                "p[class^=HN-], p[class^=Judg-]",
                "els => els.map(e => [e.className || '', e.innerText || ''])",
            )
        finally:
            browser.close()

    judgment = _parse_judgment_elements(elements)
    judgment["url"] = url
    return judgment


###---###
# Stage 1: choose relevant sections
###---###

SELECT_SYSTEM_PROMPT = (
    "You are a Singapore legal research assistant. You are given a statement "
    "that an AI made about a court judgment, together with the judgment's "
    "table of contents (section headings with paragraph ranges) and the "
    "headnote holdings. Choose the fewest sections whose paragraphs are most "
    "likely to confirm or refute that statement. Prefer sections about the "
    "court's own reasoning and decision over background facts or summaries of "
    "counsel's arguments, unless the statement is about those. "
    "When the statement is about the court's conclusion, ruling, holding, "
    "outcome, order, or disposition, strongly prefer the judgment's final "
    "substantive heading — often labelled Conclusion, Disposition, Orders, "
    "or similar — and/or the headnote Held section, rather than early "
    "background or issue-framing sections. Respond with a "
    "single JSON object: "
    '{"section_ids": [int, ...], "use_headnote": bool, "reason": string}. '
    f"Select at most {MAX_SECTIONS_PER_STATEMENT} section ids."
)


def _outline(judgment: dict) -> str:
    lines = []
    for section in judgment["sections"]:
        indent = "  " * max(section["level"] - 1, 0)
        first, last = section["first_para"], section["last_para"]
        if first is None:
            span = f"{len(section['paragraphs'])} unnumbered paras"
        elif first == last:
            span = f"para {first}"
        else:
            span = f"paras {first}-{last}"
        lines.append(f"[{section['id']}] {indent}{section['heading']} ({span})")
    return "\n".join(lines)


def _headnote_text(judgment: dict) -> str:
    held = judgment["headnote"]["held"]
    if not held:
        return ""
    heading = judgment["headnote"]["held_heading"] or "Held:"
    return heading + "\n" + "\n".join(held)


def _keyword_fallback(statement: str, judgment: dict) -> list[int]:
    words = {
        w for w in normalize_quote(statement).split()
        if len(w) > 3
    }
    scored = []
    for section in judgment["sections"]:
        body = normalize_quote(
            section["heading"] + " " + " ".join(
                p["text"] for p in section["paragraphs"]
            )
        )
        hits = sum(1 for w in words if w in body)
        scored.append((hits, -section["char_count"], section["id"]))
    scored.sort(reverse=True)
    return [sid for hits, _, sid in scored[:4] if hits > 0]


def select_sections(
    statement: str,
    judgment: dict,
    model_name: str = DEFAULT_MODEL,
    cost_tracker=None,
) -> dict:
    if not judgment["sections"]:
        return {
            "section_ids": [],
            "use_headnote": True,
            "reason": "Judgment has no section headings; using headnote only.",
            "method": "none",
        }

    user_prompt = (
        f"STATEMENT:\n{statement}\n\n"
        f"JUDGMENT: {judgment.get('title')}\n\n"
        f"TABLE OF CONTENTS:\n{_outline(judgment)}\n"
    )
    headnote = _headnote_text(judgment)
    if headnote:
        user_prompt += f"\nHEADNOTE:\n{headnote}\n"

    try:
        result = chat_json(
            SELECT_SYSTEM_PROMPT,
            user_prompt,
            model_name,
            cost_tracker=cost_tracker,
            enable_thinking=True,
        )
        valid_ids = {s["id"] for s in judgment["sections"]}
        ids = []
        for sid in result.get("section_ids", []):
            try:
                sid = int(sid)
            except (TypeError, ValueError):
                continue
            if sid in valid_ids and sid not in ids:
                ids.append(sid)
        ids = ids[:MAX_SECTIONS_PER_STATEMENT]

        if not ids:
            ids = _keyword_fallback(statement, judgment)

        return {
            "section_ids": ids,
            "use_headnote": bool(result.get("use_headnote", True)),
            "reason": result.get("reason", ""),
            "method": "llm",
        }
    except Exception as e:
        return {
            "section_ids": _keyword_fallback(statement, judgment),
            "use_headnote": True,
            "reason": f"LLM section selection failed ({e}); used keyword fallback.",
            "method": "keyword_fallback",
        }


###---###
# Stage 2: verify statement against selected paragraphs
###---###

VERIFY_SYSTEM_PROMPT = (
    "You are a meticulous Singapore legal fact-checker. You are given a "
    "statement an AI made about a court judgment, and excerpts from that "
    "judgment. Each excerpt paragraph is prefixed with its paragraph number "
    "in square brackets, e.g. [73]. Decide whether the statement is factually "
    "supported by the excerpts. Use only the excerpts; do not rely on outside "
    "knowledge. Verdict options: 'supported' (the excerpts clearly establish "
    "the statement), 'partially_supported' (the gist is right but a material "
    "detail is wrong or overstated), 'contradicted' (the excerpts show the "
    "statement is wrong), 'unsure' (the excerpts do not contain enough to "
    "decide). Always cite the specific paragraph numbers you relied on, with "
    "a short verbatim quote from each. If unsure, explain in reasoning exactly "
    "what is missing and cite the closest relevant paragraphs. Respond with a "
    "single JSON object: "
    '{"verdict": string, "confidence": number 0-1, "reasoning": string, '
    '"citations": [{"para": int, "quote": string}]}'
)


def _build_context(
    judgment: dict,
    section_ids: list[int],
    use_headnote: bool,
) -> tuple[str, dict[int, dict]]:
    parts = []
    para_lookup = {}
    total = 0

    if use_headnote:
        headnote = _headnote_text(judgment)
        if headnote:
            block = "HEADNOTE (editorial summary, cite as [HN]):\n" + headnote
            parts.append(block)
            total += len(block)

    by_id = {s["id"]: s for s in judgment["sections"]}

    for sid in section_ids:
        section = by_id.get(sid)
        if not section:
            continue
        lines = [f"SECTION: {section['path']}"]
        for paragraph in section["paragraphs"]:
            label = paragraph["para"] if paragraph["para"] is not None else "-"
            lines.append(f"[{label}] {paragraph['text']}")
            if paragraph["para"] is not None:
                para_lookup[paragraph["para"]] = paragraph
        block = "\n".join(lines)
        if total + len(block) > MAX_CONTEXT_CHARS:
            parts.append(
                f"SECTION: {section['path']} (omitted: context limit reached)"
            )
            continue
        parts.append(block)
        total += len(block)

    return "\n\n".join(parts), para_lookup


def _validate_citations(
    citations: list,
    para_lookup: dict[int, dict],
    headnote_allowed: bool,
) -> list[dict]:
    cleaned = []
    for citation in citations or []:
        if not isinstance(citation, dict):
            continue
        para = citation.get("para")
        quote = str(citation.get("quote") or "").strip()

        if isinstance(para, str) and para.strip().upper() == "HN":
            cleaned.append({
                "para": "HN",
                "quote": quote,
                "in_context": headnote_allowed,
                "quote_verified": None,
            })
            continue

        try:
            para = int(para)
        except (TypeError, ValueError):
            continue

        paragraph = para_lookup.get(para)
        quote_verified = None
        if paragraph and quote:
            needle = normalize_quote(quote)
            haystack = normalize_quote(paragraph["text"])
            if needle in haystack:
                quote_verified = True
            else:
                ratio = _best_window_ratio(needle, haystack)
                quote_verified = ratio >= NEAR_MATCH_THRESHOLD

        cleaned.append({
            "para": para,
            "quote": quote,
            "matched_text": paragraph["text"] if paragraph else None,
            "in_context": paragraph is not None,
            "quote_verified": quote_verified,
        })
    return cleaned


def _empty_verification(reason: str) -> dict:
    return {
        "verdict": "unsure",
        "confidence": 0.0,
        "reasoning": reason,
        "citations": [],
    }


def verify_statement(
    statement: str,
    judgment: dict,
    selection: dict,
    model_name: str = DEFAULT_MODEL,
    cost_tracker=None,
) -> dict:
    context, para_lookup = _build_context(
        judgment,
        selection["section_ids"],
        selection.get("use_headnote", True),
    )

    if not context.strip():
        return _empty_verification("Empty judgment context.")

    user_prompt = (
        f"STATEMENT TO CHECK:\n{statement}\n\n"
        f"JUDGMENT: {judgment.get('title')} {' / '.join(judgment.get('citations', []))}\n\n"
        f"EXCERPTS:\n{context}"
    )

    result = chat_json(
        VERIFY_SYSTEM_PROMPT,
        user_prompt,
        model_name,
        cost_tracker=cost_tracker,
        enable_thinking=True,
    )

    verdict = str(result.get("verdict", "unsure")).lower().strip()
    if verdict not in VERDICTS:
        verdict = "unsure"

    try:
        confidence = float(result.get("confidence", 0.0))
    except (TypeError, ValueError):
        confidence = 0.0
    confidence = max(0.0, min(1.0, confidence))

    reasoning = str(result.get("reasoning", "")).strip()
    if not reasoning:
        reasoning = str(result.get("unsure_reason") or "").strip()

    return {
        "verdict": verdict,
        "confidence": round(confidence, 2),
        "reasoning": reasoning,
        "citations": _validate_citations(
            result.get("citations"),
            para_lookup,
            selection.get("use_headnote", True),
        ),
    }


def _check_one_statement(
    statement: str,
    judgment: dict,
    model_name: str,
    cost_tracker=None,
) -> dict:
    selection = select_sections(
        statement, judgment, model_name, cost_tracker=cost_tracker
    )
    try:
        verification = verify_statement(
            statement,
            judgment,
            selection,
            model_name,
            cost_tracker=cost_tracker,
        )
    except Exception as e:
        verification = _empty_verification(f"Verification failed: {e}")

    by_id = {s["id"]: s for s in judgment["sections"]}
    return {
        "statement": statement,
        "sections_checked": [
            {
                "id": sid,
                "path": by_id[sid]["path"],
                "paras": [by_id[sid]["first_para"], by_id[sid]["last_para"]],
            }
            for sid in selection["section_ids"]
            if sid in by_id
        ],
        "selection_reason": selection.get("reason"),
        "selection_method": selection.get("method"),
        **verification,
    }


###---###
# Orchestration
###---###

def _quote_corpus(judgment: dict) -> list[dict]:
    corpus = list(judgment["paragraphs"])
    for text in judgment["headnote"]["held"]:
        corpus.append({"para": "HN", "text": text})
    for text in judgment["headnote"]["facts"]:
        corpus.append({"para": "HN", "text": text})
    return corpus


# Fact-checks the AI's statements and quotes about one case against its LawNet judgment
def check_case_claims(
    href_or_url: str,
    statements: list[str],
    quotes: list[str] | None = None,
    model_name: str = DEFAULT_MODEL,
    headless: bool = True,
    cost_tracker=None,
) -> dict:
    url = resolve_case_url(href_or_url)
    statements = [s for s in (statements or []) if s and s.strip()]
    quotes = [q for q in (quotes or []) if q and q.strip()]

    try:
        judgment = fetch_judgment(url, headless=headless)
    except PlaywrightTimeoutError as e:
        return {
            "url": url,
            "status": "error",
            "error": f"Timed out loading judgment: {e}",
            "statements": [],
            "quotes": [],
        }
    except Exception as e:
        return {
            "url": url,
            "status": "error",
            "error": str(e),
            "statements": [],
            "quotes": [],
        }

    statement_results: list[dict | None] = [None] * len(statements)
    if statements:
        workers = min(STATEMENT_WORKERS, len(statements))
        with ThreadPoolExecutor(max_workers=workers) as executor:
            future_to_index = {
                executor.submit(
                    _check_one_statement,
                    statement,
                    judgment,
                    model_name,
                    cost_tracker,
                ): i
                for i, statement in enumerate(statements)
            }
            for future in as_completed(future_to_index):
                index = future_to_index[future]
                try:
                    statement_results[index] = future.result()
                except Exception as e:
                    statement_results[index] = {
                        "statement": statements[index],
                        "sections_checked": [],
                        "selection_reason": "",
                        "selection_method": "error",
                        **_empty_verification(f"Statement check failed: {e}"),
                    }

    statement_results = [r for r in statement_results if r is not None]

    quote_results = check_quotes(quotes, _quote_corpus(judgment))

    verdict_counts = {}
    for r in statement_results:
        verdict_counts[r["verdict"]] = verdict_counts.get(r["verdict"], 0) + 1

    return {
        "url": url,
        "status": "checked",
        "case_title": judgment.get("title"),
        "case_citations": judgment.get("citations", []),
        "paragraph_count": len(judgment["paragraphs"]),
        "section_count": len(judgment["sections"]),
        "statements": statement_results,
        "quotes": quote_results,
        "summary": {
            "statements": verdict_counts,
            "quotes": {
                status: sum(1 for q in quote_results if q["status"] == status)
                for status in ("exact", "near_match", "not_found")
            },
        },
    }


def check_claims_parallel(
    jobs: list[dict],
    model_name: str = DEFAULT_MODEL,
    headless: bool = True,
    max_workers: int = 3,
    on_start=None,
    on_done=None,
    cost_tracker=None,
) -> list[dict | None]:
    results: list[dict | None] = [None] * len(jobs)

    def _run(index: int, job: dict):
        if on_start:
            on_start(index, job)
        return check_case_claims(
            job["url"],
            job.get("statements", []),
            job.get("quotes", []),
            model_name,
            headless,
            cost_tracker=cost_tracker,
        )

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_index = {
            executor.submit(_run, i, job): i
            for i, job in enumerate(jobs)
        }

        for future in as_completed(future_to_index):
            index = future_to_index[future]
            try:
                results[index] = future.result()
            except Exception as e:
                results[index] = {
                    "url": jobs[index]["url"],
                    "status": "error",
                    "error": str(e),
                    "statements": [],
                    "quotes": [],
                }

            if on_done:
                on_done(index, results[index])

    return results


if __name__ == "__main__":
    import json

    demo = check_case_claims(
        "/openlaw/cases/citation/[2007]+SGCA+37?ref=sg-sc",
        statements=[
            "The court held that a two-stage test applies to determine duty of care."
        ],
        quotes=[
            "first stage requires physical, circumstantial or causal proximity"
        ],
    )
    print(json.dumps(demo, indent=2, ensure_ascii=False))