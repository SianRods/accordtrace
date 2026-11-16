"""Check the running localhost API without exposing its bearer token or full text."""
import hashlib
import json
from pathlib import Path

import httpx


def main():
    backend = Path(__file__).resolve().parents[1]
    env = dict(line.split('=', 1) for line in (backend / '.env').read_text().splitlines()
               if line and not line.startswith('#'))
    headers = {'Authorization': 'Bearer ' + env['ACCORDTRACE_API_TOKEN']}
    with httpx.Client(base_url='http://127.0.0.1:8000', timeout=20, headers=headers) as client:
        ready = client.get('/health/ready')
        ready.raise_for_status()
        assert ready.json()['graph_ready'] and not ready.json()['search_ready']
        response = client.get('/api/v1/documents')
        response.raise_for_status()
        docs = response.json()['items']
        assert len(docs) == 6
        evidence_ids = []
        for doc in docs:
            response = client.get(f"/api/v1/documents/{doc['id']}/sections")
            response.raise_for_status()
            section = response.json()['items'][0]
            response = client.get('/api/v1/evidence/' + section['id'])
            response.raise_for_status()
            item = response.json()
            assert hashlib.sha256(item['quote'].encode()).hexdigest() == item['text_hash']
            assert item['source_hash'] == doc['raw_hash']
            assert item['end'] - item['start'] == len(item['quote'])
            evidence_ids.append(item['id'])
        result = dict(ready=ready.json(), documents=len(docs), evidence_ids=evidence_ids)
        print(json.dumps(result, indent=2))
        (backend / '.runtime/api-smoke.json').write_text(json.dumps(result, indent=2), encoding='utf-8')


if __name__ == '__main__':
    main()
