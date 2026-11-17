"""Deterministic structural extraction. All ranges address immutable decoded text."""
import bisect
import re
from dataclasses import asdict, dataclass, field

from accordtrace_backend.common.core import PARSER_VERSION, digest, identity


def normalized_view(text: str) -> tuple[str, list[tuple[int, int]]]:
    """Collapse whitespace and retain start/end mappings for every output character."""
    chars, mapping = [], []
    for match in re.finditer(r'\s+|\S', text):
        chars.append(' ' if match.group().isspace() else match.group())
        mapping.append(match.span())
    return ''.join(chars), mapping


@dataclass
class Parsed:
    version_id: str
    spans: list[dict] = field(default_factory=list)
    terms: list[dict] = field(default_factory=list)
    references: list[dict] = field(default_factory=list)
    uses: list[dict] = field(default_factory=list)
    issues: list[dict] = field(default_factory=list)
    coverage: list[dict] = field(default_factory=list)

    def record(self) -> dict:
        return asdict(self)


def parse(text: str, version_id: str, component: bool = False) -> Parsed:
    if not component:
        boundaries=list(re.finditer(r'(?m)^\s*(?:EXHIBIT\s+[A-Z]|ANNEX\s+[IVX]+|AMENDMENT\s+NO\.?\s*\d)\b',text))
        # Headers before the agreement's first 10k characters can be navigation.
        boundaries=[m for m in boundaries if m.start()>10000]
        if boundaries:
            first=boundaries[0].start()
            main=parse(text[:first],version_id,True)
            for n,m in enumerate(boundaries):
                start=m.start(); end=boundaries[n+1].start() if n+1<len(boundaries) else len(text)
                scope='supplement:'+str(start)
                child=parse(text[start:end],identity(version_id,scope),True)
                ids={r['id']:identity(version_id,scope,r['id']) for records in
                     (child.spans,child.terms,child.references,child.uses,child.issues) for r in records}
                for records in (child.spans,child.terms,child.references,child.uses,child.issues):
                    for record in records:
                        for key in ('id','parent_id','span_id','source_id','target_id','term_id'):
                            if record.get(key) in ids: record[key]=ids[record[key]]
                        for key in ('start','end'):
                            if key in record:record[key]+=start
                        if 'label' in record:record['label']=scope+'/'+record['label']
                        if 'scope' in record:record['scope']=scope
                if not child.spans:
                    raw=text[start:end]
                    child.spans.append(dict(id=identity(version_id,PARSER_VERSION,scope,'unparsed'),start=start,end=end,
                        kind='clause',label=scope,parent_id=None,role='supplemental_unparsed',text_hash=digest(raw.encode())))
                for attr in ('spans','terms','references','uses','issues'):
                    getattr(main,attr).extend(getattr(child,attr))
                main.coverage.append(dict(start=start,end=end,classification='supplemental_document'))
                main.issues.append(dict(id=identity(version_id,PARSER_VERSION,scope,'scope_review'),code='supplement_scope_review',
                    start=start,end=end,detail='Separate attachment/amendment scope; no legal supersession inferred'))
            return main
    result = Parsed(version_id)

    def span(start, end, kind, label, parent=None, role='operative'):
        item = dict(id=identity(version_id, PARSER_VERSION, start, end, kind, label),
                    start=start, end=end, kind=kind, label=label, parent_id=parent,
                    role=role, text_hash=digest(text[start:end].encode('utf-8')))
        result.spans.append(item)
        return item

    def issue(code, start, end, detail):
        result.issues.append(dict(id=identity(version_id, PARSER_VERSION, code, start, end),
                                  code=code, start=start, end=end, detail=detail))

    preambles = list(re.finditer(
        r'\bThis\s+(?:AMENDED\s+AND\s+RESTATED\s+)?AGREEMENT\s+AND\s+PLAN\s+OF\s+MERGER'
        r'|AGREEMENT\s+AND\s+PLAN\s+OF\s+MERGER\s*,\s*dated', text, re.I))
    toc = re.search(r'TABLE\s+OF\s+CONTENTS', text, re.I)
    valid = [m for m in preambles if toc is None or m.start() > toc.end()]
    body_start = valid[0].start() if valid else 0
    if toc and not valid:
        issue('body_boundary_ambiguous', 0, len(text), 'No supported preamble after TOC')
        return result
    if body_start:
        result.coverage.append(dict(start=0, end=body_start, classification='front_matter_toc'))
    # Standalone page numbers are annotations, never destructive cleanup. A
    # number could be substantive, so retain it and expose the candidate status.
    for m in re.finditer(r'(?m)^[ \t]*(?:-[ \t]*)?\d{1,4}(?:[ \t]*-)?[ \t]*\r?$',text):
        if m.start()>=body_start:
            issue('page_artifact_candidate',m.start(),m.end(),
                  'Standalone page-number candidate retained in source and evidence')
    # Explicit Section headings may be flattened, but require a boundary and a capitalized title.
    candidates = []
    pattern = r'(?i:\bsection)[ \t]+(\d{1,2}\.\d{1,2})\.?[ \t\n]+|(?m:^[ \t]*(\d{1,2}\.\d{1,2})\.[ \t]+)'
    for m in re.finditer(pattern, text):
        if m.start() < body_start:
            continue
        pos = m.end()
        if pos >= len(text) or not text[pos].isupper():
            continue
        before = text[max(body_start, m.start()-3):m.start()]
        if m.group(1) and m.start() > body_start and not ('\n' in before or re.search(r'\s{2,}$', before)):
            continue
        title = re.split(r'\.\s|\n', text[pos:pos+180], maxsplit=1)[0]
        if len(title) < 2:
            continue
        candidates.append((m.start(), m.group(1) or m.group(2), title))
    # A repeated body label is ambiguous, not a license to overwrite a registry entry.
    seen, headings = {}, []
    for start, label, title in candidates:
        if label in seen:
            issue('duplicate_section_candidate', start, min(start+len(title), len(text)), label)
            continue
        seen[label] = start
        headings.append((start, label, title))
    if not headings:
        issue('no_sections', body_start, len(text), 'No substantive section boundaries found')
        return result
    if headings[0][0] > body_start:
        span(body_start, headings[0][0], 'clause', 'preamble', role='preamble')
    sections = {}
    clauses = []
    for index, (start, label, _) in enumerate(headings):
        end = headings[index+1][0] if index+1 < len(headings) else len(text)
        section = span(start, end, 'section', label)
        sections[label] = section
        markers = list(re.finditer(r'\s+\(\s*([a-z]|[ivx]{2,5})\s*\)[ \t]+', text[start:end]))
        positions = [(start, label)]
        alpha, roman = None, None
        for marker in markers:
            token = marker.group(1)
            if len(token) == 1 and (token not in 'ivx' or alpha is None or token == chr(ord(alpha)+1)):
                alpha, roman = token, None
                child_label = f'{label}({token})'
            elif alpha:
                roman = token
                child_label = f'{label}({alpha})({roman})'
            else:
                child_label = f'{label}({token})'
            positions.append((start+marker.start(), child_label))
        clause_parents={}
        for j, (cs, cl) in enumerate(positions):
            depth=cl.count('(')
            ce=end
            for ns,nl in positions[j+1:]:
                if depth==0 or nl.count('(')<=depth:
                    ce=ns
                    break
            if ce > cs:
                parent_label=cl.rsplit('(',1)[0] if depth>1 else None
                parent_id=clause_parents.get(parent_label,section['id'])
                item=span(cs, ce, 'clause', cl, parent_id)
                clauses.append(item)
                clause_parents[cl]=item['id']
        result.coverage.append(dict(start=start, end=end, classification='body_section'))
    if headings[0][0] > body_start:
        result.coverage.insert(1 if body_start else 0, dict(start=body_start, end=headings[0][0], classification='preamble'))

    base_clauses = sorted([s for s in result.spans if s['kind']=='clause'], key=lambda s:s['start'])
    starts = [s['start'] for s in base_clauses]

    def owner(position):
        n = bisect.bisect_right(starts, position)-1
        while n>=0:
            if position < base_clauses[n]['end']:return base_clauses[n]
            n-=1
        return None

    # Definitions can contain lettered lists and multiple paragraphs. Their evidence
    # context ends at the next definition or section boundary, never at an internal
    # list marker. This is a conservative source context, not a legal sentence cut.
    definition_pattern = r'[“"]([^”"\n]{1,100})[”"]\s+(means\b|shall mean\b|has the meaning\b|shall have the meaning\b)'
    definition_matches=list(re.finditer(definition_pattern, text[body_start:], re.I))
    section_list=sorted(sections.values(),key=lambda s:s['start'])
    for number,m in enumerate(definition_matches):
        start = body_start+m.start()
        parent = next((s for s in section_list if s['start']<=start<s['end']),None) or owner(start)
        if not parent:
            continue
        next_definition=body_start+definition_matches[number+1].start() if number+1<len(definition_matches) else len(text)
        end=min(parent['end'],next_definition)
        while end>start and text[end-1].isspace():end-=1
        defining = span(start, end, 'clause', parent['label']+':definition:'+m.group(1), parent['id'], 'definition')
        location_only = 'meaning' in m.group(2).lower()
        result.terms.append(dict(id=identity(version_id, 'term', m.group(1), defining['id']),
                                 name=m.group(1), scope='agreement', span_id=defining['id'],
                                 location_only=location_only, start=start, end=end))
        if location_only:
            issue('definition_location_only', start, end, m.group(1))
    for m in re.finditer(r'\((?:the\s+)?[“"](Parent|Company|Sub|Purchaser|Merger Sub|Agreement|End Date)[”"]\)',text[body_start:]):
        start,end=body_start+m.start(),body_start+m.end()
        parent=owner(start)
        if parent:
            result.terms.append(dict(id=identity(version_id,PARSER_VERSION,'alias',start,end),name=m.group(1),
                scope='agreement',span_id=parent['id'],location_only=False,start=parent['start'],end=parent['end']))
    names = {}
    for term in result.terms:
        names.setdefault(term['name'], []).append(term)
    occupied=[]
    for name, terms in sorted(names.items(), key=lambda x:-len(x[0])):
        if len(terms)!=1 or terms[0]['location_only']:
            continue
        term = terms[0]
        for m in re.finditer(r'(?<!\w)'+re.escape(name)+r'(?!\w)', text[body_start:]):
            start, end = body_start+m.start(), body_start+m.end()
            if term['start'] <= start < term['end']:
                continue
            if any(start<b and end>a for a,b in occupied):continue
            parent = owner(start)
            if parent:
                result.uses.append(dict(id=identity(version_id,PARSER_VERSION, 'use', start,end), source_id=parent['id'],
                                        term_id=term['id'], start=start, end=end))
                occupied.append((start,end))

    registry = {}
    for s in result.spans:
        registry.setdefault(s['label'], []).append(s)
    reference_pattern = r'\bSections?\s+\d{1,2}\.\d{1,2}(?:\([a-zivx0-9]+\))*(?:(?:\s*(?:,|and|or|through|to|[-–])\s*)\d{1,2}\.\d{1,2}(?:\([a-zivx0-9]+\))*)*|\b(?:Schedule|Exhibit|Annex)\s+[A-Z0-9]+\b'
    for m in re.finditer(reference_pattern, text[body_start:], re.I):
        start,end=body_start+m.start(),body_start+m.end()
        parent=owner(start)
        if not parent:
            continue
        literal=m.group()
        # Heading labels are not reference mentions.
        if any(abs(start-s['start'])<12 for s in sections.values()):
            continue
        keys=re.findall(r'\d{1,2}\.\d{1,2}(?:\([a-zivx0-9]+\))*',literal)
        if not keys:
            keys=[literal]
        is_range=bool(re.search(r'\b(?:through|to)\b|[-–]',literal))
        external=bool(re.match(r'\s+of\s+(?:the\s+)?(?:Original|another|a different)',text[end:end+50],re.I))
        for key in keys:
            targets=registry.get(key,[])
            target=next((s for s in targets if s['kind']=='section'),None) if '(' not in key else (targets[0] if len(targets)==1 else None)
            status='verified' if target else 'missing'
            if is_range: status='ambiguous'
            if external: status='out_of_corpus'
            if status=='missing' and '(' in key and target is None and key.split('(')[0] in sections: status='partial'
            result.references.append(dict(id=identity(version_id,PARSER_VERSION,'ref',start,end,key), source_id=parent['id'],
                target_id=target['id'] if status=='verified' else None, literal=literal, target_label=key,
                start=start,end=end,status=status))
    # Lossless coverage includes page artifacts within body spans; they are not deleted.
    for s in result.spans:
        assert 0 <= s['start'] < s['end'] <= len(text)
        assert digest(text[s['start']:s['end']].encode('utf-8')) == s['text_hash']
    return result
