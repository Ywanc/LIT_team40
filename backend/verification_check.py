import re
import json
from concurrent.futures import ThreadPoolExecutor, as_completed

from playwright.sync_api import (
    sync_playwright,
    TimeoutError as PlaywrightTimeoutError,
)


###---###
# Text normalisation
###---###

# Normalises text for comparison
def normalize_text(text: str | None) -> str:
    if not text:
        return ""

    text = text.lower()
    text = text.replace("&", "and")
    text = re.sub(r"[^\w\s]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()

    return text


# Normalises court names into standard codes
def normalize_court(court: str | None) -> str:
    if not court:
        return ""

    court = normalize_text(court)

    aliases = {
        "singapore court of appeal": "sgca",
        "court of appeal": "sgca",
        "sgca": "sgca",

        "singapore high court": "sghc",
        "high court": "sghc",
        "sghc": "sghc",

        "singapore high court family division": "sghcf",
        "sghcf": "sghcf",

        "singapore district court": "sgdc",
        "district court": "sgdc",
        "sgdc": "sgdc",

        "singapore family justice courts": "sgfc",
        "family justice courts": "sgfc",
        "sgfc": "sgfc",
    }

    return aliases.get(court, court)


# Checks whether a court value is actually usable
def is_valid_court(court: str | None) -> bool:
    if not court:
        return False

    normalized = normalize_text(court)

    invalid_values = {
        "singapore",
        "singapore law",
        "singapore jurisdiction",
    }

    return normalized not in invalid_values


###---###
# Citation extraction
###---###

# Extracts a neutral citation from text
def extract_neutral_citation(text: str | None) -> dict | None:
    if not text:
        return None

    match = re.search(
        r"\[(\d{4})\]\s*"
        r"(SGCA|SGHC|SGHCF|SGICC|SGDC|SGFC)\s*"
        r"(\d+)",
        text,
        re.IGNORECASE,
    )

    if not match:
        return None

    return {
        "year": match.group(1),
        "court": match.group(2).upper(),
        "number": match.group(3),
    }


# Extracts a law report citation from text
def extract_reported_citation(text: str | None) -> dict | None:
    if not text:
        return None

    match = re.search(
        r"\[(\d{4})\]\s*"
        r"(\d+)\s+"
        r"([A-Za-z]+(?:\([A-Za-z]+\))?)\s+"
        r"(\d+)",
        text,
        re.IGNORECASE,
    )

    if not match:
        return None

    return {
        "year": match.group(1),
        "volume": match.group(2),
        "report": match.group(3),
        "page": match.group(4),
    }


# Extracts both citation formats from text
def extract_citations(text: str | None) -> dict:
    return {
        "neutral": extract_neutral_citation(text),
        "reported": extract_reported_citation(text),
    }


###---###
# Claimed citation extraction
###---###

# Extracts a claimed citation from whatever fields the extractor supplied
def extract_claimed_citation(case_data: dict) -> dict | None:
    for field in ("raw_mention", "citation", "canonical_query"):
        text = case_data.get(field)
        if not text or not isinstance(text, str):
            continue

        neutral = extract_neutral_citation(text)
        if neutral:
            return {
                "citation_type": "neutral",
                **neutral,
            }

        reported = extract_reported_citation(text)
        if reported:
            return {
                "citation_type": "reported",
                **reported,
            }

    return None


# Checks whether the AI supplied a citation
def has_citation(case_data: dict) -> bool:
    return extract_claimed_citation(case_data) is not None


# Strips neutral/reported citations so party matching is not polluted
def strip_citations_from_title(title: str | None) -> str:
    if not title:
        return ""

    cleaned = re.sub(
        r"\[\d{4}\]\s*"
        r"(?:SGCA|SGHC|SGHCF|SGICC|SGDC|SGFC)\s*"
        r"\d+",
        " ",
        title,
        flags=re.IGNORECASE,
    )
    cleaned = re.sub(
        r"\[\d{4}\]\s*"
        r"\d+\s+"
        r"[A-Za-z]+(?:\([A-Za-z]+\))?\s+"
        r"\d+",
        " ",
        cleaned,
        flags=re.IGNORECASE,
    )
    return re.sub(r"\s+", " ", cleaned).strip()


###---###
# Citation comparison
###---###

# Compares a claimed neutral citation with a LawNet neutral citation
def compare_neutral_citation(claimed: dict, actual: dict | None) -> dict:
    if not actual:
        return {
            "match": False,
            "year": False,
            "court": False,
            "number": False,
        }

    year_match = (
        str(claimed.get("year"))
        == str(actual.get("year"))
    )

    court_match = (
        normalize_court(claimed.get("court"))
        == normalize_court(actual.get("court"))
    )

    number_match = (
        str(claimed.get("number"))
        == str(actual.get("number"))
    )

    return {
        "match": (
            year_match
            and court_match
            and number_match
        ),
        "year": year_match,
        "court": court_match,
        "number": number_match,
    }


# Compares a claimed reported citation with a LawNet reported citation
def compare_reported_citation(claimed: dict, actual: dict | None) -> dict:
    if not actual:
        return {
            "match": False,
            "year": False,
            "volume": False,
            "report": False,
            "page": False,
        }

    year_match = (
        str(claimed.get("year"))
        == str(actual.get("year"))
    )

    volume_match = (
        str(claimed.get("volume"))
        == str(actual.get("volume"))
    )

    report_match = (
        normalize_text(claimed.get("report"))
        == normalize_text(actual.get("report"))
    )

    page_match = (
        str(claimed.get("page"))
        == str(actual.get("page"))
    )

    return {
        "match": (
            year_match
            and volume_match
            and report_match
            and page_match
        ),
        "year": year_match,
        "volume": volume_match,
        "report": report_match,
        "page": page_match,
    }


# Checks whether the claimed citation matches either LawNet citation
def compare_citation(claimed: dict | None, actual: dict) -> dict:
    if not claimed:
        return {
            "match": None,
            "match_type": None,
            "neutral": None,
            "reported": None,
        }

    neutral_result = compare_neutral_citation(
        claimed,
        actual.get("neutral"),
    )

    reported_result = compare_reported_citation(
        claimed,
        actual.get("reported"),
    )

    if neutral_result["match"]:
        match_type = "neutral"
    elif reported_result["match"]:
        match_type = "reported"
    else:
        match_type = None

    return {
        "match": (
            neutral_result["match"]
            or reported_result["match"]
        ),
        "match_type": match_type,
        "neutral": neutral_result,
        "reported": reported_result,
    }


###---###
# Case title / party matching
###---###

# Splits a case title into plaintiff and defendant
def split_case_title(title: str) -> tuple[str, str]:
    normalized = normalize_text(title)

    match = re.search(
        r"\s+v\s+",
        normalized,
    )

    if not match:
        return normalized, ""

    plaintiff = normalized[:match.start()].strip()
    defendant = normalized[match.end():].strip()

    return plaintiff, defendant


# Checks whether a short name is an abbreviation of a full party name
def abbreviation_matches(short_name: str, full_name: str) -> bool:
    short_name = re.sub(
        r"[^a-z]",
        "",
        short_name.lower(),
    )

    words = re.findall(
        r"[a-z]+",
        full_name.lower(),
    )

    ignored = {
        "pte",
        "ltd",
        "limited",
        "inc",
        "plc",
        "the",
        "of",
        "and",
        "company",
        "co",
    }

    words = [
        word
        for word in words
        if word not in ignored
    ]

    if not words:
        return False

    acronym = "".join(
        word[0]
        for word in words
    )

    return short_name == acronym


# Checks whether two party names refer to the same party
def party_matches(claimed: str, actual: str) -> dict:
    claimed = normalize_text(claimed)
    actual = normalize_text(actual)

    if not claimed or not actual:
        return {
            "match": False,
            "method": None,
        }

    if claimed == actual:
        return {
            "match": True,
            "method": "exact",
        }

    if claimed in actual or actual in claimed:
        return {
            "match": True,
            "method": "substring",
        }

    if abbreviation_matches(
        claimed,
        actual,
    ):
        return {
            "match": True,
            "method": "abbreviation",
        }

    claimed_words = set(
        claimed.split()
    )

    actual_words = set(
        actual.split()
    )

    ignored = {
        "pte",
        "ltd",
        "limited",
        "inc",
        "plc",
        "the",
        "company",
        "co",
    }

    claimed_words -= ignored
    actual_words -= ignored

    if claimed_words:
        overlap = (
            len(
                claimed_words
                & actual_words
            )
            / len(claimed_words)
        )

        if overlap >= 0.7:
            return {
                "match": True,
                "method": "word_overlap",
            }

    return {
        "match": False,
        "method": None,
    }


# Resolves whether two case titles refer to the same case
def resolve_case_title(
    claimed_title: str,
    actual_title: str,
) -> dict:
    claimed_title = strip_citations_from_title(claimed_title)
    actual_title = strip_citations_from_title(actual_title)

    claimed_plaintiff, claimed_defendant = (
        split_case_title(claimed_title)
    )

    actual_plaintiff, actual_defendant = (
        split_case_title(actual_title)
    )

    if not claimed_plaintiff or not claimed_defendant:
        return {
            "match": False,
            "reason": (
                "Could not split claimed title "
                "into two parties."
            ),
        }

    if not actual_plaintiff or not actual_defendant:
        return {
            "match": False,
            "reason": (
                "Could not split LawNet title "
                "into two parties."
            ),
        }

    plaintiff_result = party_matches(
        claimed_plaintiff,
        actual_plaintiff,
    )

    defendant_result = party_matches(
        claimed_defendant,
        actual_defendant,
    )

    return {
        "match": (
            plaintiff_result["match"]
            and defendant_result["match"]
        ),
        "claimed_parties": {
            "plaintiff": claimed_plaintiff,
            "defendant": claimed_defendant,
        },
        "actual_parties": {
            "plaintiff": actual_plaintiff,
            "defendant": actual_defendant,
        },
        "plaintiff": plaintiff_result,
        "defendant": defendant_result,
    }


###---###
# LawNet verification
###---###

# Searches LawNet and verifies the case name and citation
def verify_case_basic_data(
    case_data: dict,
    headless: bool = False,
) -> dict:

    target_url = (
        "https://www.lawnet.com/openlaw/singapore/"
        "judgments/supreme-court"
    )

    # Allow slow LawNet / parallel browser sessions more headroom.
    NAV_TIMEOUT_MS = 45000
    SEARCH_TIMEOUT_MS = 45000

    query_str = case_data.get(
        "canonical_query"
    )

    if not query_str:
        return {
            "case_exists": False,
            "citation_verified": False,
            "status": "No case found",
            "reason": (
                "No canonical_query provided."
            ),
        }

    claimed_citation = (
        extract_claimed_citation(
            case_data
        )
    )

    claimed = {
        "title": strip_citations_from_title(query_str) or query_str,
        "citation": claimed_citation,
    }

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=headless,
            args=[
                "--no-sandbox",
                "--disable-dev-shm-usage",
            ],
        )

        page = browser.new_page(
            viewport={
                "width": 1920,
                "height": 1080,
            }
        )

        try:

            ###---###
            # Open LawNet
            ###---###

            page.goto(
                target_url,
                wait_until="domcontentloaded",
                timeout=NAV_TIMEOUT_MS,
            )

            ###---###
            # Search LawNet
            ###---###

            search_input = page.locator(
                'textarea.search-query-placeholder'
                '[placeholder="Search"]'
            ).first

            search_input.wait_for(
                state="visible",
                timeout=NAV_TIMEOUT_MS,
            )

            search_input.click()
            search_input.fill(
                query_str
            )
            search_input.press(
                "Enter"
            )

            ###---###
            # Wait for results
            ###---###

            page.wait_for_url(
                re.compile(
                    r"[?&]q="
                ),
                timeout=SEARCH_TIMEOUT_MS,
            )

            page.wait_for_selector(
                ".result-item, .tab-result-count, "
                ':has-text("No results found"), '
                ':has-text("0 results")',
                timeout=SEARCH_TIMEOUT_MS,
            )
            # Let result cards finish rendering after the count appears.
            page.wait_for_timeout(1500)

            result_locator = page.locator(
                ".result-item"
            )

            result_count = (
                result_locator.count()
            )

            ###---###
            # No LawNet results
            ###---###

            if result_count == 0:
                return {
                    "case_exists": False,
                    "citation_verified": False,
                    "status": "No case found",
                    "claimed_metadata": claimed,
                    "url": page.url,
                }

            ###---###
            # Extract candidates
            ###---###

            candidates = []

            for i in range(
                result_count
            ):
                result = (
                    result_locator.nth(i)
                )

                result_text = (
                    result
                    .inner_text()
                    .strip()
                )

                title_locator = (
                    result.locator(
                        ".title a"
                    )
                )

                result_title = (
                    title_locator
                    .inner_text()
                    .strip()
                    if title_locator.count() > 0
                    else ""
                )

                result_title = (
                    result_title
                    or result_text
                )

                result_href = None

                if title_locator.count() > 0:
                    result_href = (
                        title_locator
                        .get_attribute(
                            "href"
                        )
                    )

                resolution = (
                    resolve_case_title(
                        query_str,
                        result_title,
                    )
                )

                citations = (
                    extract_citations(
                        result_text
                    )
                )

                # Citation match is enough when party names are abbreviated
                # (e.g. DSTA) or the query includes a trailing citation.
                if (
                    not resolution["match"]
                    and claimed_citation
                ):
                    citation_cmp = compare_citation(
                        claimed_citation,
                        citations,
                    )
                    if citation_cmp.get("match"):
                        resolution = {
                            **resolution,
                            "match": True,
                            "method": "citation",
                            "citation_compare": citation_cmp,
                        }

                candidates.append({
                    "index": i,
                    "text": result_text,
                    "title": result_title,
                    "href": result_href,
                    "resolution": resolution,
                    "citations": citations,
                })

            ###---###
            # Match case identity
            ###---###

            matched_candidates = [
                candidate
                for candidate in candidates
                if candidate[
                    "resolution"
                ]["match"]
            ]

            ###---###
            # No matching case
            ###---###

            if not matched_candidates:
                return {
                    "case_exists": False,
                    "citation_verified": False,
                    "status": "No case found",
                    "claimed_metadata": claimed,
                    "candidates": [
                        {
                            "index": candidate[
                                "index"
                            ],
                            "text": candidate[
                                "text"
                            ],
                            "title": candidate[
                                "title"
                            ],
                            "href": candidate[
                                "href"
                            ],
                            "citations": candidate[
                                "citations"
                            ],
                        }
                        for candidate in candidates[
                            :5
                        ]
                    ],
                    "reason": (
                        "LawNet returned results, "
                        "but none could be resolved "
                        "as the cited case."
                    ),
                    "url": page.url,
                }

            ###---###
            # Multiple matching cases
            ###---###

            if len(
                matched_candidates
            ) > 1:

                # No citation supplied
                if not has_citation(
                    case_data
                ):
                    return {
                        "case_exists": True,
                        "citation_verified": None,
                        "status": (
                            "Multiple cases found"
                        ),
                        "claimed_metadata": claimed,
                        "candidates": [
                            {
                                "index": candidate[
                                    "index"
                                ],
                                "text": candidate[
                                    "text"
                                ],
                                "title": candidate[
                                    "title"
                                ],
                                "href": candidate[
                                    "href"
                                ],
                                "citations": candidate[
                                    "citations"
                                ],
                                "resolution": candidate[
                                    "resolution"
                                ],
                            }
                            for candidate in matched_candidates
                        ],
                        "reason": (
                            "Multiple LawNet judgments "
                            "match the case name, but "
                            "no citation was supplied "
                            "to identify a specific "
                            "judgment."
                        ),
                        "url": page.url,
                    }

                ###---###
                # Citation supplied
                ###---###

                selected_candidates = []

                for candidate in (
                    matched_candidates
                ):
                    citation_result = (
                        compare_citation(
                            claimed_citation,
                            candidate[
                                "citations"
                            ],
                        )
                    )

                    if citation_result[
                        "match"
                    ]:
                        candidate[
                            "citation_match"
                        ] = citation_result

                        selected_candidates.append(
                            candidate
                        )

                ###---###
                # Exactly one citation match
                ###---###

                if len(
                    selected_candidates
                ) == 1:

                    matched_candidates = (
                        selected_candidates
                    )

                ###---###
                # Citation matches zero or multiple
                ###---###

                else:
                    return {
                        "case_exists": True,
                        "citation_verified": False,
                        "status": (
                            "Case found — "
                            "wrong citation"
                        ),
                        "claimed_metadata": claimed,
                        "candidates": [
                            {
                                "index": candidate[
                                    "index"
                                ],
                                "text": candidate[
                                    "text"
                                ],
                                "title": candidate[
                                    "title"
                                ],
                                "href": candidate[
                                    "href"
                                ],
                                "citations": candidate[
                                    "citations"
                                ],
                                "resolution": candidate[
                                    "resolution"
                                ],
                            }
                            for candidate in matched_candidates
                        ],
                        "reason": (
                            "The case exists, but "
                            "the supplied citation "
                            "does not identify exactly "
                            "one matching LawNet "
                            "judgment."
                        ),
                        "url": page.url,
                    }

            ###---###
            # One judgment established
            ###---###

            matched = (
                matched_candidates[0]
            )

            actual_citations = (
                matched["citations"]
            )

            resolution = (
                matched["resolution"]
            )

            ###---###
            # No citation supplied
            ###---###

            if not has_citation(
                case_data
            ):
                return {
                    "case_exists": True,
                    "citation_verified": None,
                    "status": "Case found",
                    "claimed_metadata": claimed,
                    "actual_citations": actual_citations,
                    "matched_search_result": (
                        matched["text"]
                    ),
                    "matched_search_title": (
                        matched["title"]
                    ),
                    "matched_search_href": (
                        matched["href"]
                    ),
                    "match_details": {
                        "title": resolution,
                        "citation": None,
                    },
                    "url": page.url,
                }

            ###---###
            # Verify citation
            ###---###

            citation_result = (
                compare_citation(
                    claimed_citation,
                    actual_citations,
                )
            )

            if citation_result[
                "match"
            ]:
                status = "Case found"
                citation_verified = True
            else:
                status = (
                    "Case found — "
                    "wrong citation"
                )
                citation_verified = False

            return {
                "case_exists": True,
                "citation_verified": (
                    citation_verified
                ),
                "status": status,
                "claimed_metadata": claimed,
                "actual_citations": (
                    actual_citations
                ),
                "match_details": {
                    "title": resolution,
                    "citation": citation_result,
                },
                "matched_search_result": (
                    matched["text"]
                ),
                "matched_search_title": (
                    matched["title"]
                ),
                "matched_search_href": (
                    matched["href"]
                ),
                "url": page.url,
            }

        except PlaywrightTimeoutError as e:
            return {
                "case_exists": None,
                "citation_verified": None,
                "status": "Search error",
                "claimed_metadata": claimed,
                "error": str(e),
            }

        except Exception as e:
            return {
                "case_exists": None,
                "citation_verified": None,
                "status": "Search error",
                "claimed_metadata": claimed,
                "error": str(e),
            }

        finally:
            browser.close()


