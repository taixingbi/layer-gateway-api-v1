from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, Response

from app.core.config import get_settings
from app.schemas.feedback import FeedbackRequest

router = APIRouter(prefix="/api", tags=["feedback"])


@router.post("/feedback")
async def post_feedback(request: Request, payload: FeedbackRequest):
    settings = get_settings()
    if settings.orchestrator_contract != "flat_headers":
        raise HTTPException(
            status_code=501,
            detail="Feedback proxy is only available when orchestrator_contract=flat_headers",
        )
    client = request.app.state.orchestrator_client
    body = payload.model_dump(exclude_none=True)
    status_code, data = await client.post_feedback(body)
    if status_code == 204:
        return Response(status_code=204)
    if data is None:
        return JSONResponse(content={}, status_code=status_code)
    return JSONResponse(content=data, status_code=status_code)
