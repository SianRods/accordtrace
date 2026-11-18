"""HTTP-only boundary. No DB connection, backend imports or admin routes."""
import time
import httpx


class Backend:
    def __init__(self, settings):
        if not settings.backend_api_token.get_secret_value():
            raise ValueError('TESTBED_BACKEND_API_TOKEN is required')
        self.settings = settings
        self.client = httpx.Client(base_url=settings.backend_url,
            headers={'Authorization':'Bearer '+settings.backend_api_token.get_secret_value()},
            timeout=settings.request_timeout_seconds,trust_env=False)

    def request(self, method, path, **kwargs):
        if '/admin/' in path or method not in ('GET','POST'):
            raise ValueError('Administrative mutation is forbidden')
        r = self.client.request(method,path,**kwargs)
        if r.is_error:
            # Never include request headers, credentials, URLs or provider bodies.
            known={'ambiguous_term_selector','ambiguous_section_selector','embedding_mode_mismatch',
                'agreement_outside_snapshot_scope','chat_credentials_not_configured'}
            try:code=r.json().get('error',{}).get('code')
            except (ValueError,AttributeError):code=None
            raise RuntimeError('backend_http_'+str(r.status_code)+('_'+code if code in known else ''))
        return r.json()

    def documents(self):
        result=[]
        for offset in range(0,10000,100):
            page=self.request('GET',f'/api/v1/documents?offset={offset}&limit=100')['items']
            result.extend(page)
            if len(page)<100:return result
        raise RuntimeError('document_pagination_limit')

    def search(self, case, snapshot, mode):
        body={'query':case.question,'agreement_ids':case.agreement_ids,'snapshot_id':snapshot,'mode':mode,'limit':6}
        if mode=='exact':
            if not case.defined_term:raise ValueError('Exact pilot requires defined_term')
            body['defined_term']=case.defined_term
        return self.request('POST','/api/v1/search',json=body)

    def submit(self, case, snapshot, key, state, save, extractive=False):
        if not state.get('conversation_id'):
            c=self.request('POST','/api/v1/conversations',json={'agreement_ids':case.agreement_ids,'snapshot_id':snapshot})
            if c['snapshot_id']!=snapshot or set(c['agreement_ids'])!=set(case.agreement_ids):
                raise ValueError('Conversation scope mismatch')
            state['conversation_id']=c['id'];state['document_version_ids']=c['document_version_ids'];save()
        if not state.get('run_id'):
            body={'content':case.question,'mode':'extractive' if extractive else 'auto'}
            if extractive:
                if not case.defined_term:raise ValueError('Extractive pilot requires defined_term')
                body['defined_term']=case.defined_term
            r=self.request('POST',f"/api/v1/conversations/{state['conversation_id']}/messages",
                json=body,headers={'Idempotency-Key':key})
            state['run_id']=r['id'];save()
        deadline=time.monotonic()+self.settings.chat_wait_seconds
        while time.monotonic()<deadline:
            run=self.request('GET','/api/v1/runs/'+state['run_id'])
            state['run']=run;save()
            if run['status'] in ('completed','failed','cancelled'):
                state['trace']=self.request('GET','/api/v1/runs/'+state['run_id']+'/trace');save()
                return
            time.sleep(1)
        raise RuntimeError('chat_wait_expired_run_not_cancelled_resume_same_directory')

    def citation_checks(self, result):
        checks=[]
        for claim in result.get('claims',[]):
            for c in claim.get('citations',[]):
                try:
                    p=self.request('GET','/api/v1/evidence/'+c['span_id'])
                    a,b=c['start']-p['start'],c['end']-p['start']
                    valid=(p['document_version_id']==c['document_version_id'] and p['source_hash']==c['source_hash']
                        and p['agreement_id']==c['agreement_id']
                        and c['agreement_id'] in result['scope']['agreement_ids']
                        and c['document_version_id'] in result['scope']['document_version_ids']
                        and p['offset_unit']==c['offset_unit']=='unicode_codepoints'
                        and 0<=a<b<=len(p['quote']) and p['quote'][a:b]==c['quote'])
                    from .dataset import sha
                    valid=valid and sha(c['quote'].encode())==c['text_hash'] and sha(p['quote'].encode())==p['text_hash']
                    checks.append({'valid':bool(valid),'claim_id':claim['id'],'evidence_id':c['evidence_id']})
                except Exception as exc:
                    checks.append({'valid':False,'error_type':type(exc).__name__})
        return checks

    def close(self):self.client.close()
