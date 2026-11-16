"""Print bounded, source-backed definition/reference examples for review."""
import json
from collections import Counter

from sqlalchemy import select
from sqlalchemy.orm import Session

from accordtrace_backend.core import PARSER_VERSION, Settings
from accordtrace_backend.storage import Document, Issue, Reference, Span, Term, engine


def main():
    settings = Settings()
    db_engine = engine(settings)
    output = []
    with Session(db_engine) as db:
        for doc in db.scalars(select(Document).order_by(Document.agreement_id)):
            spans = select(Span.id).where(Span.document_id == doc.id, Span.parser_version == PARSER_VERSION)
            term = db.scalar(select(Term).where(Term.span_id.in_(spans), Term.location_only.is_(False)).order_by(Term.name))
            ref = db.scalar(select(Reference).where(Reference.source_id.in_(spans), Reference.status == 'verified').order_by(Reference.start))
            definition = db.get(Span, term.span_id)
            target = db.get(Span, ref.target_id)
            issues = db.scalars(select(Issue).where(Issue.document_id == doc.id, Issue.parser_version == PARSER_VERSION))
            output.append(dict(agreement_id=doc.agreement_id, document_version_id=doc.id,
                definition=dict(term=term.name, span_id=definition.id, start=definition.start,
                    quote=doc.text[definition.start:definition.end][:200]),
                reference=dict(id=ref.id, literal=ref.literal, start=ref.start,
                    context=doc.text[max(0, ref.start-45):ref.end+45], target_label=target.label,
                    target_span_id=target.id, target_start=target.start,
                    target_quote=doc.text[target.start:target.end][:120]),
                issues=dict(Counter(i.detail['code'] for i in issues))))
    db_engine.dispose()
    result = json.dumps(output, indent=2, ensure_ascii=True)
    print(result)
    (settings.runtime / 'inspection.json').write_text(result, encoding='utf-8')


if __name__ == '__main__':
    main()
