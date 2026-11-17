"""Single model-intensive worker with durable quotas, leases and recovery."""
import logging
import threading
import time
from datetime import timedelta
from sqlalchemy import select,text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session
from accordtrace_backend.common.core import DomainError, Settings
from accordtrace_backend.persistence.storage import engine, utcnow
from accordtrace_backend.chat import checkpoints
from accordtrace_backend.chat.models import ModelCall, Run
from accordtrace_backend.chat.prompts import configuration
from accordtrace_backend.chat.providers import LiveProviders
from accordtrace_backend.chat.repository import Cancelled, Halt, QuotaWait, claim, event, finalize
from accordtrace_backend.chat.workflow import Workflow

LOG=logging.getLogger(__name__)


def heartbeat(db_engine,run_id,token,stop):
    previous=time.monotonic()
    while not stop.wait(0.5):
        now=time.monotonic()
        try:
            with Session(db_engine) as db,db.begin():
                run=db.get(Run,run_id,with_for_update=True)
                if not run or run.lease_token!=token or run.status not in ('running','cancelling') or not run.lease_until or run.lease_until<=utcnow():return
                run.active_seconds+=now-previous;run.lease_until=utcnow()+timedelta(seconds=120)
            previous=now
        except Exception:
            LOG.warning('chat_heartbeat_unavailable');return


def fail(db_engine,run_id,token,code):
    # Access revocation and lease loss never pass through a content finalizer.
    with Session(db_engine) as db,db.begin():
        run=db.get(Run,run_id,with_for_update=True)
        if run is None or run.lease_token!=token or run.status not in ('running','cancelling') or not run.lease_until or run.lease_until<=utcnow():return
        if code in ('source_access_revoked','chat_lease_lost'):
            run.status='failed';run.error_code=code
        elif run.cancel_requested or code=='cancelled':run.status='cancelled'
        else:return False
        event(db,run,run.status,run.status,{'code':code});run.stage=run.status;run.lease_token=None;run.lease_until=None
        return True


def process(settings,db_engine,claimed,providers=None,embedding_provider=None):
    if claimed is None:return False
    run_id,token=claimed;providers=providers or LiveProviders(settings)
    stop=threading.Event();thread=threading.Thread(target=heartbeat,args=(db_engine,run_id,token,stop),daemon=True)
    thread.start()
    try:
        with Session(db_engine) as db,db.begin():
            run=db.get(Run,run_id,with_for_update=True)
            # An interrupted HTTP request may have completed remotely. Never refund it.
            for call in db.scalars(select(ModelCall).where(ModelCall.run_id==run_id,ModelCall.status=='in_flight')):
                call.status='uncertain';run.active_seconds+=call.timeout
            if run.configuration!=configuration(settings,providers.scripted):raise Halt('workflow_version_unavailable')
        if not checkpoints.ready(db_engine):raise Halt('chat_checkpoint_setup_required')
        workflow=Workflow(settings,db_engine,run_id,token,providers,embedding_provider)
        with checkpoints.saver(settings) as saver:
            graph=workflow.graph(saver);config={'configurable':{'thread_id':run_id},'recursion_limit':32}
            state=graph.get_state(config)
            graph.invoke(None if state.values else {'run_id':run_id},config,durability='sync')
        return True
    except QuotaWait as exc:
        with Session(db_engine) as db,db.begin():
            run=db.get(Run,run_id,with_for_update=True)
            if run.lease_token==token and run.status in ('running','cancelling'):
                if run.cancel_requested:
                    run.status='cancelled';event(db,run,'cancelled','cancelled',{})
                else:
                    run.status='waiting_quota';run.retry_at=min(exc.retry_at,run.expires_at)
                    event(db,run,'quota:'+str(run.attempts),'waiting_quota',{'retry_at':run.retry_at.isoformat()})
                run.lease_token=None;run.lease_until=None
        return True
    except Exception as exc:
        code=exc.code if isinstance(exc,DomainError) else 'chat_execution_failed'
        LOG.warning('chat_run_stopped code=%s',code)
        if fail(db_engine,run_id,token,code):return True
        ambiguous=code.startswith('ambiguous_')
        result=dict(status='needs_clarification' if ambiguous else 'service_unavailable',
            answer='Please specify the intended clause or definition.' if ambiguous else 'A verified answer is unavailable for this run.',claims=[],gaps=[code])
        try:finalize(db_engine,run_id,token,result,[],[])
        except DomainError as final_error:fail(db_engine,run_id,token,final_error.code)
        return True
    finally:
        stop.set();thread.join(timeout=3)


def work(settings=None,once=False):
    settings=settings or Settings();db_engine=engine(settings)
    try:
        while True:
            handled=False
            # Session-level advisory lock also serializes separate worker processes.
            try:
                with db_engine.connect() as lock:
                    if lock.execute(text('SELECT pg_try_advisory_lock(71240613)')).scalar():
                        try:handled=process(settings,db_engine,claim(db_engine,120))
                        finally:lock.execute(text('SELECT pg_advisory_unlock(71240613)'))
            except SQLAlchemyError:
                if once:raise
                LOG.warning('chat_storage_unavailable')
            if once:return
            if not handled:time.sleep(0.5)
    finally:db_engine.dispose()
