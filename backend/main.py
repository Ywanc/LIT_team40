import queue
import re
import threading
from typing import Callable, Iterator

from playwright.sync_api import sync_playwright

from extract_case_items import (
    DEFAULT_MODEL,
    extract_case_queries,
)
from llm_client import CostTracker
from verification_check import verify_cases_parallel
from claim_check import check_claims_parallel


ProgressCallback = Callable[[dict], None]


def _skeleton_case(case: dict) -> dict:
    """Placeholder row shown immediately after extraction."""
    audit_context = case.get("audit_context") or {}
    return {
        "case_exists": None,
        "citation_verified": None,
        "status": "Queued",
        "claimed_metadata": {
            "title": case.get("canonical_query") or "Unknown case",
            "citation": case.get("citation"),
        },
        "matched_search_title": None,
        "matched_search_href": None,
        "actual_citations": {},
        "candidates": [],
        "claim_check": None,
        "statements": audit_context.get("asserted_holdings") or [],
        "_pending": True,
    }


def _attach_claim_checks(
    case_list: list[dict],
    results: list[dict | None],
    model_name: str,
    headless: bool,
    max_workers: int,
    on_progress: ProgressCallback | None = None,
    cost_tracker: CostTracker | None = None,
) -> None:
    jobs = []
    job_indices = []

    for i, (case, result) in enumerate(zip(case_list, results)):
        if not result or result.get("status") != "Case found":
            continue

        href = result.get("matched_search_href")
        if not href:
            continue

        audit_context = case.get("audit_context") or {}
        statements = audit_context.get("asserted_holdings") or []
        quotes = audit_context.get("direct_quotes") or []

        if not statements and not quotes:
            result["claim_check"] = {
                "status": "skipped",
                "reason": "No statements or quotes attributed to this case.",
            }
            if on_progress:
                on_progress({
                    "type": "case_update",
                    "index": i,
                    "claim_check": result["claim_check"],
                })
            continue

        jobs.append({
            "url": href,
            "statements": statements,
            "quotes": quotes,
        })
        job_indices.append(i)

    if not jobs:
        return

    def on_start(job_i: int, _job: dict):
        case_i = job_indices[job_i]
        if on_progress:
            on_progress({
                "type": "case_update",
                "index": case_i,
                "status": "Verifying statements",
            })

    def on_done(job_i: int, claim_result: dict | None):
        case_i = job_indices[job_i]
        results[case_i]["claim_check"] = claim_result
        if on_progress:
            # Restore the LawNet verification status alongside the claim result.
            on_progress({
                "type": "case_update",
                "index": case_i,
                "status": results[case_i].get("status"),
                "claim_check": claim_result,
                "total_cost": (
                    round(cost_tracker.total, 6) if cost_tracker else None
                ),
            })

    check_claims_parallel(
        jobs,
        model_name=model_name,
        headless=headless,
        max_workers=max_workers,
        on_start=on_start,
        on_done=on_done,
        cost_tracker=cost_tracker,
    )


def _run_audit_core(
    text: str,
    model_name: str,
    headless: bool,
    max_workers: int,
    check_statements: bool,
    on_progress: ProgressCallback | None = None,
) -> dict:
    def emit(event: dict) -> None:
        if on_progress:
            on_progress(event)

    cost_tracker = CostTracker()

    emit({"type": "phase", "phase": "extracting"})

    cases = extract_case_queries(
        text,
        model_name=model_name,
        cost_tracker=cost_tracker,
    )

    case_list = [
        case.model_dump(exclude_none=True)
        for case in cases
    ]

    skeletons = [_skeleton_case(case) for case in case_list]
    emit({
        "type": "extracted",
        "count": len(skeletons),
        "cases": skeletons,
        "statements_checked": check_statements,
        "total_cost": round(cost_tracker.total, 6),
    })

    if not case_list:
        done = {
            "type": "done",
            "extracted_count": 0,
            "verified_count": 0,
            "statements_checked": check_statements,
            "cases": [],
            "total_cost": round(cost_tracker.total, 6),
        }
        emit(done)
        return {
            "extracted_count": 0,
            "verified_count": 0,
            "statements_checked": check_statements,
            "cases": [],
            "total_cost": round(cost_tracker.total, 6),
        }

    emit({"type": "phase", "phase": "searching"})

    def on_search_start(index: int, _case_data: dict):
        emit({
            "type": "case_update",
            "index": index,
            "status": "Searching case in LawNet",
        })

    def on_search_done(index: int, result: dict | None):
        if result is not None:
            audit_context = case_list[index].get("audit_context") or {}
            result["statements"] = audit_context.get("asserted_holdings") or []
        emit({
            "type": "case_update",
            "index": index,
            "result": result,
            "total_cost": round(cost_tracker.total, 6),
        })

    results = verify_cases_parallel(
        case_list,
        headless=headless,
        max_workers=max_workers,
        on_start=on_search_start,
        on_done=on_search_done,
    )

    if check_statements:
        emit({"type": "phase", "phase": "verifying_statements"})
        _attach_claim_checks(
            case_list,
            results,
            model_name=model_name,
            headless=headless,
            max_workers=min(max_workers, 3),
            on_progress=on_progress,
            cost_tracker=cost_tracker,
        )

    payload = {
        "extracted_count": len(case_list),
        "verified_count": sum(
            1
            for result in results
            if result and result.get("case_exists")
        ),
        "statements_checked": check_statements,
        "cases": results,
        "total_cost": round(cost_tracker.total, 6),
    }
    emit({"type": "done", **payload})
    return payload