###---###
# Test case loading
###---###

# Loads test cases from a JSON file
def load_test_cases(file_path: str) -> list:
    with open(
        file_path,
        "r",
        encoding="utf-8",
    ) as f:
        return json.load(f)


###---###
# Parallel verification
###---###

# Verifies multiple cases in parallel
def verify_cases_parallel(
    case_list: list,
    headless: bool = True,
    max_workers: int = 5,
    on_start=None,
    on_done=None,
) -> list:
    results = [None] * len(case_list)

    def _run(index: int, case_data: dict):
        if on_start:
            on_start(index, case_data)
        return verify_case_basic_data(case_data, headless)

    with ThreadPoolExecutor(
        max_workers=max_workers
    ) as executor:

        future_to_index = {
            executor.submit(
                _run,
                i,
                case_data,
            ): i
            for i, case_data in enumerate(
                case_list
            )
        }

        for future in as_completed(
            future_to_index
        ):
            index = future_to_index[
                future
            ]

            try:
                results[index] = (
                    future.result()
                )

                print(
                    f"Finished "
                    f"{index + 1}/"
                    f"{len(case_list)}: "
                    f"{case_list[index]['canonical_query']}"
                )

            except Exception as e:
                results[index] = {
                    "case_exists": None,
                    "citation_verified": None,
                    "status": "Search error",
                    "error": str(e),
                }

            if on_done:
                on_done(index, results[index])

    return results


###---###
# Main
###---###

if __name__ == "__main__":

    test_cases = load_test_cases(
        "test_cases.json"
    )

    results = verify_cases_parallel(
        test_cases,
        headless=True,
        max_workers=5,
    )

    with open(
        "verification_results.json",
        "w",
        encoding="utf-8",
    ) as f:
        json.dump(
            results,
            f,
            indent=2,
            ensure_ascii=False,
        )

    print("\nDone.")