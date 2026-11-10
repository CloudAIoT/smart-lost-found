"""Compare saved feature outputs offline; never load models or call an API."""
import argparse
import html
import json
from pathlib import Path

from app.models.schemas import Features


def read_result(path: Path) -> dict:
    result = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(result, dict) or result.get('success') is not True:
        raise ValueError('Both inputs must be successful analyze_image results.')
    Features.model_validate(result.get('data'))
    for name in ('comparison_metadata', 'timings_ms', 'usage'):
        if result.get(name) is not None and not isinstance(result[name], dict):
            raise ValueError('Invalid result metadata.')
    return result


def cell(value) -> str:
    return html.escape(json.dumps(value, ensure_ascii=False)).replace('|', r'\|').replace('\n', ' ')


def build_report(left: dict, right: dict) -> str:
    lines = ['# 이미지 특징 추출 결과 비교', '',
             '저장된 결과를 나란히 보여주는 검토 자료입니다. 정확도 점수나 자동 우열 판정은 제공하지 않습니다.', '',
             '| 항목 | 결과 A | 결과 B |', '| --- | --- | --- |']
    for name in ('backend', 'model_name', 'input_category'):
        lines.append(f'| {name} | {cell(left.get(name))} | {cell(right.get(name))} |')
    for name in Features.model_fields:
        lines.append(f'| {name} | {cell(left["data"][name])} | {cell(right["data"][name])} |')
    for name in ('model_load', 'inference', 'total'):
        lines.append(f'| {name} (ms) | {cell((left.get("timings_ms") or {}).get(name))} | {cell((right.get("timings_ms") or {}).get(name))} |')
    lines.extend(['', '## 비교 조건', ''])
    for name in ('image_sha256', 'prepared_image_sha256', 'prepared_size', 'prompt_sha256'):
        a = (left.get('comparison_metadata') or {}).get(name)
        b = (right.get('comparison_metadata') or {}).get(name)
        state = '확인 불가: 이전 형식 결과에 메타데이터가 없습니다.' if a is None or b is None else (
            '일치' if a == b else '불일치: 같은 조건으로 다시 실행해야 합니다.')
        lines.append(f'- {name}: {state}')
    for label, result in (('A', left), ('B', right)):
        meta = result.get('comparison_metadata') or {}
        lines.append(f'- {label} 생성 설정: temperature={cell(meta.get("temperature"))}, '
                     f'max_output_tokens={cell(meta.get("max_output_tokens"))}, '
                     f'structured_output={cell(meta.get("structured_output"))}')
        lines.append(f'- {label} 품질 경고: {cell(result.get("warnings", []))}')
        lines.append(f'- {label} 토큰 사용량: {cell(result.get("usage"))}')
    lines.extend(['', '## 사진과 함께 확인할 항목', '',
                  '- 분류와 색상이 실제 물품과 일치하는가?',
                  '- 재질, 브랜드, 부착물, 흠집을 근거 없이 생성하지 않았는가?',
                  '- visible_text가 사진의 표기와 일치하는가?',
                  '- 확인하기 어려운 특징은 unknown/null/빈 배열로 남겼는가?',
                  '- 서로 다른 사진과 반복 실행에서도 결과가 유지되는가?', '',
                  'Gemini 시간에는 네트워크 왕복이 포함되고, Qwen의 모델 로딩 시간은 별도입니다. '
                  '두 모델의 생성 한도와 JSON 강제 방식도 다르므로 해당 설정을 함께 검토하세요. '
                  '사진·프롬프트가 일치해도 같은 오답일 수 있으며, JSON 검증 통과는 내용의 정확성을 보장하지 않습니다.', ''])
    return '\n'.join(lines)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description='Compare two saved model results without inference or API calls.')
    parser.add_argument('left', type=Path)
    parser.add_argument('right', type=Path)
    parser.add_argument('--output', type=Path, default=Path('outputs/comparison.md'))
    args = parser.parse_args(argv)
    try:
        report = build_report(read_result(args.left), read_result(args.right))
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(report, encoding='utf-8')
        print(report)
        return 0
    except (OSError, ValueError, TypeError):
        print(json.dumps({'success': False, 'error': {'code': 'COMPARISON_FAILED',
                         'message': 'Read two successful feature result JSON files and use a writable output path.'}}))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
