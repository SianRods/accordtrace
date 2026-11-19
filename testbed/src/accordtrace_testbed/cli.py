"""Run from anywhere: uv run --project testbed accordtrace-testbed --help."""
import argparse
from contextlib import contextmanager
import json
import os
from pathlib import Path
from .config import ROOT,Settings
from .dataset import load,validate_sources


@contextmanager
def exclusive(directory):
    directory.mkdir(parents=True,exist_ok=True);path=directory/'.writer.lock'
    fd=os.open(path,os.O_CREAT|os.O_EXCL|os.O_WRONLY)
    try:
        os.write(fd,str(os.getpid()).encode());os.close(fd);yield
    finally:path.unlink(missing_ok=True)


def main():
    parser=argparse.ArgumentParser(description='Independent six-contract HTTP evaluation; no backend mutation/admin access.')
    parser.add_argument('--dataset',type=Path,default=ROOT/'datasets/pilot.jsonl')
    sub=parser.add_subparsers(dest='command',required=True)
    sub.add_parser('validate')
    sub.add_parser('config',help='Check effective configuration without exposing secrets or making live calls')
    preflight=sub.add_parser('preflight');preflight.add_argument('--embeddings',action='store_true')
    collect=sub.add_parser('collect')
    collect.add_argument('--run',required=True,help='Safe name under testbed/.runtime/')
    collect.add_argument('--mode',choices=['retrieval','extractive','chat'],default='retrieval')
    collect.add_argument('--search-mode',choices=['auto','exact','lexical'],default='auto')
    collect.add_argument('--limit',type=int)
    collect.add_argument('--allow-live-chat',action='store_true')
    judge=sub.add_parser('score');judge.add_argument('--run',required=True);judge.add_argument('--judge',action='store_true')
    judge.add_argument('--metrics',default='context_recall',help='Comma-separated explicitly selected Ragas metrics')
    report=sub.add_parser('report');report.add_argument('--run',required=True)
    args=parser.parse_args();settings=Settings()
    try:
        if args.command=='config':
            from .configuration import inspect
            print(json.dumps(inspect(settings),indent=2));return
        if args.command=='validate':
            print(json.dumps(validate_sources(load(args.dataset),settings.sources)));return
        if args.command=='preflight':
            from .client import Backend
            from .judges import LocalOllama
            b=Backend(settings)
            try:
                health=b.request('GET','/health/ready')
                snapshot=b.request('GET','/api/v1/snapshots/'+health['active_snapshot_id'])
                profile=snapshot['configuration']['embedding']
                status={'backend_search_ready':health.get('search_ready'), 'snapshot_id':snapshot['id'],
                    'embedding_provider':profile['provider'], 'model':profile['model'],
                    'judge_configured':bool(settings.gemini_api_key.get_secret_value() and settings.gemini_model and settings.gemini_free_tier_confirmed),
                    'gemini_live_checked':False,'ollama_live_checked':False}
                if args.embeddings:
                    local=LocalOllama(settings,profile,ROOT/'.runtime/preflight_embedding_cache');local.metadata()
                    status['embedding_dimensions']=len(local.embed('merger agreement definition'))
                    status['ollama_live_checked']=True
                print(json.dumps(status));return
            finally:b.close()
        import re
        if not re.fullmatch(r'[a-zA-Z0-9_-]+',args.run):raise ValueError('Invalid run name')
        directory=ROOT/'.runtime'/args.run
        from .report import build
        if args.command=='collect':
            from .runner import collect as run
            cases=load(args.dataset)
            if args.limit is not None:
                if args.limit<1:raise ValueError('limit must be positive')
                cases=cases[:args.limit]
            with exclusive(directory):
                try:run(settings,cases,directory,args.mode,args.search_mode,args.allow_live_chat)
                finally:
                    if (directory/'manifest.json').exists():value=build(directory)
        elif args.command=='score':
            from .judges import METRICS,score
            names=args.metrics.split(',')
            if not set(names)<=set(METRICS):raise ValueError('Unknown metric')
            with exclusive(directory):
                try:score(directory,settings,args.judge,names)
                finally:
                    if (directory/'manifest.json').exists():value=build(directory)
        else:
            with exclusive(directory):value=build(directory)
        print(json.dumps({'report':str(directory/'report.md'),'pdf':str(directory/'report.pdf'),'complete_cases':value['complete_cases'],'observed_cases':value['observed_cases']}))
    except Exception as exc:
        # No raw provider/HTTP exceptions, headers, or credentials printed.
        detail=str(exc) if isinstance(exc,(ValueError,RuntimeError)) else type(exc).__name__
        parser.exit(1,'Testbed stopped: '+detail+'\n')


if __name__=='__main__':main()
