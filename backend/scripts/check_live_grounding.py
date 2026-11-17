"""Opt-in live generation against canonical source; no embedding/search calls.

This checks provider grounding/citation mechanics while a full search index may
be unavailable. It does not claim full LangGraph/CRAG or hybrid HTTP verification.
"""
import json
from sqlalchemy import select
from sqlalchemy.orm import Session
from accordtrace_backend.common.core import Settings,digest
from accordtrace_backend.persistence.storage import engine,Document,LogicalDocument,Term,Span,Membership
from accordtrace_backend.chat.providers import LiveProviders,quota_reserve,quota_result,request_estimate
from accordtrace_backend.chat.prompts import MODELS
from accordtrace_backend.chat.schemas import Draft,Review,provider_schema


def main():
    settings=Settings();db_engine=engine(settings)
    with Session(db_engine) as db:
        row=db.execute(select(Term,Span,Document).join(Span,Span.id==Term.span_id).join(Document,Document.id==Term.document_id)
            .join(LogicalDocument,LogicalDocument.id==Document.logical_document_id)
            .join(Membership,Membership.document_id==Document.id)
            .where(Membership.snapshot_id=='e55726d9-febe-56ee-9a33-795102a7dbff',Document.agreement_id=='contract_0',
                Term.name=='Per Share Cash Amount',Span.parser_version=='structural-v3',LogicalDocument.approved.is_(True))).one()
        term,span,document=row;quote=document.text[span.start:span.end]
        assert '$115.00' in quote and digest(quote.encode())==span.text_hash
        source=dict(document_version_id=document.id,source_hash=document.raw_hash,text_hash=span.text_hash,start=span.start,end=span.end)
    evidence=[dict(evidence_id='E1',agreement_id='contract_0',section_label=span.label,quote=quote)]
    payload=dict(question='What is the Per Share Cash Amount? State the amount with an exact source quotation.',evidence=evidence)
    provider=LiveProviders(settings);results={}
    for role,model_type in [('draft',Draft),('reviewer',Review)]:
        schema=provider_schema(model_type);name,model=MODELS[role]
        estimate=request_estimate(role,payload,schema,1536)
        with Session(db_engine) as db,db.begin():quota_reserve(db,settings,name,model,estimate)
        value,usage=provider.generate(role,payload,schema,15,1536);parsed=model_type.model_validate(value)
        with Session(db_engine) as db,db.begin():quota_result(db,name,model,estimate,usage)
        if role=='draft':
            assert parsed.claims and '115' in ' '.join(c.text for c in parsed.claims)
            for claim in parsed.claims:
                assert claim.citations
                for citation in claim.citations:
                    assert citation.evidence_id=='E1' and (not citation.quote or citation.quote in quote)
            payload['draft']=parsed.model_dump()
        else:
            assert {v.claim_id for v in parsed.verdicts}=={c['id'] for c in payload['draft']['claims']}
            assert all(v.verdict=='supported' for v in parsed.verdicts)
        results[role]=dict(status='passed',provider=name,model=model,usage=usage)
    report=dict(status='passed',verification='live_generation_with_canonical_postgres_evidence; no_search_no_agent_run',
        source=source,roles=results,claims=payload['draft']['claims'])
    (settings.runtime/'live-grounded-generation.json').write_text(json.dumps(report,indent=2,ensure_ascii=False),encoding='utf-8')
    print(json.dumps(dict(status='passed',verification=report['verification'],claims=len(report['claims']))))
    db_engine.dispose()


if __name__=='__main__':main()
