import json

from app.compare_results import build_report, main


def result():
    return {'success': True, 'backend': 'mlx', 'model_name': 'test-model', 'input_category': '필통',
            'data': {'category': 'pencil_case', 'color': ['black'], 'material': 'unknown', 'brand': None,
                     'visible_text': ['a|b'], 'distinctive_features': [], 'description': 'A black pencil case.'},
            'comparison_metadata': {'image_sha256': 'image', 'prepared_image_sha256': 'pixels',
                                    'prompt_sha256': 'prompt', 'prepared_size': [64, 32]}}


def test_report_checks_conditions_and_does_not_award_accuracy():
    left, right = result(), result()
    right['backend'] = 'gemini'
    right['comparison_metadata']['image_sha256'] = 'other-image'
    report = build_report(left, right)
    assert 'image_sha256: 불일치' in report
    assert 'prompt_sha256: 일치' in report
    assert '정확도 점수나 자동 우열 판정은 제공하지 않습니다' in report
    assert 'a\\|b' in report


def test_legacy_results_are_marked_unverifiable_and_saved_offline(tmp_path, capsys):
    left, right = result(), result()
    left.pop('comparison_metadata')
    a, b, report = tmp_path/'qwen.json', tmp_path/'gemini.json', tmp_path/'reports'/'comparison.md'
    a.write_text(json.dumps(left), encoding='utf-8')
    b.write_text(json.dumps(right), encoding='utf-8')
    assert main([str(a), str(b), '--output', str(report)]) == 0
    assert '확인 불가' in report.read_text(encoding='utf-8')


def test_failed_results_are_not_compared_as_valid_features(tmp_path, capsys):
    path = tmp_path/'failed.json'
    path.write_text('{"success":false}', encoding='utf-8')
    assert main([str(path), str(path), '--output', str(tmp_path/'report.md')]) == 1
    assert json.loads(capsys.readouterr().out)['error']['code'] == 'COMPARISON_FAILED'
    assert not (tmp_path/'report.md').exists()
