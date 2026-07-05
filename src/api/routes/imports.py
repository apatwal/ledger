"""Import history + reassign/undo (v5.2)."""
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select, update, delete
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import ImportBatch, Transaction
from ..schemas import ImportBatchOut

router = APIRouter(prefix="/imports", tags=["imports"])


@router.get("", response_model=list[ImportBatchOut])
def list_imports(db: Session = Depends(get_db)):
    """All import batches, newest first."""
    stmt = select(ImportBatch).order_by(ImportBatch.created_at.desc(), ImportBatch.id.desc())
    return db.execute(stmt).scalars().all()


class ReassignRequest(BaseModel):
    account: Optional[str] = None


class ReassignResponse(BaseModel):
    updated: int


@router.post("/{batch_id}/reassign", response_model=ReassignResponse)
def reassign_import(batch_id: int, body: ReassignRequest, db: Session = Depends(get_db)):
    """Set the account on a batch AND all its transactions. Empty/null => Unassigned (null)."""
    batch = db.get(ImportBatch, batch_id)
    if batch is None:
        raise HTTPException(status_code=404, detail=f"Import batch {batch_id} not found")

    new_account = body.account.strip() if body.account and body.account.strip() else None
    batch.account = new_account
    result = db.execute(
        update(Transaction).where(Transaction.batch_id == batch_id).values(account=new_account)
    )
    db.commit()
    return ReassignResponse(updated=int(result.rowcount or 0))


@router.delete("/{batch_id}", status_code=204)
def delete_import(batch_id: int, db: Session = Depends(get_db)):
    """Undo an import: delete the batch and all its transactions."""
    batch = db.get(ImportBatch, batch_id)
    if batch is None:
        raise HTTPException(status_code=404, detail=f"Import batch {batch_id} not found")
    db.execute(delete(Transaction).where(Transaction.batch_id == batch_id))
    db.delete(batch)
    db.commit()
