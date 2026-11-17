"""Opt-in HTTP smoke checks against the real active index and hosted models.

Run inside the configured API container. Credentials are never printed. This is
an integration smoke check, not an evaluation dataset or quality benchmark.
"""
import argparse
import json
import time
import uuid
from pathlib import Path

import httpx
from accordtrace_backend.common.core import Settings


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--url',default='http://localhost:8000')
    parser.add_argument('--max-wait',type=int,default=600)
    args=parser.parse_args();settings=Settings();report={'verification':'live_http_real_index_hosted_models','checks':{}}
    target=settings.runtime/'live-backend-smoke.json'
    def save():target.write_text(json.dumps(report,indent=2,ensure_ascii=False),encoding='utf-8')
    def record(name,value):
        report['checks'][name]=value;save();print(json.dumps({'check':name,**value}),flush=True)
    with httpx.Client(base_url=args.url,headers={'Authorization':'Bearer '+settings.api_token.get_secret_value()},timeout=20) as client:
        def request(method,path,**kwargs):
            response=client.request(method,path,**kwargs)
            if response.is_error:raise RuntimeError('http_'+str(response.status_code)+'_'+path.split('?')[0])
            return response
        def wait(run_id):
            deadline=time.monotonic()+args.max_wait;previous=None
            while time.monotonic()<deadline:
                value=request('GET','/api/v1/runs/'+run_id).json()
                progress=(value['status'],value['stage'])
                if progress!=previous:
                    print(json.dumps({'run_id':run_id,'status':progress[0],'stage':progress[1]}),flush=True);previous=progress
                if value['status'] in ('completed','failed','cancelled'):return value
                time.sleep(1)
            raise RuntimeError('smoke_run_wait_exceeded')
        def turn(conversation_id,content,**extra):
            key=str(uuid.uuid4());body=dict(content=content,**extra);path='/api/v1/conversations/'+conversation_id+'/messages'
            response=request('POST',path,json=body,headers={'Idempotency-Key':key});assert response.status_code==202
            run_id=response.json()['id']
            assert request('POST',path,json=body,headers={'Idempotency-Key':key}).json()['id']==run_id
            value=wait(run_id)
            trace=request('GET','/api/v1/runs/'+run_id+'/trace').json()
            return value,trace
        try:
            ready=request('GET','/health/ready').json()
            assert ready['chat_ready'] and ready['search_ready']
            report['snapshot_id']=ready['active_snapshot_id'];record('readiness',{'status':'passed','snapshot_id':ready['active_snapshot_id']})
            conversation=request('POST','/api/v1/conversations',json={'agreement_ids':['contract_0']}).json()
            cid=conversation['id'];report['conversation_id']=cid
            exact,trace=turn(cid,'Show the Per Share Cash Amount definition.',mode='extractive',defined_term='Per Share Cash Amount')
            assert exact['status']=='completed' and exact['result']['claims'] and not trace['models']
            assert '$115.00' in exact['result']['answer']
            record('exact_zero_generation',{'status':'passed','run_id':exact['id'],'answer_status':exact['answer_status']})
            direct,trace=turn(cid,'What is the Per Share Cash Amount in contract_0? State only the amount and cite its definition.')
            report['direct_run_id']=direct['id'];save()
            assert direct['status']=='completed' and direct['answer_status'] in ('supported','partially_supported')
            assert '115' in direct['result']['answer'] and direct['result']['claims']
            assert {m['provider'] for m in trace['models'] if m['status']=='succeeded'}=={'groq'}
            assert {'plan','draft','review:initial'} <= {m['step'] for m in trace['models'] if m['status']=='succeeded'}
            for claim in direct['result']['claims']:
                for citation in claim['citations']:
                    span=request('GET','/api/v1/evidence/'+citation['span_id']).json()
                    relative=citation['start']-span['start']
                    assert span['quote'][relative:relative+len(citation['quote'])]==citation['quote']
                    assert citation['source_hash']==span['source_hash']
            record('live_answer_and_canonical_citations',{'status':'passed','run_id':direct['id'],'answer_status':direct['answer_status'],
                'model_attempts':len(trace['models']),'claims':len(direct['result']['claims'])})
            follow,follow_trace=turn(cid,'Which agreement and definition was that amount taken from? Cite the source again.')
            report['followup_run_id']=follow['id'];save()
            assert follow['status']=='completed' and follow['answer_status'] in ('supported','partially_supported') and follow['result']['claims']
            assert follow['result']['scope']==direct['result']['scope']
            record('live_followup_pinned_scope',{'status':'passed','run_id':follow['id'],'answer_status':follow['answer_status']})
            events=request('GET','/api/v1/runs/'+direct['id']+'/events').text
            ids=[int(line[4:]) for line in events.splitlines() if line.startswith('id: ')]
            assert ids==sorted(set(ids)) and events.count('event: final\n')==1
            assert request('GET','/api/v1/runs/'+direct['id']+'/events',headers={'Last-Event-ID':str(ids[-1])}).text==''
            exported=request('GET','/api/v1/runs/'+direct['id']+'/export?format=json').json()
            assert exported['run_id']==direct['id']
            assert request('GET','/api/v1/runs/'+direct['id']+'/export?format=markdown').status_code==200
            record('sse_replay_and_exports',{'status':'passed','events':len(ids)})
            key=str(uuid.uuid4());pending=request('POST','/api/v1/conversations/'+cid+'/messages',json={'content':'Show the cash definition again.'},headers={'Idempotency-Key':key}).json()
            request('POST','/api/v1/runs/'+pending['id']+'/cancel')
            cancelled=wait(pending['id']);assert cancelled['status']=='cancelled'
            record('cancellation',{'status':'passed','run_id':pending['id']})
            report['status']='passed';save();print(json.dumps({'status':'passed','report':str(target)}),flush=True)
            return 0
        except Exception as exc:
            report['status']='failed';report['failure']={'type':type(exc).__name__,'code':str(exc) if isinstance(exc,RuntimeError) else 'smoke_assertion_or_transport_failure'}
            save();print(json.dumps({'status':'failed','failure':report['failure'],'report':str(target)}),flush=True)
            return 1


if __name__=='__main__':raise SystemExit(main())
