import json
import uvicorn
from typing import Iterator, List, Literal, Optional

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from extract_case_items import (
    DEFAULT_MODEL,
    extract_case_queries,
)
from main import run_audit_pipeline, run_audit_pipeline_stream
from claim_check import check_case_claims
from verification_check import (
    verify_case_basic_data,
    verify_cases_parallel,
)


app = FastAPI(
    title="LIT_team40 Case Audit API",
    description=(
        "Extracts legal case mentions from AI responses and verifies "
        "them against Singapore LawNet / OpenLaw."
    ),
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


###---###
# Schemas
###---###

class ExtractRequest(BaseModel):
    text: str = Field(
        description="The AI response text to scan for case mentions."
    )
    model_name: str = Field(
        default=DEFAULT_MODEL,
        description="OpenRouter model used for extraction.",
    )


class VerifyRequest(BaseModel):
    raw_mention: str = Field(
        default="",
        description="The exact raw text snippet mentioning the case.",
    )
    type: Literal[
        "neutral_citation",
        "slr_citation",
        "case_title",
        "informal",
    ] = Field(
        default="case_title",
    )
    canonical_query: str = Field(
        description="Cleaned case name/citation used as the LawNet search query."
    )
    citation: Optional[str] = None
    year: Optional[str] = None
    court: Optional[str] = None
    number: Optional[str] = None


class VerifyBatchRequest(BaseModel):
    cases: List[VerifyRequest]
    max_workers: int = Field(
        default=5,
        ge=1,
        le=20,
        description="Number of parallel LawNet browser sessions.",
    )


class AuditRequest(BaseModel):
    text: str = Field(
        description="The AI response text to audit."
    )
    model_name: str = Field(
        default=DEFAULT_MODEL,
    )
    max_workers: int = Field(
        default=5,
        ge=1,
        le=20,
    )
    check_statements: bool = Field(
        default=False,
        description=(
            "After verification, fact-check the AI's asserted holdings and "
            "direct quotes against the LawNet judgment for each confirmed case."
        ),
    )


class ClaimsRequest(BaseModel):
    url: str = Field(
        description=(
            "LawNet judgment URL or href, e.g. "
            "/openlaw/cases/citation/[2007]+SGCA+37?ref=sg-sc"
        ),
    )
    statements: List[str] = Field(
        default_factory=list,
        description="Statements the AI made about this case to fact-check.",
    )
    quotes: List[str] = Field(
        default_factory=list,
        description="Direct quotes the AI attributed to this case.",
    )
    model_name: str = Field(
        default=DEFAULT_MODEL,
    )


###---###
# Endpoints
###---###

@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.post("/extract")
def extract_case_mentions(req: ExtractRequest) -> dict:
    """Extract case mentions from an AI response text via OpenRouter LLM."""
    cases = extract_case_queries(
        req.text,
        model_name=req.model_name,
    )

    return {
        "count": len(cases),
        "cases": [case.model_dump() for case in cases],
    }


@app.post("/verify")
def verify_case(req: VerifyRequest, headless: bool = True) -> dict:
    """Verify a single case against LawNet."""
    case_data = req.model_dump(exclude_none=True)
    result = verify_case_basic_data(
        case_data,
        headless=headless,
    )
    return result


@app.post("/verify/batch")
def verify_cases(req: VerifyBatchRequest, headless: bool = True) -> dict:
    """Verify multiple cases against LawNet in parallel."""
    case_list = [
        case.model_dump(exclude_none=True)
        for case in req.cases
    ]

    results = verify_cases_parallel(
        case_list,
        headless=headless,
        max_workers=req.max_workers,
    )

    return {
        "count": len(results),
        "results": results,
    }


@app.post("/audit")
def audit_ai_response(req: AuditRequest, headless: bool = True) -> dict:
    """Full pipeline: LLM-extract case mentions, verify each on LawNet,
    and optionally fact-check the AI's statements against the judgments."""
    return run_audit_pipeline(
        req.text,
        model_name=req.model_name,
        headless=headless,
        max_workers=req.max_workers,
        check_statements=req.check_statements,
    )


@app.post("/audit/stream")
def audit_ai_response_stream(req: AuditRequest, headless: bool = True):
    """Same as /audit, but streams SSE progress events as work proceeds.

    Event types: phase, extracted, case_update, done, error.
    """

    def event_stream() -> Iterator[str]:
        for event in run_audit_pipeline_stream(
            req.text,
            model_name=req.model_name,
            headless=headless,
            max_workers=req.max_workers,
            check_statements=req.check_statements,
        ):
            yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.post("/claims")
def check_claims(req: ClaimsRequest, headless: bool = True) -> dict:
    """Fact-check statements and quotes about one case against its LawNet judgment."""
    return check_case_claims(
        req.url,
        statements=req.statements,
        quotes=req.quotes,
        model_name=req.model_name,
        headless=headless,
    )


if __name__ == "__main__":
    uvicorn.run(
        "api:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
    )
