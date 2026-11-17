"""Explicit free-profile generation adapters and durable attempt accounting."""
import json
import math
import time
import uuid
from datetime import timedelta
from email.utils import parsedate_to_datetime

from pydantic import ValidationError
from sqlalchemy import func,select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from accordtrace_backend.indexing.chunking import fingerprint
from accordtrace_backend.common.core import DomainError
from accordtrace_backend.persistence.storage import utcnow
from accordtrace_backend.chat.models import ModelCall, ProviderWindow, Run
from accordtrace_backend.chat.prompts import MODELS, PROMPTS, configuration
from accordtrace_backend.chat.repository import Halt, QuotaWait, event, get_artifact, owned, put_artifact, reserve
from accordtrace_backend.chat.schemas import provider_schema


class ProviderFailure(Exception):
    def __init__(self,code,retryable=False,quota=False,retry_after=60):
        self.code,self.retryable,self.quota,self.retry_after=code,retryable,quota,retry_after


def configured(settings):
    return bool(settings.gemini_api_key.get_secret_value() and settings.gemini_free_tier_confirmed
                and settings.groq_api_key.get_secret_value() and settings.groq_free_tier_confirmed)


def retry_delay(headers):
    value=headers.get('retry-after','60') if headers else '60'
    try:return max(1,float(value))
    except (ValueError,TypeError):
        try:return max(1,(parsedate_to_datetime(value)-utcnow()).total_seconds())
        except Exception:return 60


class LiveProviders:
    scripted=False
    def __init__(self,settings):self.settings=settings

    def generate(self,role,payload,schema,timeout,max_output):
        provider,model=MODELS[role]
        if not configured(self.settings):raise ProviderFailure('chat_credentials_not_configured')
        try:
            if provider=='groq':
                from groq import Groq
                with Groq(api_key=self.settings.groq_api_key.get_secret_value(),timeout=timeout,max_retries=0) as client:
                    response=client.chat.completions.create(model=model,stream=False,
                        messages=[{'role':'system','content':PROMPTS[role]},
                                  {'role':'user','content':json.dumps(payload,ensure_ascii=False)}],
                        reasoning_effort='low',include_reasoning=False,max_completion_tokens=max_output,
                        response_format={'type':'json_schema','json_schema':dict(name=role,strict=True,schema=schema)})
                if not response.choices or not response.choices[0].message.content:raise ProviderFailure('empty_model_response')
                usage=response.usage.model_dump() if response.usage else {}
                return json.loads(response.choices[0].message.content),usage
            from google import genai
            from google.genai import types
            with genai.Client(api_key=self.settings.gemini_api_key.get_secret_value(),
                http_options=types.HttpOptions(timeout=int(timeout*1000),retry_options=types.HttpRetryOptions(attempts=1))) as client:
                response=client.models.generate_content(model=model,contents=json.dumps(payload,ensure_ascii=False),
                    config=types.GenerateContentConfig(system_instruction=PROMPTS[role],response_mime_type='application/json',
                        response_json_schema=schema,max_output_tokens=max_output,
                        thinking_config=types.ThinkingConfig(thinking_level='minimal',include_thoughts=False)))
            if not response.text:raise ProviderFailure('empty_model_response')
            usage=response.usage_metadata.model_dump() if response.usage_metadata else {}
            return json.loads(response.text),usage
        except (json.JSONDecodeError,ProviderFailure):raise
        except Exception as exc:
            response=getattr(exc,'response',None)
            code=getattr(exc,'status_code',None) or getattr(exc,'code',None)
            if code==429:raise ProviderFailure('provider_quota',quota=True,retry_after=retry_delay(getattr(response,'headers',{}))) from None
            if code in (401,403,404):raise ProviderFailure('provider_access_denied') from None
            raise ProviderFailure('provider_unavailable',retryable=code in (None,500,502,503,504)) from None


def _limits(settings,provider):
    if provider=='groq':return settings.chat_groq_rpm,settings.chat_groq_tpm,settings.chat_groq_rpd,settings.chat_groq_tpd
    return settings.chat_gemini_rpm,settings.chat_gemini_tpm,settings.chat_gemini_rpd,1000000


