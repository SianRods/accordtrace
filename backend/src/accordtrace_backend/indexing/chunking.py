"""Source-preserving partitioning and tokenizer-sized retrieval children."""
import json
import re

from accordtrace_backend.common.core import DomainError, digest, identity

CHUNKER = dict(version='children-v1', target=320, maximum=360, overlap=40,
               input_bytes=1536, breadcrumb_tokens=32, preprocessing='whitespace-v1')


def fingerprint(value):
    return digest(json.dumps(value,sort_keys=True,separators=(',',':')).encode())


def partitions(spans):
    sections={s.id for s in spans if s.kind=='section'}
    parents=sorted((s for s in spans if s.kind=='clause' and s.role!='definition'
                    and (s.parent_id in sections or s.parent_id is None)),key=lambda s:(s.start,s.end))
    for a,b in zip(parents,parents[1:]):
        if a.end>b.start: raise DomainError('overlapping_chunk_parents')
    return parents


def chunks(document, spans, tokenizer, chunker_hash):
    output=[]
    for parent in partitions(spans):
        breadcrumb=f'{document.agreement_id} | {parent.label}'
        tokens=tokenizer.encode(breadcrumb,add_special_tokens=False)
        if len(tokens.ids)>32:
            breadcrumb=breadcrumb[:tokens.offsets[31][1]]
        prefix=breadcrumb+'\n'
        start=parent.start
        while start<parent.end:
            raw=document.text[start:parent.end]
            encoded=tokenizer.encode(raw,add_special_tokens=False)
            if not encoded.ids:
                # Preserve whitespace in the preceding range or a small standalone chunk.
                end=min(parent.end,start+1024)
            else:
                count=min(CHUNKER['maximum'],len(encoded.ids))
                end=start+encoded.offsets[count-1][1]
                if count>CHUNKER['target']:
                    preferred=start+encoded.offsets[CHUNKER['target']-1][1]
                    boundaries=list(re.finditer(r'[.;]\s+|\n\s*\([a-zivx]+\)',document.text[max(start,preferred-120):end]))
                    if boundaries:
                        end=max(start,preferred-120)+boundaries[-1].end()
                if count==len(encoded.ids):end=parent.end
            # Binary search to avoid O(n^2) shortening on long unknown words/Unicode.
            lo,hi=start+1,end
            while lo<=hi:
                mid=(lo+hi)//2
                value=prefix+re.sub(r'\s+',' ',document.text[start:mid]).strip()
                if len(value.encode())<=CHUNKER['input_bytes']:lo=mid+1
                else:hi=mid-1
            end=min(end,hi)
            if end<=start: raise DomainError('breadcrumb_exceeds_input_limit')
            value=prefix+re.sub(r'\s+',' ',document.text[start:end]).strip()
            text_hash=digest(document.text[start:end].encode())
            output.append(dict(id=identity(document.id,chunker_hash,parent.id,start,end),
                document_id=document.id,parent_id=parent.id,start=start,end=end,text_hash=text_hash,
                chunker_hash=chunker_hash,input_hash=digest(value.encode()),embedding_input=value,
                tokens=len(tokenizer.encode(document.text[start:end],add_special_tokens=False).ids)))
            if end==parent.end:break
            used=tokenizer.encode(document.text[start:end],add_special_tokens=False)
            next_start=start+used.offsets[-40][0] if len(used.ids)>40 else end
            start=next_start if next_start>start else end
    return output
