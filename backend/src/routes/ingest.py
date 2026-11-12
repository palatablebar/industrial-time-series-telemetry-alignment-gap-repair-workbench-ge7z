from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from src.config.database import get_db
from src.controllers import ingest_controller
from src.middlewares.auth import get_current_user
from src.middlewares.rbac import WRITE_ROLES, require_roles
from src.models import User
from src.schemas.ingest import (
    DeliveryResult,
    IngestBatchCreate,
    IngestBatchDetail,
    IngestBatchList,
    SegmentDelivery,
)

router = APIRouter(prefix="/ingest-batches", tags=["ingest"])


@router.get("", response_model=IngestBatchList)
def list_batches(db: Session = Depends(get_db), _user: User = Depends(get_current_user)) -> dict:
    return ingest_controller.list_batches(db)


@router.post("")
def create_batch(
    payload: IngestBatchCreate,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(*WRITE_ROLES)),
) -> dict:
    return ingest_controller.create_batch(request, db, payload, user)


@router.get("/{batch_id}", response_model=IngestBatchDetail)
def batch_detail(
    batch_id: int, db: Session = Depends(get_db), _user: User = Depends(get_current_user)
) -> dict:
    return ingest_controller.batch_detail(db, batch_id)["batch"]


@router.post("/{batch_id}/segments/{segment_key}", response_model=DeliveryResult)
def deliver_segment(
    batch_id: int,
    segment_key: str,
    payload: SegmentDelivery,
    request: Request,
    db: Session = Depends(get_db),
    _user: User = Depends(require_roles(*WRITE_ROLES)),
) -> dict:
    return ingest_controller.deliver_segment(request, db, batch_id, segment_key, payload)


@router.post("/{batch_id}/segments/{segment_key}/simulate", response_model=DeliveryResult)
def simulate_segment(
    batch_id: int,
    segment_key: str,
    request: Request,
    db: Session = Depends(get_db),
    _user: User = Depends(require_roles(*WRITE_ROLES)),
) -> dict:
    """Demo-only gateway stand-in: generates that segment's rows and pushes them."""
    return ingest_controller.replay_segment(request, db, batch_id, segment_key)
