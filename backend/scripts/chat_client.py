"""Minimal authenticated terminal client; never accepts API tokens as arguments."""
import argparse
import json
import os
import uuid
import httpx


def main():
    parser=argparse.ArgumentParser();parser.add_argument('question')
    parser.add_argument('--url',default='http://127.0.0.1:8000')
    parser.add_argument('--conversation');parser.add_argument('--agreement',action='append')
    parser.add_argument('--section');parser.add_argument('--term')
    args=parser.parse_args();token=os.environ.get('ACCORDTRACE_API_TOKEN')
    if not token:parser.error('Set ACCORDTRACE_API_TOKEN in the process environment.')
    with httpx.Client(base_url=args.url,headers={'Authorization':'Bearer '+token},timeout=660) as client:
        conversation=args.conversation
        if not conversation:
            if not args.agreement:parser.error('--agreement required for a new conversation')
            response=client.post('/api/v1/conversations',json={'agreement_ids':args.agreement});response.raise_for_status()
            conversation=response.json()['id'];print('Conversation:',conversation)
        body={'content':args.question}
        if args.section or args.term:body.update(mode='extractive',section_label=args.section,defined_term=args.term)
        response=client.post('/api/v1/conversations/'+conversation+'/messages',json=body,headers={'Idempotency-Key':str(uuid.uuid4())})
        response.raise_for_status();run=response.json()['id'];print('Run:',run)
        with client.stream('GET','/api/v1/runs/'+run+'/events') as stream:
            stream.raise_for_status()
            for line in stream.iter_lines():
                if line.startswith('data: '):print(json.dumps(json.loads(line[6:]),indent=2))


if __name__=='__main__':main()
