import json
import os
import re
from pathlib import Path
from typing import List, Literal, Optional

import requests
from dotenv import load_dotenv
from pydantic import BaseModel, Field


# Load OPENROUTER_API_KEY / OPENROUTER_MODEL from a .env file
# (project root: LIT_team40/.env, or the backend folder).
load_dotenv()
load_dotenv(Path(__file__).resolve().parent / ".env")


# OpenRouter model used for structured extraction.
# Override with the OPENROUTER_MODEL environment variable or backend/.env.
DEFAULT_MODEL = os.environ.get(
    "OPENROUTER_MODEL",
    "~deepseek/deepseek-v4-flash-latest",
)

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"

SYSTEM_PROMPT = (
    "You are a Singapore legal audit assistant. Extract EVERY legal case mention "
    "from the text into the requested JSON schema. Be exhaustive and consistent.\n\n"
    "CASE IDENTITY\n"
    "- For each case, set canonical_query to the clean case name (parties), "
    "optionally followed by its citation if present in the text.\n"
    "- Put any Singapore neutral citation (e.g. [2007] SGCA 37) or law report "
    "citation (e.g. [2007] 4 SLR(R) 100) in the citation field when it appears. "
    "Do not invent citations.\n"
    "- raw_mention must be the exact span from the text that refers to the case.\n"
    "- type must be one of: neutral_citation, slr_citation, case_title, informal.\n\n"
    "AUDIT CONTEXT (critical — do not under-extract)\n"
    "- asserted_holdings: every distinct principle, holding, or claim the author "
    "attributes to that case. Split compound sentences into atomic holdings. "
    "Do NOT put quoted speech here.\n"
    "- direct_quotes: every verbatim quotation (text inside \"...\" or '...', "
    "including curly quotes) that the author attributes to that case or its "
    "court/judgment. Copy quotes exactly without the surrounding quotation marks. "
    "If quotation marks appear for a case, direct_quotes MUST NOT be empty.\n"
    "- Include holdings/quotes that appear later in the text but still refer to "
    "the same case (scattered references).\n"
    "- If a case is only named with no holdings or quotes, use empty lists.\n"
    "- Never merge a holding and a quote into one field; never drop a quote "
    "because a holding already covers similar content.\n\n"
    "Respond with a single JSON object only, conforming exactly to this schema:\n"
)

FEW_SHOT_EXAMPLES = [
    {
        "role": "user",
        "content": (
            "In Spandeck Engineering v DSTA [2007] SGCA 37, the Court of Appeal "
            "held that a single two-stage test, preceded by a threshold requirement "
            "of factual foreseeability, applies to determine a duty of care. The "
            'court stated that "a coherent and workable test can be fashioned out '
            'of the basic two-stage test premised on proximity and policy '
            'considerations".'
        ),
    },
    {
        "role": "assistant",
        "content": json.dumps(
            {
                "cases": [
                    {
                        "raw_mention": "Spandeck Engineering v DSTA [2007] SGCA 37",
                        "type": "neutral_citation",
                        "canonical_query": "Spandeck Engineering v DSTA [2007] SGCA 37",
                        "citation": "[2007] SGCA 37",
                        "audit_context": {
                            "asserted_holdings": [
                                "A single two-stage test, preceded by a threshold requirement of factual foreseeability, applies to determine a duty of care.",
                            ],
                            "direct_quotes": [
                                "a coherent and workable test can be fashioned out of the basic two-stage test premised on proximity and policy considerations",
                            ],
                        },
                    }
                ]
            },
            ensure_ascii=False,
        ),
    },
    {
        "role": "user",
        "content": (
            "Under Singapore law, proximity is governed by Spandeck Engineering v "
            "DSTA [2007] 4 SLR(R) 100. The court held that a two-stage test applies "
            "to determine duty of care. Later, the judge noted \"first stage "
            "requires physical, circumstantial or causal proximity\". Also in "
            "PP v Smith, Judge crashed out."
        ),
    },
    {
        "role": "assistant",
        "content": json.dumps(
            {
                "cases": [
                    {
                        "raw_mention": "Spandeck Engineering v DSTA [2007] 4 SLR(R) 100",
                        "type": "slr_citation",
                        "canonical_query": "Spandeck Engineering v DSTA [2007] 4 SLR(R) 100",
                        "citation": "[2007] 4 SLR(R) 100",
                        "audit_context": {
                            "asserted_holdings": [
                                "A two-stage test applies to determine duty of care.",
                            ],
                            "direct_quotes": [
                                "first stage requires physical, circumstantial or causal proximity",
                            ],
                        },
                    },
                    {
                        "raw_mention": "PP v Smith",
                        "type": "informal",
                        "canonical_query": "PP v Smith",
                        "citation": None,
                        "audit_context": {
                            "asserted_holdings": [
                                "Judge crashed out.",
                            ],
                            "direct_quotes": [],
                        },
                    },
                ]
            },
            ensure_ascii=False,
        ),
    },
    {
        "role": "user",
        "content": (
            "In Lim Meng Suang v AG [2014] SGCA 53 the court upheld the "
            "constitutionality of s 377A. It also held that the provision did not "
            "violate Article 12. Counsel argued the opposite, but that is not what "
            "the court decided."
        ),
    },
    {
        "role": "assistant",
        "content": json.dumps(
            {
                "cases": [
                    {
                        "raw_mention": "Lim Meng Suang v AG [2014] SGCA 53",
                        "type": "neutral_citation",
                        "canonical_query": "Lim Meng Suang v AG [2014] SGCA 53",
                        "citation": "[2014] SGCA 53",
                        "audit_context": {
                            "asserted_holdings": [
                                "The court upheld the constitutionality of s 377A.",
                                "The provision did not violate Article 12.",
                            ],
                            "direct_quotes": [],
                        },
                    }
                ]
            },
            ensure_ascii=False,
        ),
    },
]


