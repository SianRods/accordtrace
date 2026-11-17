"""Strict provider outputs and public message contracts."""
from typing import Literal
from pydantic import BaseModel,ConfigDict,Field,model_validator

AnswerStatus=Literal['supported','partially_supported','insufficient_evidence','conflicting_evidence','needs_clarification','service_unavailable']

class Strict(BaseModel):
    model_config=ConfigDict(extra='forbid')

class Action(Strict):
    kind:Literal['search','lookup_section','lookup_term','expand_parent','expand_graph']
    query:str|None=Field(max_length=1536)
    section_label:str|None=Field(max_length=120)
    term:str|None=Field(max_length=120)
    evidence_id:str|None=Field(max_length=30)

    @model_validator(mode='after')
    def selector(self):
        required={'search':'query','lookup_section':'section_label','lookup_term':'term',
                  'expand_parent':'evidence_id','expand_graph':'evidence_id'}[self.kind]
        if not getattr(self,required):raise ValueError('missing action selector: '+required)
        if self.query and len(self.query.encode())>1536:raise ValueError('query exceeds byte limit')
        return self

class Task(Strict):
    id:str=Field(min_length=1,max_length=40)
    question:str=Field(max_length=1600)
    action:Action

class Plan(Strict):
    standalone_question:str=Field(min_length=1,max_length=1600)
    tasks:list[Task]=Field(max_length=3)
    clarification:str|None=Field(max_length=1000)

class Citation(Strict):
    evidence_id:str
    quote:str|None

class Claim(Strict):
    id:str=Field(min_length=1,max_length=40)
    text:str=Field(max_length=1600)
    citations:list[Citation]=Field(max_length=6)

class Draft(Strict):
    claims:list[Claim]=Field(max_length=8)
    gaps:list[str]=Field(max_length=12)

class Verdict(Strict):
    claim_id:str
    verdict:Literal['supported','unsupported','conflicting']
    reason:str=Field(max_length=600)

class Review(Strict):
    verdicts:list[Verdict]=Field(max_length=8)
    material_gaps:list[str]=Field(max_length=12)
    repair:Action|None

class CreateConversation(Strict):
    agreement_ids:list[str]=Field(min_length=1,max_length=6)
    snapshot_id:str|None=None
    @model_validator(mode='after')
    def distinct(self):
        if len(set(self.agreement_ids))!=len(self.agreement_ids):raise ValueError('duplicate agreement IDs')
        return self

class MessageRequest(Strict):
    content:str=Field(min_length=1,max_length=4000)
    mode:Literal['auto','extractive']='auto'
    section_label:str|None=Field(default=None,max_length=120)
    defined_term:str|None=Field(default=None,max_length=120)
    @model_validator(mode='after')
    def selector(self):
        if self.section_label and self.defined_term:raise ValueError('one lookup selector only')
        if self.mode=='extractive' and not (self.section_label or self.defined_term):raise ValueError('lookup selector required')
        if not self.content.strip():raise ValueError('empty question')
        return self

def provider_schema(model):
    """Groq strict mode requires all properties, including nullable ones."""
    schema=model.model_json_schema()
    def visit(value):
        if isinstance(value,dict):
            value.pop('default',None)
            if value.get('type')=='object':
                value['additionalProperties']=False
                value['required']=list(value.get('properties',{}))
            for child in value.values():visit(child)
        elif isinstance(value,list):
            for child in value:visit(child)
    visit(schema);return schema
