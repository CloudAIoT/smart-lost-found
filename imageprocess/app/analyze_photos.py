"""Analyze a directory with the current seven-common-field schema and filename category hints."""
import argparse
import json
from pathlib import Path
import sys
from tempfile import mkdtemp

from app.analyze_image import analyze_file
from app.core.config import Settings
from app.core.errors import ServiceError
from app.explore_features import category_from_filename
from app.models.schemas import COMMON_FEATURE_FIELDS


def _save(path: Path, value) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def _cell(value) -> str:
    return json.dumps(value, ensure_ascii=False).replace('|', '\\|').replace('\n', ' ')


def write_summary(output: Path, records: list[dict], manifest: dict) -> None:
    successes = [record for record in records if record['success']]
    lines = ['# 공통 필드 7개와 특이 특징', '',
             f"모델: `{manifest['model_name']}` · 성공 {len(successes)}/{len(manifest['inputs'])}장", '',
             '입력은 사진과 파일명에서 얻은 카테고리이다. 브랜드·캐릭터 이름과 정답 특징은 사전에 제공하지 않았다.',
             '확인하지 못한 값은 null이다. 브랜드·캐릭터 식별은 모델의 판단이므로 사진과 함께 검토한다.', '',
             '## 공통 필드의 관찰 가능 여부', '', '| 필드 | 값 있음 | null |', '|---|---:|---:|']
    for name in COMMON_FEATURE_FIELDS:
        present = sum(record['data'][name] is not None for record in successes)
        lines.append(f'| {name} | {present} | {len(successes) - present} |')
    for record in records:
        lines += ['', '## ' + record['image'], '', f"[결과 JSON]({record['result_file']})", '']
        if not record['success']:
            lines += [record['error']['code'] + ': ' + record['error']['message']]
            continue
        lines += ['| 공통 필드 | 값 |', '|---|---|']
        lines.extend(f"| {name} | {_cell(record['data'][name])} |" for name in COMMON_FEATURE_FIELDS)
        lines += ['', '특이 특징:', '', _cell(record['data']['distinctive_features']), '',
                  f"API/모델 추론 시간: {record['timings_ms']['inference'] / 1000:.2f}초"]
    (output / 'report.md').write_text('\n'.join(lines) + '\n', encoding='utf-8')


def run_batch(photos: Path, output_root: Path, settings: Settings, *, start_from: str | None = None,
              analyzer=None) -> tuple[Path, list[dict]]:
    all_paths = sorted(path for path in photos.iterdir()
                   if path.is_file() and path.suffix.lower() in {'.jpg', '.jpeg', '.png', '.webp'})
    if not all_paths:
        raise ServiceError('NO_IMAGES', 'No JPEG, PNG or WEBP images found in the input directory.')
    start_index = 0
    if start_from is not None:
        names = [path.name for path in all_paths]
        if start_from not in names:
            raise ServiceError('START_IMAGE_NOT_FOUND', 'The --start-from filename must match an image in the input directory.')
        start_index = names.index(start_from)
    paths = all_paths[start_index:]
    inputs = [{'image': path.name, 'category': category_from_filename(path)} for path in paths]
    output_root.mkdir(parents=True, exist_ok=True)
    output = Path(mkdtemp(prefix='run-', dir=output_root))
    manifest = {'feature_schema': 'fixed_common_v2', 'backend': settings.inference_backend,
                'model_name': settings.selected_model_name, 'inputs': inputs,
                'start_from': start_from, 'skipped_before_start': [path.name for path in all_paths[:start_index]],
                'common_fields': list(COMMON_FEATURE_FIELDS), 'unknown_value': None,
                'brand_or_character_answers_supplied': False, 'status': 'running'}
    _save(output / 'manifest.json', manifest)
    analyzer = analyzer if analyzer is not None else analyze_file
    records = []
    try:
        for index, (path, item) in enumerate(zip(paths, inputs), start_index + 1):
            print(f'[{index}/{len(all_paths)}] {path.name} ({item["category"]})', flush=True)
            try:
                result = analyzer(path, settings, category=item['category'])
            except ServiceError as exc:
                result = {'success': False, 'input_category': item['category'],
                          'error': {'code': exc.code, 'message': exc.message}}
            result.update(image=path.name, result_file=f'{index:02d}_{path.stem}.json')
            records.append(result)
            _save(output / result['result_file'], result)
            write_summary(output, records, manifest)
            print('  OK' if result['success'] else '  FAILED ' + result['error']['code'], flush=True)
            if not result['success'] and result['error']['code'].startswith('GEMINI_'):
                break
    finally:
        manifest.update(status='completed' if len(records) == len(paths) and all(r['success'] for r in records) else 'incomplete',
                        attempted=len(records), succeeded=sum(r['success'] for r in records))
        _save(output / 'manifest.json', manifest)
        write_summary(output, records, manifest)
    return output, records


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('photos', type=Path)
    parser.add_argument('--start-from', help='Start at this exact filename (inclusive), in sorted filename order')
    parser.add_argument('--output-dir', type=Path, default=Path('outputs/fixed_features'))
    parser.add_argument('--backend', choices=('gemini', 'mlx', 'mock'), default='gemini')
    parser.add_argument('--model')
    parser.add_argument('--max-output-tokens', type=int, default=8192, help='Gemini token budget')
    args = parser.parse_args(argv)
    try:
        overrides = {'inference_backend': args.backend, 'gemini_max_output_tokens': args.max_output_tokens}
        if args.model:
            overrides['gemini_model_name' if args.backend == 'gemini' else 'model_name'] = args.model
        output, records = run_batch(args.photos, args.output_dir, Settings(**overrides), start_from=args.start_from)
        print(json.dumps({'output_dir': str(output.resolve()), 'succeeded': sum(r['success'] for r in records),
                          'attempted': len(records)}, ensure_ascii=False))
        return 0 if records and all(r['success'] for r in records) else 1
    except ServiceError as exc:
        print(json.dumps({'success': False, 'error': {'code': exc.code, 'message': exc.message}}))
        return 1
    except Exception as exc:
        print(json.dumps({'success': False, 'error': {'code': 'BATCH_ANALYSIS_FAILED', 'exception_type': type(exc).__name__}}))
        return 1


if __name__ == '__main__':
    sys.exit(main())
