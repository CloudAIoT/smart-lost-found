"""Run the image-to-feature pipeline directly, without an HTTP server."""
import argparse
import hashlib
import json
import sys
from pathlib import Path
from time import perf_counter

from app.core.config import Settings
from app.core.errors import ServiceError
from app.services.preprocessing import preprocess
from app.services.vision_service import VisionService, build_prompt, normalize_category, parse_features


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description='Extract lost-item features from a local photograph.')
    parser.add_argument('image', type=Path, help='JPEG, PNG or WEBP photograph')
    # A local validation run must explicitly select mock, even if .env uses it.
    parser.add_argument('--backend', choices=('mlx', 'gemini', 'mock'), default='mlx')
    parser.add_argument('--model', help='Override the selected backend model (Qwen repository/path or Gemini model ID)')
    parser.add_argument('--category', help='Finder-selected category, such as 필통 or pencil_case (optional)')
    parser.add_argument('--output', type=Path, help='Save the complete validated result JSON')
    return parser


def analyze_file(path: Path, settings: Settings, service=None, *, category: str | None = None) -> dict:
    start = perf_counter()
    category = normalize_category(category)
    try:
        with path.open('rb') as source:
            content = source.read(settings.max_upload_bytes + 1)
    except OSError:
        raise ServiceError('IMAGE_READ_FAILED', 'Cannot read the local image file.') from None
    if len(content) > settings.max_upload_bytes:
        raise ServiceError('IMAGE_TOO_LARGE', 'Image exceeds the file byte limit.', 413)

    preprocessing_started = perf_counter()
    prepared = preprocess(content, settings)
    preprocessing_ms = (perf_counter() - preprocessing_started) * 1000
    service = service if service is not None else VisionService(settings)
    model_started = perf_counter()
    try:
        service.load()
    except ServiceError:
        raise
    except Exception as exc:
        raise ServiceError('MODEL_LOAD_FAILED',
                           f'Cannot load model ({type(exc).__name__}). Check the Python environment, MLX dependencies and model files.',
                           503) from None
    model_ms = (perf_counter() - model_started) * 1000
    inference_started = perf_counter()
    try:
        raw = service.infer(prepared.image, category=category)
    except ServiceError:
        raise
    except Exception as exc:
        raise ServiceError('AI_INFERENCE_FAILED', f'Image inference failed ({type(exc).__name__}).', 502) from None
    finally:
        closer = getattr(service, 'close', None)
        if callable(closer):
            closer()
    inference_ms = (perf_counter() - inference_started) * 1000
    features = parse_features(raw)
    flags = list(prepared.warnings)
    if settings.inference_backend == 'mock':
        flags.append('MOCK_INFERENCE')
    return {
        'success': True,
        'backend': settings.inference_backend,
        'model_name': settings.selected_model_name,
        'input_category': category,
        'data': features.model_dump(),
        'warnings': flags,
        'comparison_metadata': {
            'image_sha256': hashlib.sha256(content).hexdigest(),
            'prepared_image_sha256': hashlib.sha256(prepared.image.tobytes()).hexdigest(),
            'prepared_size': list(prepared.image.size),
            'prompt_sha256': hashlib.sha256(build_prompt(category).encode('utf-8')).hexdigest(),
            'temperature': 0.0,
            'structured_output': 'native_json_schema' if settings.inference_backend == 'gemini' else 'prompt_and_validation',
            'max_output_tokens': settings.gemini_max_output_tokens if settings.inference_backend == 'gemini' else settings.max_tokens,
        },
        'usage': service.usage if isinstance(getattr(service, 'usage', None), dict) else None,
        'timings_ms': {
            'preprocessing': round(preprocessing_ms, 2),
            'model_load': round(model_ms, 2),
            'inference': round(inference_ms, 2),
            'total': round((perf_counter() - start) * 1000, 2),
        },
    }


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        overrides = {'inference_backend': args.backend}
        if args.model:
            overrides['gemini_model_name' if args.backend == 'gemini' else 'model_name'] = args.model
        result = analyze_file(args.image, Settings(**overrides), category=args.category)
        encoded = json.dumps(result, ensure_ascii=False, indent=2)
        if args.output:
            try:
                args.output.parent.mkdir(parents=True, exist_ok=True)
                args.output.write_text(encoded + '\n', encoding='utf-8')
            except OSError:
                raise ServiceError('OUTPUT_WRITE_FAILED', 'Cannot write the result JSON file.') from None
        print(encoded)
        return 0
    except ServiceError as exc:
        print(json.dumps({'success': False, 'error': {'code': exc.code, 'message': exc.message}}, ensure_ascii=False))
        return 1
    except Exception as exc:
        print(json.dumps({'success': False, 'error': {'code': 'LOCAL_ANALYSIS_FAILED',
                         'message': f'Local analysis failed ({type(exc).__name__}). Check configuration and dependencies.'}}))
        return 1


if __name__ == '__main__':
    sys.exit(main())
