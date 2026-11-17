from pathlib import Path

import pytest

from accordtrace_backend.common.core import Settings, digest, identity
from accordtrace_backend.ingestion.pipeline import sources
from accordtrace_backend.ingestion.parsing import normalized_view, parse


def test_normalization_is_reversible_with_unicode_and_crlf():
    text='A\r\n  \U0001f600 B\tC'
    view,mapping=normalized_view(text)
    assert view=='A \U0001f600 B C'
    for char,(start,end) in zip(view,mapping):
        assert text[start:end].isspace() if char==' ' else text[start:end]==char
    assert mapping[2]==(5,6)


def test_scope_definitions_and_missing_targets():
    text='Section 1.01  Definitions.\n\n“Price” means $10.\n\nSection 2.01  Payment.\n(a) Pay the Price subject to Section 1.01 and Schedule C.\n(b) Not otherwise.'
    p=parse(text,'doc-a')
    assert len([s for s in p.spans if s['kind']=='section'])==2
    assert [t['name'] for t in p.terms]==['Price']
    assert p.uses
    assert any(r['status']=='verified' and r['target_label']=='1.01' for r in p.references)
    assert any(r['status']=='missing' and r['literal']=='Schedule C' for r in p.references)
    assert parse(text,'doc-b').terms[0]['id']!=p.terms[0]['id']
    assert p.record()==parse(text,'doc-a').record()
    for s in p.spans:
        assert digest(text[s['start']:s['end']].encode())==s['text_hash']


def test_ambiguous_definition_does_not_bind_arbitrarily():
    text='Section 1.1  Definitions.\n\n“Price” means $1.\n\n“Price” means $2.\n\nSection 2.1  Payment. Pay Price.'
    p=parse(text,'d')
    assert len(p.terms)==2
    assert not p.uses


def test_no_guess_for_toc_without_body():
    p=parse('TABLE OF CONTENTS\nSection 1.1 Definitions 2','d')
    assert not p.spans
    assert p.issues[0]['code']=='body_boundary_ambiguous'


def test_external_original_agreement_is_not_current_target():
    t='Section 1.1  Definitions.\nSection 2.1  Amendment. Under Section 1.1 of the Original Agreement.'
    refs=parse(t,'d').references
    assert any(r['status']=='out_of_corpus' and r['target_id'] is None for r in refs)


def test_parser_version_is_part_of_every_artifact_identity(monkeypatch):
    from accordtrace_backend.ingestion import parsing as parsing
    text='Section 1.1  Definitions.\n\n“Price” means $10.\n\nSection 2.1  Payment. Pay Price under Section 1.1.'
    before=parsing.parse(text,'doc')
    monkeypatch.setattr(parsing,'PARSER_VERSION','changed-test-version')
    after=parsing.parse(text,'doc')
    for name in ('spans','terms','uses','references'):
        assert getattr(before,name)
        assert not ({r['id'] for r in getattr(before,name)} & {r['id'] for r in getattr(after,name)})


def test_definition_context_keeps_nested_lists_and_multiple_paragraphs():
    text='Section 1.1  Definitions.\n\n“Action” means any\n\n(a) purchase; or\n(b) disposal.\n\n“Price” means $10.\n\nSection 2.1  Payment. Pay Price.'
    p=parse(text,'doc')
    term=next(t for t in p.terms if t['name']=='Action')
    definition=next(s for s in p.spans if s['id']==term['span_id'])
    quote=text[definition['start']:definition['end']]
    assert '(a) purchase' in quote and '(b) disposal.' in quote
    assert '“Price”' not in quote


def test_page_number_candidate_is_classified_without_removing_evidence():
    text='Section 1.1  Payment.\nPay $10.\n\n- 23 -\n\nSection 2.1  Closing.\nClose.'
    p=parse(text,'doc')
    page=next(i for i in p.issues if i['code']=='page_artifact_candidate')
    assert text[page['start']:page['end']]=='- 23 -'
    section=next(s for s in p.spans if s['kind']=='section' and s['label']=='1.1')
    assert '- 23 -' in text[section['start']:section['end']]


def test_definition_in_preamble_is_preserved():
    p=parse('This AGREEMENT AND PLAN OF MERGER.\n“Parent” means Acme.\n\nSection 1.1  Closing. Parent closes.','doc')
    term=next(t for t in p.terms if t['name']=='Parent')
    assert term and any(u['term_id']==term['id'] for u in p.uses)


@pytest.mark.parametrize('number',range(6))
def test_real_source_offsets_hierarchy_and_coverage(number):
    settings=Settings()
    file=settings.source_dir/f'contract_{number}.txt'
    if not file.exists(): pytest.skip('Read-only MAUD source mount unavailable')
    raw=file.read_bytes(); text=raw.decode('utf-8-sig')
    p=parse(text,identity(number,digest(raw)))
    sections=[s for s in p.spans if s['kind']=='section']
    assert len(sections)>40
    assert len(p.terms)>20
    assert any(r['status']=='verified' for r in p.references)
    coverage=sorted(p.coverage,key=lambda c:c['start'])
    assert coverage[0]['start']==0
    assert coverage[-1]['end']==len(text)
    assert all(a['end']==b['start'] for a,b in zip(coverage,coverage[1:]))
    for span in p.spans:
        assert 0<=span['start']<span['end']<=len(text)
        assert digest(text[span['start']:span['end']].encode())==span['text_hash']
    if number==0:
        sec=next(s for s in sections if s['label']=='7.1')
        assert sec['start']>290000
        term=next(t for t in p.terms if t['name']=='Per Share Cash Amount')
        definition=next(s for s in p.spans if s['id']==term['span_id'])
        assert '$115.00' in text[definition['start']:definition['end']]
    if number==2:
        assert any(s['label'].startswith('supplement:') for s in sections)


def test_manifest_validates_raw_hashes_and_rejects_changes(tmp_path):
    settings=Settings()
    if not settings.source_dir.exists(): pytest.skip('MAUD not mounted')
    docs,fp=sources(settings)
    assert len(docs)==6 and len(fp)==64
    with pytest.raises(FileNotFoundError): sources(Settings(source_dir=tmp_path))
