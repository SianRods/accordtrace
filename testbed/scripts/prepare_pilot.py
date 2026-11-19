"""Prepare a narrow, source-aligned diagnostic pilot; no backend parser/LLM imports."""
import argparse
import json
from pathlib import Path
import re
from accordtrace_testbed.config import ROOT,Settings
from accordtrace_testbed.dataset import sha,REVISION

TERMS={
    0:['Business Day','Per Share Cash Amount','Code'],
    1:['Reverse Termination Fee','Antitrust Laws','Code'],
    2:['Business Day','Code','HSR Act'],
    3:['Business Day','Antitrust Laws','Code'],
    4:['Business Day','Closing Date','Code'],
    5:['Business Day','CA 2006','Code'],
}
HASHES=[
    '819ca0442bcd4ceeab7ba5740bf16ff9d404d22577417feebb144779bf4d3693',
    '7b9a5ddfcdfd3698bfbf44d161c2cc83fb072c6032afb2b2fab1e7d279594f96',
    '3a4254dbb88abbd52e553837b4675c2358769b050c6855c4b4e6b78246bb646f',
    'cf16f47babe01f892c2bbacaa1e1791bf7c9c671675ecd1820671ef4cfdf446a',
    '402afc70b95cf93f6e45ef7a3e77ed83be6f744db73dd1ec5596210170577a62',
    '4ca3e80e76ec434a9dfd03603b26ee119d26083db0bc61b5e1f28123d118d1d0',
]


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--overwrite',action='store_true');args=parser.parse_args()
    path=ROOT/'datasets/pilot.jsonl'
    if path.exists() and not args.overwrite:raise SystemExit('Pilot exists; preserve annotations or use explicit --overwrite')
    settings=Settings();rows=[]
    for i,terms in TERMS.items():
        raw=(settings.sources/f'contract_{i}.txt').read_bytes()
        if sha(raw)!=HASHES[i]:raise ValueError('Unexpected source revision/hash')
        text=raw.decode('utf-8-sig')
        definitions=list(re.finditer(r'\u201c([^\u201d]{1,70})\u201d\s+(?:means?|shall mean|has the meaning|is defined)\b',text,re.I))
        for j,term in enumerate(terms):
            hits=[m for m in definitions if m.group(1)==term and re.search(r'\b(?:means|shall mean)$',m.group(),re.I)]
            if len(hits)!=1:raise ValueError(f'Expected one literal definition for contract_{i}: {term}')
            m=hits[0];next_start=min((x.start() for x in definitions if x.start()>m.start()),default=len(text))
            segment=text[m.start():next_start]
            if i==1 and term=='Reverse Termination Fee':
                # Reviewed source boundary: the definition ends before subsection (d).
                segment=segment[:segment.index('   (d)')]
            paragraph=re.split(r'\n\s*\n',segment,maxsplit=1)[0].rstrip()
            # Drop an isolated trailing printed page number, not any source words.
            paragraph=re.sub(r'\s+\d+\s*$','',paragraph)
            if len(paragraph)>2000:raise ValueError('Definition needs manual boundary review')
            rows.append({'id':f'c{i}_definition_{j+1}','question':f'In contract_{i}, how is "{term}" defined? Preserve all qualifications in its definition.',
                'agreement_ids':[f'contract_{i}'],'category':'direct_definition','reference':paragraph,
                'reference_contexts':[{'agreement_id':f'contract_{i}','source_hash':sha(raw),'start':m.start(),
                    'end':m.start()+len(paragraph),'quote':paragraph}],
                'defined_term':term,'expected_behavior':'answer','review_status':'source_verified','source_revision':REVISION,
                'notes':'Literal definition and exact Unicode source alignment verified. No legal expert adjudication; diagnostic pilot, not held-out benchmark.'})
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text('\n'.join(json.dumps(r,ensure_ascii=False) for r in rows)+'\n',encoding='utf-8')
    print(json.dumps({'cases':len(rows),'path':str(path),'review_status':'source_verified_not_expert_reviewed'}))


if __name__=='__main__':main()
