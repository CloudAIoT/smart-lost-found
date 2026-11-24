"""Inspect saved analysis through an operator's DB connection, without calling AI."""

import argparse
import json
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app import services
from app.errors import BusinessError
from imageprocess.app.models.schemas import Features


def inspect_analysis(session: Session, registration_id: UUID) -> dict:
    registration = services.registration(session, registration_id)
    images = services.images(session, registration_id)
    image_ids = [str(image.id) for image in images]
    inputs_match = ([image.shot_order for image in images] == [1, 2]
                    and registration.analysis_input_image_ids == image_ids)
    try:
        Features.model_validate(registration.analysis_features, strict=True)
        schema_valid = True
    except ValidationError:
        schema_valid = False
    return {
        "registration_id": str(registration.id),
        "status": registration.status,
        "analysis_status": registration.analysis_status,
        "images": [{"id": str(image.id), "shot_order": image.shot_order,
                    "byte_size": image.byte_size} for image in images],
        "analysis_input_image_ids": registration.analysis_input_image_ids,
        "input_images_match": inputs_match,
        "feature_schema_valid": schema_valid,
        "analysis_features": registration.analysis_features,
        "verified": registration.analysis_status == "succeeded" and inputs_match and schema_valid,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("registration_id", type=UUID)
    parser.add_argument("--require-succeeded", action="store_true",
                        help="Exit nonzero unless saved features and both input IDs validate.")
    args = parser.parse_args()
    from app.db import SessionLocal
    try:
        with SessionLocal() as session:
            result = inspect_analysis(session, args.registration_id)
    except BusinessError as error:
        print(json.dumps({"error": error.code}))
        return 1
    except SQLAlchemyError:
        print(json.dumps({"error": "ANALYSIS_INSPECTION_DB_FAILED"}))
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return int(args.require_succeeded and not result["verified"])


if __name__ == "__main__":
    raise SystemExit(main())
