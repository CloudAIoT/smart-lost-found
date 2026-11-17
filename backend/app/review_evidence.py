"""Explicit internal review, never inferred from the model's output.

python -m app.review_evidence --registration-id UUID --file reviewed.json
The file is a JSON array; each entry includes the current analysis_id and reviewer.
"""
import argparse
from datetime import datetime
import json
from pathlib import Path
from uuid import UUID

from sqlalchemy import select
from app.db import SessionLocal
from app.models import Registration
from app.ai_ownership_verification.search import valid_evidence


def review(session, registration_id, entries):
    reg = session.scalar(select(Registration).where(Registration.id == registration_id).with_for_update())
    if reg is None or reg.analysis_status != 'succeeded':
        raise ValueError('A successfully analyzed registration is required')
    if not isinstance(entries, list) or len(entries) > 20:
        raise ValueError('Provide at most 20 evidence entries')
    seen, reviewed = set(), []
    for entry in entries:
        ev = valid_evidence(reg, entry)
        if ev is None or ev.feature_id in seen:
            raise ValueError('Invalid, stale or duplicate evidence')
        if not ev.reviewed_by.strip() or len(ev.reviewed_by) > 200:
            raise ValueError('Reviewer identity is required')
        timestamp = datetime.fromisoformat(ev.reviewed_at)
        if timestamp.tzinfo is None:
            raise ValueError('Review timestamp must include a timezone')
        seen.add(ev.feature_id)
        reviewed.append(ev.model_dump())
    reg.ownership_evidence = reviewed
    session.commit()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--registration-id', type=UUID, required=True)
    parser.add_argument('--file', type=Path, required=True)
    args = parser.parse_args()
    with SessionLocal() as session:
        review(session, args.registration_id, json.loads(args.file.read_text(encoding='utf-8')))
    print('Review saved.')


if __name__ == '__main__':
    main()
