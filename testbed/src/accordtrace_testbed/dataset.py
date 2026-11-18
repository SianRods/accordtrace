"""Gold evidence contracts independent of backend IDs, parser and database models."""
import hashlib
import json
from pathlib import Path
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator

REVISION = '37d5c3b95d18dcd8404cc5ce3fd5069be062392f'
AGREEMENTS = {f'contract_{i}' for i in range(6)}


def sha(data):
    return hashlib.sha256(data).hexdigest()


def fingerprint(value):
    return sha(json.dumps(value,sort_keys=True,ensure_ascii=False).encode())


class Evidence(BaseModel):
    model_config = ConfigDict(extra='forbid')
    agreement_id: str
    source_hash: str = Field(pattern=r'^[a-f0-9]{64}$')
    start: int = Field(ge=0)
    end: int = Field(gt=0)
    quote: str = Field(min_length=1)

    @model_validator(mode='after')
    def valid(self):
        if self.agreement_id not in AGREEMENTS or self.start >= self.end:
            raise ValueError('Invalid source or Unicode range')
        return self


class Case(BaseModel):
    model_config = ConfigDict(extra='forbid')
    id: str = Field(pattern=r'^[a-z0-9_-]+$')
    question: str = Field(min_length=1,max_length=1536)
    agreement_ids: list[str] = Field(min_length=1,max_length=6)
    category: str
    reference: str
    reference_contexts: list[Evidence]
    defined_term: str | None = None
    expected_behavior: Literal['answer','abstain','clarify'] = 'answer'
    review_status: Literal['draft','source_verified','human_reviewed'] = 'draft'
    source_revision: str = REVISION
    notes: str = ''

    @model_validator(mode='after')
    def scope(self):
        if len(set(self.agreement_ids)) != len(self.agreement_ids) or not set(self.agreement_ids) <= AGREEMENTS:
            raise ValueError('Only six distinct approved agreements are allowed')
        if any(e.agreement_id not in self.agreement_ids for e in self.reference_contexts):
            raise ValueError('Reference outside question scope')
        if self.expected_behavior == 'answer' and not self.reference_contexts:
            raise ValueError('Answerable reference needs source evidence')
        if self.source_revision != REVISION:
            raise ValueError('Unexpected corpus revision')
        return self


def load(path: Path):
    cases = [Case.model_validate_json(l) for l in path.read_text(encoding='utf-8').splitlines() if l.strip()]
    if len({c.id for c in cases}) != len(cases):
        raise ValueError('Duplicate case IDs')
    return cases


def validate_sources(cases, directory):
    cache = {}
    for case in cases:
        for e in case.reference_contexts:
            if e.agreement_id not in cache:
                raw = (directory/(e.agreement_id+'.txt')).read_bytes()
                cache[e.agreement_id] = (sha(raw),raw.decode('utf-8-sig'))
            digest,text = cache[e.agreement_id]
            if digest != e.source_hash or text[e.start:e.end] != e.quote or e.end > len(text):
                raise ValueError('Source/hash/Unicode evidence mismatch: '+case.id)
    return {'cases':len(cases),'agreements':sorted(cache),'source_revision':REVISION}