def request_estimate(role,payload,schema,max_output):
    # Keep the SDK serialization and conservative reservation consistent; allow repair hints.
    return math.ceil(len((PROMPTS[role]+json.dumps(payload,ensure_ascii=False)+json.dumps(schema)).encode())/3)+max_output+64


def fit_payload(role,payload,schema,max_output,tpm):
    """Send source text and labels, not the entire persistence/provenance envelope.

    Canonical metadata stays server-side. Never omit a draft's cited source from
    reviewer input; refuse the request if those sources alone exceed the budget.
    """
    value=dict(payload);omitted=[];history_trimmed=0
    if 'evidence' in value:
        value['evidence']=[{k:item[k] for k in ('evidence_id','agreement_id','section_label','quote') if k in item}
            for item in value['evidence']]
    if 'retrieval' in value:
        packets=value['retrieval'].get('items',[]);gaps=[];warnings=[]
        for packet in packets:
            for gap in packet.get('gaps',[]):
                item={k:gap[k] for k in ('literal','status') if k in gap}
                if item not in gaps:gaps.append(item)
            for warning in packet.get('warnings',[]):
                item=warning if isinstance(warning,str) else warning.get('code','unknown')
                if item not in warnings:warnings.append(item)
        value['retrieval']=dict(gaps=gaps[:12],additional_gaps=max(0,len(gaps)-12),warnings=warnings[:12])
    if 'history' in value:
        value['history']=[dict(question=turn.get('question','')[:300],
            claims=[dict(text=c['text'][:250],citations=c.get('citations',[])[:2]) for c in turn.get('claims',[])[:2]]) for turn in value['history']]
    protected={c['evidence_id'] for claim in value.get('draft',{}).get('claims',[]) for c in claim.get('citations',[])}
    protected&={item['evidence_id'] for item in value.get('evidence',[])}
    while request_estimate(role,value,schema,max_output)>tpm:
        history=value.get('history',[])
        if history:
            history.pop(0);history_trimmed+=1
            value['context_limitations']={'omitted_evidence_ids':omitted,'omitted_history_turns':history_trimmed}
            continue
        evidence=value.get('evidence',[])
        index=next((i for i in range(len(evidence)-1,-1,-1) if evidence[i]['evidence_id'] not in protected),None)
        if index is None:raise Halt('provider_context_budget')
        omitted.append(evidence.pop(index)['evidence_id'])
        value['context_limitations']={'omitted_evidence_ids':omitted,'omitted_history_turns':history_trimmed}
    if history_trimmed or omitted:value['context_limitations']={'omitted_evidence_ids':omitted,'omitted_history_turns':history_trimmed}
    if request_estimate(role,value,schema,max_output)>tpm:raise Halt('provider_context_budget')
    return value,dict(omitted_evidence_ids=omitted,omitted_history_turns=history_trimmed)


def quota_reserve(db,settings,provider,model,estimate):
    now=utcnow();key=provider+':'+model
    db.execute(insert(ProviderWindow).values(id=key,minute_start=now,day_start=now.replace(hour=0,minute=0,second=0,microsecond=0),
        requests=0,tokens=0,day_requests=0,day_tokens=0,failures=0).on_conflict_do_nothing())
    window=db.get(ProviderWindow,key,with_for_update=True)
    rpm,tpm,rpd,tpd=_limits(settings,provider)
    if estimate>tpm:raise Halt('provider_context_budget')
    if window.blocked_until and window.blocked_until>now:raise QuotaWait(window.blocked_until)
    if now-window.minute_start>=timedelta(seconds=60):
        window.minute_start=now;window.requests=0;window.tokens=0
    if now.date()!=window.day_start.date():
        window.day_start=now.replace(hour=0,minute=0,second=0,microsecond=0);window.day_requests=0;window.day_tokens=0
    if window.day_requests>=rpd or window.day_tokens+estimate>tpd:raise QuotaWait(window.day_start+timedelta(days=1))
    if window.requests>=rpm or window.tokens+estimate>tpm:raise QuotaWait(window.minute_start+timedelta(seconds=61))
    window.requests+=1;window.tokens+=estimate;window.day_requests+=1;window.day_tokens+=estimate