def run_audit_pipeline(
    text: str,
    model_name: str = DEFAULT_MODEL,
    headless: bool = True,
    max_workers: int = 5,
    check_statements: bool = False,
) -> dict:
    return _run_audit_core(
        text,
        model_name=model_name,
        headless=headless,
        max_workers=max_workers,
        check_statements=check_statements,
    )


def run_audit_pipeline_stream(
    text: str,
    model_name: str = DEFAULT_MODEL,
    headless: bool = True,
    max_workers: int = 5,
    check_statements: bool = False,
) -> Iterator[dict]:
    """Yield progress events while the audit pipeline runs.

    Events:
      phase      – {type, phase}
      extracted  – {type, count, cases, statements_checked}
      case_update – {type, index, status?|result?|claim_check?}
      done       – {type, extracted_count, verified_count, statements_checked, cases}
      error      – {type, message}
    """
    events: queue.Queue[dict | None] = queue.Queue()

    def on_progress(event: dict) -> None:
        events.put(event)

    def worker() -> None:
        try:
            _run_audit_core(
                text,
                model_name=model_name,
                headless=headless,
                max_workers=max_workers,
                check_statements=check_statements,
                on_progress=on_progress,
            )
        except Exception as e:
            events.put({"type": "error", "message": str(e)})
        finally:
            events.put(None)

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()

    while True:
        event = events.get()
        if event is None:
            break
        yield event

    thread.join(timeout=1)


# format citations
def _canonical_citation(text: str) -> str:
    # Normalise a citation string e.g. "[2023] sgca 9999" -> "[2023] SGCA 9999"
    m = re.search(
        r"\[(\d{4})\]\s*((?:SGCA|SGHC|SGHCF|SGHCR|SGDC|SGMC)(?:\(I\))?)\s*(\d+)",
        text,
        re.IGNORECASE,
    )
    if m:
        return f"[{m.group(1)}] {m.group(2).upper()} {m.group(3)}"
    m = re.search(r"\[(\d{4})\]\s*(\d+)\s*SLR\s*(\d+)", text, re.IGNORECASE)
    if m:
        return f"[{m.group(1)}] {m.group(2)} SLR {m.group(3)}"
    return ""


def search_lawnet_ui_bar(query_str: str, headless: bool = False) -> dict:
    target_url = (
        "https://www.lawnet.com/openlaw/singapore/judgments/supreme-court"
    )

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=headless, args=["--no-sandbox", "--disable-dev-shm-usage"]
        )
        page = browser.new_page(viewport={"width": 1920, "height": 1080})

        try:
            page.goto(target_url, wait_until="domcontentloaded", timeout=15000)

            # OpenLaw uses a sidebar <textarea>, not the hidden header <input>
            search_input = page.locator(
                'textarea.search-query-placeholder[placeholder="Search"]'
            ).first
            search_input.wait_for(state="visible", timeout=15000)

            search_input.click()
            search_input.fill(query_str)
            search_input.press("Enter")

            # Wait until search results update (URL gains ?q=...)
            page.wait_for_url(re.compile(r"[?&]q="), timeout=15000)
            page.wait_for_selector(
                ".result-item, .tab-result-count", timeout=15000
            )

            has_results = page.locator(".result-item").count() > 0
            no_results = (
                page.locator(
                    ':has-text("No results found"), :has-text("0 results")'
                ).count()
                > 0
            )

            exists = has_results and not no_results

            # OpenLaw search is fuzzy: it may return unrelated cases.
            # Only count it as a match if a result bears the queried citation.
            query_cit = _canonical_citation(query_str)
            if exists and query_cit:
                result_texts = page.locator(".result-item").all_inner_texts()
                exists = any(query_cit in rt for rt in result_texts)

            return {
                "query": query_str,
                "exists": exists,
                "url": page.url,
            }
        except Exception as e:
            return {"query": query_str, "exists": False, "error": str(e)}
        finally:
            browser.close()


if __name__ == "__main__":
    sample = (
        "Under Singapore law, proximity is governed by "
        "Spandeck Engineering v DSTA [2007] 4 SLR(R) 100.\n"
        "Also in PP v Smith, Judge crashed out."
    )
    result = run_audit_pipeline(
        sample,
        headless=True,
        max_workers=2,
    )
    print(result)
