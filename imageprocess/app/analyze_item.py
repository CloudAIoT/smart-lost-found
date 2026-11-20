"""Local two-photo validation tool; the production model API belongs to the backend."""
import argparse
import hashlib
import json
from pathlib import Path
import sys
from time import perf_counter
from uuid import UUID, uuid4

from app.core.config import Settings
from app.core.errors import ServiceError
from app.services.item_processing import ImageInput, parse_item_output, prepare_item
from app.services.vision_service import VisionService


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('images', type=Path, nargs=2, metavar='PHOTO')
    parser.add_argument('--category')
    parser.add_argument('--registration-id', type=UUID)
    parser.add_argument('--image-ids', type=UUID, nargs=2)
    parser.add_argument('--backend', choices=('gemini', 'mlx'), default='gemini')
    parser.add_argument('--model')
    parser.add_argument('--prepare-only', action='store_true', help='Validate and prepare without calling a model')
    parser.add_argument('--output', type=Path)
    args = parser.parse_args(argv)
    started = perf_counter()
    try:
        overrides = {'inference_backend': args.backend}
        if args.model:
            overrides['gemini_model_name' if args.backend == 'gemini' else 'model_name'] = args.model
        settings = Settings(**overrides)
        ids = args.image_ids or (uuid4(), uuid4())
        inputs = []
        for image_id, path in zip(ids, args.images):
            try:
                with path.open('rb') as source:
                    content = source.read(settings.max_upload_bytes + 1)
            except OSError:
                raise ServiceError('IMAGE_READ_FAILED', 'Cannot read the local image file.') from None
            inputs.append(ImageInput(image_id, content))
        prepared = prepare_item(args.registration_id or uuid4(), tuple(inputs), category=args.category, settings=settings)
        meta = {'image_sha256': list(prepared.image_sha256),
                'prepared_sizes': [list(image.size) for image in prepared.images],
                'prompt_sha256': hashlib.sha256(prepared.prompt.encode()).hexdigest()}
        if args.prepare_only:
            result = {'status': 'prepared_only', 'inference_performed': False,
                      'registration_id': str(prepared.registration_id), 'image_ids': [str(value) for value in prepared.image_ids],
                      'input_category': prepared.input_category, 'warnings': list(prepared.warnings),
                      'comparison_metadata': meta, 'prompt': prepared.prompt, 'response_schema': prepared.response_schema}
        else:
            service = VisionService(settings)
            try:
                service.load()
                inference_started = perf_counter()
                raw = service.infer_images(prepared.images, category=prepared.input_category)
                inference_ms = (perf_counter() - inference_started) * 1000
                validated = parse_item_output(prepared, raw)
                usage = service.usage
            finally:
                service.close()
            result = {'success': True, **validated.model_dump(mode='json'),
                      'backend': args.backend, 'model_name': settings.selected_model_name,
                      'usage': usage, 'comparison_metadata': meta,
                      'timings_ms': {'inference': round(inference_ms, 2), 'total': round((perf_counter()-started)*1000, 2)}}
        encoded = json.dumps(result, ensure_ascii=False, indent=2)
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(encoded+'\n', encoding='utf-8')
        print(encoded)
        return 0
    except ServiceError as exc:
        print(json.dumps({'success': False, 'error': {'code': exc.code, 'message': exc.message}}))
        return 1
    except Exception as exc:
        print(json.dumps({'success': False, 'error': {'code': 'ITEM_ANALYSIS_FAILED', 'exception_type': type(exc).__name__}}))
        return 1


if __name__ == '__main__':
    sys.exit(main())
