"""Backend operational commands; no model calls or implicit migrations."""
import argparse
import json
import logging
import sys

from sqlalchemy.orm import Session

from .core import DomainError, Settings
from .ingestion import enqueue, retry, sources, work
from .storage import Job, Snapshot, engine


def main():
    parser=argparse.ArgumentParser(prog='accordtrace-backend')
    sub=parser.add_subparsers(dest='command',required=True)
    sub.add_parser('validate-sources')
    start=sub.add_parser('enqueue')
    start.add_argument('--key',required=True)
    worker=sub.add_parser('worker')
    worker.add_argument('--once',action='store_true')
    for name in ('job','retry','snapshot'):
        sub.add_parser(name).add_argument('id')
    args=parser.parse_args()
    settings=Settings()
    logging.basicConfig(level=logging.INFO,format='%(levelname)s %(name)s %(message)s')
    db_engine=engine(settings)
    try:
        if args.command=='validate-sources':
            records,fp=sources(settings)
            print(json.dumps({'fingerprint':fp,'documents':[{k:r[k] for k in ('name','sha','revision')} for r in records]},indent=2))
        elif args.command=='enqueue':
            print(enqueue(settings,db_engine,args.key).id)
        elif args.command=='worker': work(settings,once=args.once)
        elif args.command=='retry': retry(db_engine,args.id)
        else:
            with Session(db_engine) as db:
                row=db.get(Job if args.command=='job' else Snapshot,args.id)
                if row is None: raise DomainError('not_found',404)
                print(json.dumps({c.name:getattr(row,c.name) for c in row.__table__.columns
                                 if c.name not in ('lease_token','idempotency_key')},default=str,indent=2))
    except Exception as exc:
        print(json.dumps({'error':exc.code if isinstance(exc,DomainError) else type(exc).__name__}),file=sys.stderr)
        return 1
    finally:
        db_engine.dispose()
    return 0


if __name__=='__main__':
    sys.exit(main())
