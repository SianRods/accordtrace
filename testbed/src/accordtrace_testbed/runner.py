"""Capture once, score offline. Never change application prompts or indexes."""
from datetime import datetime,timezone
import json
from pathlib import Path
import uuid
from .client import Backend
from .dataset import fingerprint,sha,validate_sources
from .metrics import retrieval,integrity


def write(path,value):
    path.parent.mkdir(parents=True,exist_ok=True)
    tmp=path.with_suffix('.tmp')
    tmp.write_text(json.dumps(value,indent=2,ensure_ascii=False,allow_nan=False),encoding='utf-8')
    tmp.replace(path)


def collect(settings,cases,directory,mode='retrieval',search_mode='auto',allow_live_chat=False):
    if mode=='chat' and not allow_live_chat:raise ValueError('Chat generation requires --allow-live-chat')
    validate_sources(cases,settings.sources)
    if any(c.review_status=='draft' for c in cases):raise ValueError('Draft references cannot be scored')
    directory=Path(directory);directory.mkdir(parents=True,exist_ok=True)
    backend=Backend(settings)
    try:
        health=backend.request('GET','/health/ready')
        snapshot=health.get('active_snapshot_id')
        if not snapshot or not health.get('search_ready'):raise RuntimeError('backend_search_not_ready')
        snap=backend.request('GET','/api/v1/snapshots/'+snapshot)
        profile=snap['configuration']['embedding']
        if profile.get('provider')!='ollama' or profile.get('verification')!='live':
            raise ValueError('Only a real Ollama snapshot can be evaluated')
        available={(d['agreement_id'],d['raw_hash']):d['id'] for d in backend.documents()}
        membership={p[0] for p in snap['configuration'].get('source_membership',[])}
        for c in cases:
            for e in c.reference_contexts:
                if available.get((e.agreement_id,e.source_hash)) not in membership:
                    raise ValueError('Gold source version not in selected backend snapshot')
        identity={'dataset':fingerprint([c.model_dump() for c in cases]),'snapshot_id':snapshot,
            'case_ids':[c.id for c in cases],
            'mode':mode,'search_mode':search_mode,'backend_url':settings.backend_url,'embedding_profile':profile,
            'testbed_version':'0.1.0'}
        manifest=directory/'manifest.json'
        if manifest.exists():
            if json.loads(manifest.read_text(encoding='utf-8'))['identity']!=identity:
                raise ValueError('Run directory belongs to a different dataset/snapshot/configuration')
        else:write(manifest,{'identity':identity,'created_at':datetime.now(timezone.utc).isoformat(),
            'note':'Retrieval captures are independent searches, NOT the context used by later chat generation.'})
        for case in cases:
            path=directory/'observations'/(case.id+'.json')
            row=json.loads(path.read_text(encoding='utf-8')) if path.exists() else {'case':case.model_dump(),'idempotency_key':str(uuid.uuid4())}
            if row.get('complete'):continue
            def save():write(path,row)
            save()
            try:
                if 'search_packet' not in row:
                    row['search_packet']=backend.search(case,snapshot,search_mode);save()
                row['retrieval_metrics']=retrieval(case,row['search_packet'])
                if mode in ('chat','extractive'):
                    state=row.setdefault('chat',{})
                    backend.submit(case,snapshot,row['idempotency_key'],state,save,extractive=mode=='extractive')
                    result=state['run'].get('result') or {}
                    if result:
                        scope=result.get('scope',{})
                        row['chat_scope_valid']=(scope.get('snapshot_id')==snapshot
                            and set(scope.get('agreement_ids',[]))==set(case.agreement_ids)
                            and set(scope.get('document_version_ids',[]))==set(state['document_version_ids']))
                    row['citation_checks']=backend.citation_checks(result)
                    row['citation_metrics']=integrity(row['citation_checks'])
                row.pop('error_type',None);row.pop('error_code',None);row['complete']=True
            except Exception as exc:
                row['error_type']=type(exc).__name__
                row['error_code']=str(exc) if isinstance(exc,(ValueError,RuntimeError)) else 'transport_or_validation_error'
            save();print(json.dumps({'case_id':case.id,'complete':row.get('complete',False),'error_type':row.get('error_type')}),flush=True)
        return directory
    finally:backend.close()
