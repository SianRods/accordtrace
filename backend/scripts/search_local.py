"""Local evidence-search client; reads credentials without printing them."""
import argparse
import json
from pathlib import Path
import httpx


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--agreement',action='append',required=True)
    parser.add_argument('--snapshot')
    parser.add_argument('--lexical',action='store_true')
    selector=parser.add_mutually_exclusive_group(required=True)
    selector.add_argument('--query');selector.add_argument('--section');selector.add_argument('--term')
    args=parser.parse_args()
    env=dict(line.split('=',1) for line in (Path(__file__).resolve().parents[1]/'.env').read_text().splitlines()
             if line and not line.startswith('#'))
    payload=dict(agreement_ids=args.agreement,mode='lexical' if args.lexical else 'auto')
    if args.query:payload['query']=args.query
    else:payload.update(mode='exact',section_label=args.section,defined_term=args.term)
    if args.snapshot:payload['snapshot_id']=args.snapshot
    response=httpx.post('http://127.0.0.1:8000/api/v1/search',json=payload,timeout=20,
                        headers={'Authorization':'Bearer '+env['ACCORDTRACE_API_TOKEN']})
    print(json.dumps(response.json(),indent=2,ensure_ascii=True))
    return 0 if response.is_success else 1


if __name__=='__main__':raise SystemExit(main())
