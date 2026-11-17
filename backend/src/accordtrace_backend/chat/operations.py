"""Explicit administrative operations; event retention defaults to dry run."""
from datetime import timedelta
from sqlalchemy import delete,func,select
from sqlalchemy.orm import Session
from accordtrace_backend.common.core import DomainError
from accordtrace_backend.persistence.storage import ActiveSnapshot, Snapshot, utcnow
from accordtrace_backend.chat import checkpoints
from accordtrace_backend.chat.models import Event, Run
from accordtrace_backend.chat.providers import LiveProviders, configured
from accordtrace_backend.chat.repository import TERMINAL
from accordtrace_backend.chat.schemas import Plan, provider_schema


def preflight(settings,engine,live=False):
    with Session(engine) as db:
        pointer=db.get(ActiveSnapshot,1);snapshot=db.get(Snapshot,pointer.snapshot_id) if pointer and pointer.snapshot_id else None
        from accordtrace_backend.indexing.embedding import mode_matches
        active=bool(snapshot and snapshot.search_ready and snapshot.configuration['embedding']['verification']=='live' and mode_matches(settings,snapshot.configuration['embedding']))
    result=dict(checkpoints=checkpoints.ready(engine),credentials_configured=configured(settings),live_search_snapshot=active,live_checked=False,offline_mode=settings.offline_mode)
    if live:
        if not all(result[k] for k in ('checkpoints','credentials_configured','live_search_snapshot')):raise DomainError('live_preflight_prerequisites_missing')
        # Explicit operator smoke only: no hidden SDK retries or model substitutions.
        from accordtrace_backend.chat.prompts import MODELS
        from accordtrace_backend.chat.providers import quota_reserve, quota_result, request_estimate
        from accordtrace_backend.chat.schemas import Draft, Review
        roles={'planner':Plan,'draft':Draft,'reviewer':Review};usage={}
        for role,model_type in roles.items():
            provider,model=MODELS[role]
            payload={'question':'Smoke check only. No evidence is supplied; return empty claims/tasks/verdicts and a clarification/gap.'}
            schema=provider_schema(model_type);estimate=request_estimate(role,payload,schema,1024)
            with Session(engine) as db,db.begin():quota_reserve(db,settings,provider,model,estimate)
            value,used=LiveProviders(settings).generate(role,payload,schema,10,1024)
            model_type.model_validate(value)
            with Session(engine) as db,db.begin():quota_result(db,provider,model,estimate,used)
            usage[role]=used
        result.update(live_checked=True,usage=usage)
    return result


def prune_events(engine,days=7,apply=False):
    if days<7:raise DomainError('retention_minimum_seven_days',422)
    with Session(engine) as db,db.begin():
        ids=select(Run.id).where(Run.status.in_(TERMINAL),Run.created_at<utcnow()-timedelta(days=days))
        condition=Event.run_id.in_(ids)
        count=db.scalar(select(func.count()).select_from(Event).where(condition))
        if apply:db.execute(delete(Event).where(condition))
        return dict(events=count,applied=apply,minimum_days=days)
