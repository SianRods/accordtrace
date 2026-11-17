"""Opt-in local Ollama embedding compatibility probe; does not change any index."""
import argparse
import json
import math
import time

import httpx
from accordtrace_backend.common.core import Settings,digest


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--url',default='http://127.0.0.1:11434')
    parser.add_argument('--model',default='embeddinggemma:300m')
    args=parser.parse_args();settings=Settings()
    source=settings.source_dir/'contract_0.txt';raw=source.read_text(encoding='utf-8-sig')
    occurrence=raw.find('$115.00')
    if occurrence<0:raise RuntimeError('expected_public_source_example_missing')
    start=max(0,occurrence-120);end=min(len(raw),occurrence+200)
    # Google model-card retrieval prompts; never silently truncate evidence.
    inputs=['title: contract_0 | text: '+raw[start:end],
            'task: search result | query: What is the Per Share Cash Amount?']
    with httpx.Client(base_url=args.url,timeout=120,trust_env=False) as client:
        version=client.get('/api/version');version.raise_for_status()
        tags=client.get('/api/tags');tags.raise_for_status()
        model=next((item for item in tags.json()['models'] if item['name']==args.model),None)
        if model is None:raise RuntimeError('model_not_installed')
        metadata=client.post('/api/show',json={'model':args.model});metadata.raise_for_status()
        started=time.monotonic()
        response=client.post('/api/embed',json={'model':args.model,'input':inputs,'truncate':False,'keep_alive':'5m'})
        response.raise_for_status();payload=response.json();vectors=payload.get('embeddings',[])
        if len(vectors)!=2:raise RuntimeError('embedding_count_mismatch')
        if any(len(vector)!=768 or any(not math.isfinite(value) for value in vector) for vector in vectors):
            raise RuntimeError('invalid_embedding_vector')
        norms=[math.hypot(*vector) for vector in vectors]
        if any(abs(value-1)>0.001 for value in norms):raise RuntimeError('embedding_not_normalized')
        report=dict(status='passed',verification='real_local_ollama_document_query_smoke; no_index_changes',
            ollama_version=version.json()['version'],model=args.model,model_digest=model['digest'],
            model_size_bytes=model.get('size'),model_details=model.get('details'),
            capabilities=metadata.json().get('capabilities',[]),vectors=len(vectors),dimensions=768,
            vector_norms=norms,truncate=False,prompt_format='embeddinggemma-retrieval-v1',
            source=dict(name=source.name,start=start,end=end,text_hash=digest(raw[start:end].encode())),
            elapsed_seconds=round(time.monotonic()-started,3),prompt_eval_count=payload.get('prompt_eval_count'))
    settings.runtime.mkdir(parents=True,exist_ok=True)
    path=settings.runtime/'ollama-embedding-smoke.json';path.write_text(json.dumps(report,indent=2),encoding='utf-8')
    print(json.dumps(report,indent=2))


if __name__=='__main__':main()