def quota_result(db,provider,model,estimate,usage,failure=None):
    window=db.get(ProviderWindow,provider+':'+model,with_for_update=True)
    if not window:return
    if failure:
        if failure.quota:window.blocked_until=utcnow()+timedelta(seconds=failure.retry_after)
        elif failure.retryable:
            window.failures+=1
            if window.failures>=3:window.blocked_until=utcnow()+timedelta(seconds=60)
        return
    window.failures=0
    actual=usage.get('total_tokens') or usage.get('total_token_count')
    if isinstance(actual,int):
        window.tokens=max(0,window.tokens+actual-estimate);window.day_tokens=max(0,window.day_tokens+actual-estimate)


class Gateway:
    def __init__(self,settings,db_engine,run_id,token,providers):
        self.settings,self.engine,self.run_id,self.token,self.providers=settings,db_engine,run_id,token,providers

    def generate(self,role,step,payload,model_type):
        schema=provider_schema(model_type)
        provider,model=MODELS[role]
        max_output=2048 if role=='draft' else 1536
        payload,limited=fit_payload(role,payload,schema,max_output,_limits(self.settings,provider)[1])
        input_hash=fingerprint(dict(role=role,payload=payload,schema=schema,prompt=PROMPTS[role]))
        cached=get_artifact(self.engine,self.run_id,'model:'+step,input_hash)
        if cached is not None:return model_type.model_validate(cached)
        repair=False;network_retry=False
        for _ in range(3):
            with Session(self.engine) as db,db.begin():
                run=owned(db,self.run_id,self.token)
                if run.configuration!=configuration(self.settings,self.providers.scripted):raise Halt('workflow_version_unavailable')
                attempt=(db.scalar(select(func.max(ModelCall.attempt)).where(ModelCall.run_id==run.id,ModelCall.step==step)) or 0)+1
                if attempt>3:raise Halt('model_step_attempt_limit')
                timeout=min(self.settings.chat_provider_timeout_seconds,run.configuration['active_seconds']-run.active_seconds)
                if timeout<=0:raise Halt('active_budget_exceeded')
                estimate=request_estimate(role,payload,schema,max_output)
                if limited['omitted_evidence_ids'] or limited['omitted_history_turns']:
                    event(db,run,'context:'+step,'context_limited',dict(step=step,**limited))
                    from .models import Artifact
                    db.merge(Artifact(run_id=run.id,key='context:'+step,input_hash=input_hash,value=limited))
                if not self.providers.scripted:quota_reserve(db,self.settings,provider,model,estimate)
                reserve(run,'generation')
                call=ModelCall(id=str(uuid.uuid4()),run_id=run.id,step=step,attempt=attempt,provider=provider,model=model,
                    input_hash=input_hash,status='in_flight',timeout=timeout)
                db.add(call);event(db,run,'model:'+step+':'+str(attempt),'provider_started',{'role':role,'model':model,'attempt':attempt})
                call_id=call.id
            failure=None;usage={};parsed=None
            try:
                value,usage=self.providers.generate(role,{**payload,**({'schema_repair':'Return valid JSON for the exact schema.'} if repair else {})},schema,timeout,max_output)
                parsed=model_type.model_validate(value)
            except (ValidationError,json.JSONDecodeError):failure=ProviderFailure('structured_output_invalid')
            except ProviderFailure as exc:failure=exc
            except Exception:failure=ProviderFailure('provider_unavailable',retryable=True)
            with Session(self.engine) as db,db.begin():
                run=owned(db,self.run_id,self.token)
                call=db.get(ModelCall,call_id);call.status='failed' if failure else 'succeeded'
                call.error_code=failure.code if failure else None;call.usage=usage
                if not self.providers.scripted:quota_result(db,provider,model,estimate,usage,failure)
            if failure is None:
                put_artifact(self.engine,self.run_id,self.token,'model:'+step,input_hash,parsed.model_dump())
                return parsed
            if failure.quota:raise QuotaWait(utcnow()+timedelta(seconds=failure.retry_after))
            if failure.code=='structured_output_invalid' and not repair:
                with Session(self.engine) as db,db.begin():reserve(owned(db,self.run_id,self.token),'schema_repair')
                repair=True;continue
            if failure.retryable and not network_retry:
                network_retry=True;continue
            raise Halt(failure.code)
        raise Halt('model_step_attempt_limit')
