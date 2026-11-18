import json

from pypdf import PdfReader

from accordtrace_testbed.configuration import inspect
from accordtrace_testbed.config import Settings
from accordtrace_testbed.pdf_report import metric_rows, render
from accordtrace_testbed.report import build
from accordtrace_testbed.runner import write


def test_config_check_never_returns_secrets():
    result = inspect(Settings(backend_api_token='private-backend-token', gemini_api_key='private-judge-key',
                              gemini_model='explicit-model', gemini_free_tier_confirmed=True))
    text = json.dumps(result)
    assert 'private-backend-token' not in text and 'private-judge-key' not in text
    assert result['judge_configured'] and result['live_providers_checked'] is False


def test_future_metrics_and_statistics_are_discovered(tmp_path):
    report = {'identity': {'mode': 'retrieval'}, 'metrics': {
        'future_custom_metric': {'mean': .75, 'scored_cases': 2, 'observed_cases': 5, 'confidence_interval': [.5, 1.]},
        'missing_metric': {'mean': None, 'scored_cases': 0},
    }, 'judge_coverage': {'future_judge:pending': 5}, 'future_metadata': {'new_field': 'visible'},
        'limitations': ['A < b & c is plain text.']}
    assert len(metric_rows(report, {})) == 2
    path = render(report, tmp_path/'report.pdf')
    text = '\n'.join(page.extract_text() for page in PdfReader(path).pages)
    for expected in ['future_custom_metric', 'confidence interval', '0.7500', 'Not measured',
                     'future_judge:pending', 'new_field', 'A < b & c is plain text.']:
        assert expected.lower() in ' '.join(text.lower().split())
    assert not path.with_suffix('.pdf.tmp').exists()


def test_automatic_pdf_for_empty_report_and_long_metric_tables(tmp_path):
    write(tmp_path/'manifest.json', {'identity': {'mode':'retrieval','snapshot_id':'s','case_ids':['a']}})
    report = build(tmp_path)
    assert (tmp_path/'report.pdf').read_bytes().startswith(b'%PDF-')
    report['metrics'] = {f'new_metric_{i:03}': {'mean':i/100, 'scored_cases':1, 'observed_cases':4} for i in range(50)}
    report['judge_status_reasons'] = {'long_reason': 'unavailable_'*800}
    render(report, tmp_path/'report.pdf')
    reader = PdfReader(tmp_path/'report.pdf')
    assert len(reader.pages) > 2
    assert 'new_metric_049' in '\n'.join(p.extract_text() for p in reader.pages)


def test_pdf_failure_preserves_previous_file(tmp_path):
    path = tmp_path/'report.pdf'
    render({'identity':{},'metrics':{}}, path)
    previous = path.read_bytes()
    invalid = tmp_path/'invalid.json'
    invalid.write_text('{"accent_color":"bad"}', encoding='utf-8')
    import pytest
    with pytest.raises(ValueError, match='accent_color'):
        render({'metrics':{}}, path, invalid)
    assert path.read_bytes() == previous


def test_failed_collection_still_reports_partial_run(tmp_path, monkeypatch):
    import pytest
    import accordtrace_testbed.cli as cli
    import accordtrace_testbed.runner as runner
    monkeypatch.setattr(cli, 'ROOT', tmp_path)
    monkeypatch.setattr(cli, 'load', lambda path: [])
    def fail_after_manifest(settings, cases, directory, *args):
        write(directory/'manifest.json', {'identity': {'mode':'retrieval','snapshot_id':'s','case_ids':['a']}})
        raise RuntimeError('synthetic_collection_failure')
    monkeypatch.setattr(runner, 'collect', fail_after_manifest)
    monkeypatch.setattr('sys.argv', ['accordtrace-testbed','collect','--run','partial'])
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 1
    directory = tmp_path/'.runtime/partial'
    assert (directory/'report.pdf').read_bytes().startswith(b'%PDF-')
    assert json.loads((directory/'report.json').read_text())['complete_cases'] == 0
    assert not (directory/'.writer.lock').exists()
