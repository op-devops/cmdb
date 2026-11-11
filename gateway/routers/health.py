from fastapi import APIRouter

from gateway.schemas.envelope import Envelope, json_response

router = APIRouter(tags=["health"])


@router.get("/healthz", response_model=Envelope)
def healthz():
    return json_response({"status": "ok"})
