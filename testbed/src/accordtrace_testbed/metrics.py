"""Deterministic annotated-span measures. They are not semantic judge scores."""


def ratio(n,d):return n/d if d else None


def coverage(gold,items):
    ranges=sorted((max(gold.start,x['start']),min(gold.end,x['end'])) for x in items
        if x['agreement_id']==gold.agreement_id and x['source_hash']==gold.source_hash
        and x['start']<gold.end and x['end']>gold.start)
    total=0;end=gold.start
    for a,b in ranges:
        total+=max(0,b-max(a,end));end=max(end,b)
    return total/(gold.end-gold.start)


def retrieval(case,packet):
    items=[];seen=set()
    for group in packet.get('groups',[]):
        for x in group['evidence']:
            key=(x['document_version_id'],x['start'],x['end'])
            if key not in seen:seen.add(key);items.append(x)
    scope_ok=all(x['agreement_id'] in case.agreement_ids for x in items)
    out={'scope_violation_count':sum(x['agreement_id'] not in case.agreement_ids for x in items),
        'scope_valid':scope_ok,'returned_evidence_count':len(items),'retrieval_mode':packet.get('retrieval_mode')}
    # No invented global ordering across agreement groups.
    if len(case.agreement_ids)>1:
        out['ranking_metrics_unavailable']='multi_agreement_group_order_is_not_global_rank'
    gold=case.reference_contexts
    out['annotated_span_coverage_all']=ratio(sum(coverage(g,items) for g in gold),len(gold))
    for k in (3,6):
        selected=items[:k] if len(case.agreement_ids)==1 else items
        cov=[coverage(g,selected) for g in gold]
        out[f'annotated_span_coverage_at_{k}']=ratio(sum(cov),len(cov)) if len(case.agreement_ids)==1 else None
        out[f'evidence_recall_at_{k}']=ratio(sum(v>=0.95 for v in cov),len(cov)) if len(case.agreement_ids)==1 else None
    out['required_evidence_complete']=all(coverage(g,items)>=0.95 for g in gold) if gold else None
    if len(case.agreement_ids)==1:
        first=next((i+1 for i,x in enumerate(items) if any(coverage(g,[x])>=0.5 for g in gold)),None)
        out['mrr']=1/first if first else (0.0 if gold else None)
    return out


def integrity(checks):
    return {'citation_count':len(checks),'citation_integrity':ratio(sum(c['valid'] for c in checks),len(checks))}
