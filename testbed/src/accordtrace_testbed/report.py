"""Coverage and denominators are part of every result; no fabricated aggregate."""
from collections import Counter,defaultdict
import json
from pathlib import Path
from .runner import write
from .dataset import fingerprint,sha


def build(directory):
    directory=Path(directory);manifest=json.loads((directory/'manifest.json').read_text(encoding='utf-8'))
    rows=[json.loads(p.read_text(encoding='utf-8')) for p in sorted((directory/'observations').glob('*.json'))]
    metrics=defaultdict(list);cohorts=defaultdict(list);judge=Counter();outcomes=Counter();reviews=Counter();citation_checks=[]
    actual_modes=Counter();warnings=Counter();completeness=[];judge_reasons=Counter();judge_profiles={}
    for row in rows:
        reviews[row['case']['review_status']]+=1
        if row.get('complete'):
            packet=row.get('search_packet',{})
            actual_modes[packet.get('retrieval_mode','unknown')]+=1
            warnings.update(w.get('code','unknown') for w in packet.get('warnings',[]))
            complete=row.get('retrieval_metrics',{}).get('required_evidence_complete')
            if complete is not None:completeness.append(int(complete))
            for name,value in row.get('retrieval_metrics',{}).items():
                if isinstance(value,(int,float)) and not isinstance(value,bool):
                    metrics[name].append(value)
                    cohorts[('+'.join(row['case']['agreement_ids']),name)].append(value)
            for name,value in row.get('citation_metrics',{}).items():
                if value is not None:metrics[name].append(value)
            citation_checks.extend(row.get('citation_checks',[]))
            if 'chat_scope_valid' in row:metrics['chat_scope_valid'].append(int(row['chat_scope_valid']))
        run=row.get('chat',{}).get('run',{})
        if run:outcomes[run.get('answer_status') or run['status']]+=1
        answer_status=run.get('answer_status')
        if run.get('status')=='completed' and answer_status!='service_unavailable':
            if row['case']['expected_behavior']=='answer':
                metrics['false_abstention_rate'].append(int(answer_status=='insufficient_evidence'))
                metrics['answer_emission_rate'].append(int(bool((run.get('result') or {}).get('claims'))))
            elif row['case']['expected_behavior']=='abstain':
                metrics['correct_abstention_rate'].append(int(answer_status=='insufficient_evidence'))
        score=directory/'scores'/(row['case']['id']+'.json')
        if score.exists():
            stored=json.loads(score.read_text(encoding='utf-8'))
            if stored.get('observation_hash')!=fingerprint(row):
                judge['stale_score_record']=judge['stale_score_record']+1
                continue
            if stored.get('judge'):
                judge_profiles[fingerprint(stored['judge'])]=stored['judge']
            for name,item in stored['metrics'].items():
                judge[name+':'+item['status']]+=1
                if item.get('reason'):judge_reasons[name+':'+item['reason']]+=1
                if item['status']=='scored':metrics['ragas_'+name].append(item['value'])
    summary={name:{'mean':sum(vals)/len(vals),'scored_cases':len(vals),'observed_cases':len(rows)} for name,vals in sorted(metrics.items())}
    planned=len(manifest['identity'].get('case_ids',rows))
    errors=Counter(r.get('error_code',r.get('error_type','unknown')) for r in rows if not r.get('complete'))
    per_contract={}
    for (agreement,name),vals in sorted(cohorts.items()):per_contract.setdefault(agreement,{})[name]={'mean':sum(vals)/len(vals),'n':len(vals)}
    report={'identity':manifest['identity'],'observed_cases':len(rows),'complete_cases':sum(bool(r.get('complete')) for r in rows),
        'metric_code_sha256':sha((Path(__file__).parent/'metrics.py').read_bytes()),
        'planned_cases':planned,'capture_errors':dict(errors),
        'required_evidence_success_over_planned':sum(completeness)/planned if planned else None,
        'citation_integrity_micro':sum(c['valid'] for c in citation_checks)/len(citation_checks) if citation_checks else None,
        'required_evidence_completeness':sum(completeness)/len(completeness) if completeness else None,
        'actual_retrieval_modes':dict(actual_modes),'retrieval_warnings':dict(warnings),
        'reference_review_status':dict(reviews),'outcomes':dict(outcomes),'metrics':summary,'per_contract':per_contract,
        'judge_coverage':dict(judge),'judge_status_reasons':dict(judge_reasons),
        'judge_profiles':list(judge_profiles.values()),'limitations':[
            'This source-verified pilot is definition-focused, not an expert-reviewed legal benchmark.',
            'Separate search packets are not the evidence seen by the chat models.',
            'True generation faithfulness is unavailable through the current backend HTTP API.',
            'Citation groundedness is a proxy, not a substitute for generation faithfulness.',
            'Missing judge/provider results are not zeros or perfect scores.',
            'Six agreements do not establish broad legal-domain accuracy.']}
    write(directory/'report.json',report)
    lines=['# AccordTrace pilot evaluation report','',f"Mode: **{manifest['identity']['mode']}**; planned: **{report['planned_cases']}**; observed cases: **{len(rows)}**; complete: **{report['complete_cases']}**.",
        '',f"Snapshot: `{manifest['identity']['snapshot_id']}`",'',
        'Actual retrieval modes: `'+json.dumps(dict(actual_modes))+'`.',
        'Retrieval warnings: `'+json.dumps(dict(warnings))+'`.',
        'Capture errors: `'+json.dumps(dict(errors))+'`.',
        '**Auto-mode fallback is not a successful hybrid evaluation. Separate degraded and hybrid cohorts.**','',
        '| Metric | Mean | Scored cases | Observed cases |','|---|---:|---:|---:|']
    lines.extend(f"| {name} | {v['mean']:.4f} | {v['scored_cases']} | {len(rows)} |" for name,v in summary.items())
    lines+=['','## Judge coverage','', '```json',json.dumps(report['judge_coverage'],indent=2),'```',
        '','## Outcomes','', '```json',json.dumps(report['outcomes'],indent=2),'```','',
        '## Interpretation and limitations','']+['- '+x for x in report['limitations']]
    (directory/'report.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    from .pdf_report import render
    render(report,directory/'report.pdf')
    return report