class CaseCitation(BaseModel):
    year: Optional[str] = None
    court: Optional[str] = None
    number: Optional[str] = None
    volume: Optional[str] = None
    report: Optional[str] = None
    page: Optional[str] = None


class CaseAuditTarget(BaseModel):
    asserted_holdings: List[str] = Field(
        default_factory=list,
        description=(
            "Atomic list of every distinct legal principle, holding, or claim "
            "the author asserts this case stands for. One holding per string. "
            "Do not include verbatim quotations here."
        ),
    )
    direct_quotes: List[str] = Field(
        default_factory=list,
        description=(
            "Every verbatim quotation attributed to this case, copied exactly "
            "from inside the quotation marks (without the marks). Must include "
            "all \"...\" / '...' spans tied to the case."
        ),
    )


class CaseQuery(BaseModel):
    raw_mention: str
    type: Literal[
        "neutral_citation",
        "slr_citation",
        "case_title",
        "informal",
    ]
    canonical_query: str
    citation: Optional[str] = None
    audit_context: Optional[CaseAuditTarget] = None


class ExtractionContainer(BaseModel):
    cases: List[CaseQuery]


SCHEMA_JSON = json.dumps(
    ExtractionContainer.model_json_schema(),
    ensure_ascii=False,
)


def _api_key() -> str:
    key = os.environ.get("OPENROUTER_API_KEY")
    if not key:
        raise RuntimeError(
            "OPENROUTER_API_KEY environment variable is not set."
        )
    return key


def _parse_container(raw_json: str) -> ExtractionContainer:
    raw = raw_json.strip()

    # Strip Markdown code fences if the model wrapped its reply in them.
    if raw.startswith("```"):
        raw = re.sub(r"^```[A-Za-z]*\n?|\n?```$", "", raw).strip()

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", raw, re.DOTALL)
        if not match:
            raise ValueError(
                "Model returned no valid JSON object: "
                + raw[:500]
            )
        data = json.loads(match.group(0))

    if isinstance(data, list):
        data = {"cases": data}

    return ExtractionContainer.model_validate(data)


# Extracts case mentions and citation information from AI output
def extract_case_queries(
    ai_response_text: str,
    model_name: str = DEFAULT_MODEL,
    cost_tracker=None,
) -> List[CaseQuery]:
    response = requests.post(
        f"{OPENROUTER_BASE_URL}/chat/completions",
        headers={
            "Authorization": f"Bearer {_api_key()}",
            "Content-Type": "application/json",
        },
        json={
            "model": model_name,
            "messages": [
                {
                    "role": "system",
                    "content": SYSTEM_PROMPT + SCHEMA_JSON,
                },
                *FEW_SHOT_EXAMPLES,
                {
                    "role": "user",
                    "content": ai_response_text,
                },
            ],
            "temperature": 0.0,
            "response_format": {"type": "json_object"},
            # Extraction is structured JSON — skip DeepSeek thinking for speed.
            "reasoning": {"enabled": False},
        },
        timeout=120,
    )

    response.raise_for_status()

    payload = response.json()

    if cost_tracker is not None:
        usage = payload.get("usage") or {}
        cost_tracker.add(usage.get("cost"))

    content = payload["choices"][0]["message"]["content"]
    parsed_data = _parse_container(content)

    return parsed_data.cases


# Example usage
ai_text = """
Under Singapore law, proximity is governed by Spandeck Engineering v DSTA [2007] 4 SLR(R) 100.
The court held that a two-stage test applies to determine duty of care.
Later, the judge noted "first stage requires physical, circumstantial or causal proximity".
Also in PP v Smith, Judge crashed out.
"""

if __name__ == "__main__":
    cases = extract_case_queries(ai_text)

    for case in cases:
        print(case.model_dump_json(indent=2))