"""Recheck retained integration state after service restart; no provider calls."""
import json
from pydantic import SecretStr

from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from accordtrace_backend.core import Settings
from accordtrace_backend.indexing import validate
from accordtrace_backend.retrieval import SearchRequest,search
from accordtrace_backend.storage import ActiveSnapshot,Snapshot,engine


def main():
    settings=Settings()
    evidence=json.loads((settings.runtime/'phase2-acceptance.json').read_text())
    url=make_url(settings.database_url.get_secret_value()).set(database='accordtrace_phase2_checks')
    settings=settings.model_copy(update={'database_url':SecretStr(url.render_as_string(hide_password=False))})
    db_engine=engine(settings)
    report=validate(settings,db_engine,evidence['snapshot_id'])
    packet=search(settings,db_engine,SearchRequest(mode='exact',agreement_ids=['contract_0'],
        defined_term='Per Share Cash Amount',snapshot_id=evidence['snapshot_id']))
    ids=[item['chunk_id'] for group in packet['groups'] for item in group['evidence']]
    assert ids==evidence['exact_evidence_ids']
    assert not any(w['code']=='graph_expansion_unavailable' for w in packet['warnings'])
    with Session(db_engine) as db:
        active=db.get(ActiveSnapshot,1)
        assert active.snapshot_id==evidence['activation']['snapshot_id']
        assert active.generation==evidence['activation']['generation']
        generation=active.generation
    result=dict(verification=evidence['verification'],database=url.database,snapshot_id=evidence['snapshot_id'],
        chunks=report['chunks'],graph=report['graph'],vectors=report['vectors'],same_evidence_ids=True,
        activation_generation=generation,provider_calls=0)
    (settings.runtime/'phase2-restart.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
    print(json.dumps(result,indent=2));db_engine.dispose()


if __name__=='__main__':main()
