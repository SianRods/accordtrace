"""Offline, schema-driven PDF rendering. No providers, app imports or scoring calls."""
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import re
from xml.sax.saxutils import escape

from reportlab import rl_config
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import KeepTogether, LongTable, Paragraph, SimpleDocTemplate, Spacer, TableStyle

from .config import ROOT

TEMPLATE = ROOT / 'reports/pdf_template.json'


def display(value):
    if value is None:
        return 'Not measured'
    if isinstance(value, bool):
        return 'Yes' if value else 'No'
    if isinstance(value, float):
        return f'{value:.4f}' if math.isfinite(value) else 'Undefined'
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return str(value)


def metric_rows(report, template):
    """Discover every metric and every metric field; never whitelist metric names."""
    metadata = template.get('metrics', {})
    rows = []
    for name, item in sorted(report.get('metrics', {}).items()):
        entry = metadata.get(name, {})
        rows.append((name, entry.get('label', name.replace('_', ' ').capitalize()),
                     item if isinstance(item, dict) else {'value': item},
                     entry.get('interpretation', 'No metric-specific interpretation registered. Consult the metric definition; no direction or acceptance threshold is assumed.')))
    return rows


def render(report, output, template_path=TEMPLATE):
    """Render a complete report atomically, including future metric/status fields."""
    template = json.loads(Path(template_path).read_text(encoding='utf-8'))
    accent_hex = template.get('accent_color', '#176B72')
    if not re.fullmatch(r'#[0-9a-fA-F]{6}', accent_hex):
        raise ValueError('PDF accent_color must be a six-digit hex color')
    accent = colors.HexColor(accent_hex)
    # Bundled ReportLab font files avoid OS font dependencies or network downloads.
    font_dir = Path(rl_config.TTFSearchPath[0])
    candidates = [Path(p) for p in rl_config.TTFSearchPath if (Path(p)/'Vera.ttf').exists()]
    if candidates:
        font_dir = candidates[0]
    if 'AccordVera' not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(TTFont('AccordVera', str(font_dir/'Vera.ttf')))
        pdfmetrics.registerFont(TTFont('AccordVeraBold', str(font_dir/'VeraBd.ttf')))
        pdfmetrics.registerFontFamily('AccordVera', normal='AccordVera', bold='AccordVeraBold', italic='AccordVera', boldItalic='AccordVeraBold')
    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(name='Body', fontName='AccordVera', fontSize=8.5, leading=12,
                              textColor=colors.HexColor('#24343E'), spaceAfter=6, splitLongWords=True))
    styles.add(ParagraphStyle(name='Cell', parent=styles['Body'], fontSize=7.5, leading=10, spaceAfter=0))
    styles.add(ParagraphStyle(name='Section', parent=styles['Body'], fontName='AccordVeraBold', fontSize=12,
                              leading=16, spaceBefore=14, spaceAfter=7, textColor=accent, keepWithNext=True))
    styles.add(ParagraphStyle(name='ReportTitle', parent=styles['Body'], fontName='AccordVeraBold', fontSize=26,
                              leading=32, textColor=accent, spaceAfter=9))
    styles.add(ParagraphStyle(name='Metric', parent=styles['Body'], fontName='AccordVeraBold', fontSize=9,
                              spaceBefore=8, keepWithNext=True))

    def p(text, style='Body'):
        # Plain data is escaped; never interpret model text as ReportLab markup.
        text = display(text).replace('\u2011', '-').replace('\u2013', '-').replace('\u2014', '-')
        return Paragraph(escape(text).replace('\n', '<br/>'), styles[style])

    width = A4[0] - 36*mm
    def table(headers, rows, widths=None):
        data = [[p(x, 'Cell') for x in headers]] + [[p(x, 'Cell') for x in row] for row in rows]
        t = LongTable(data, colWidths=widths or [width/len(headers)]*len(headers), repeatRows=1,
                      hAlign='LEFT', splitByRow=1, splitInRow=1)
        t.setStyle(TableStyle([
            ('BACKGROUND', (0,0), (-1,0), colors.HexColor('#DDECEF')),
            ('ROWBACKGROUNDS', (0,1), (-1,-1), [colors.white, colors.HexColor('#F4F7F9')]),
            ('VALIGN', (0,0), (-1,-1), 'TOP'),
            ('LEFTPADDING', (0,0), (-1,-1), 7), ('RIGHTPADDING', (0,0), (-1,-1), 7),
            ('TOPPADDING', (0,0), (-1,-1), 6), ('BOTTOMPADDING', (0,0), (-1,-1), 6),
            ('LINEBELOW', (0,0), (-1,0), .6, accent),
        ]))
        return t

    story = [p(template.get('title', 'AccordTrace Evaluation'), 'ReportTitle'),
             p(template.get('subtitle', 'Independent testbed')),
             p('Generated (UTC): '+datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S'))]
    identity = report.get('identity', {})
    story += [p('Run overview', 'Section'), table(['Measure', 'Result'], [
        ('Run directory', Path(output).parent.name), ('Evaluation mode', identity.get('mode')),
        ('Planned / observed / complete', ' / '.join(display(report.get(k)) for k in ('planned_cases','observed_cases','complete_cases'))),
        ('Snapshot', identity.get('snapshot_id')),
        ('Actual retrieval modes', report.get('actual_retrieval_modes', {})),
        ('Retrieval warnings', report.get('retrieval_warnings', {})),
        ('Capture errors', report.get('capture_errors', {})),
        ('Reference review status', report.get('reference_review_status', {})),
    ], [width*.34, width*.66]),
        Spacer(1, 7*mm), p('Read this report with its denominators. Missing results are not zero or perfect scores. Auto-mode lexical fallback does not establish hybrid retrieval quality.')]

    rows = metric_rows(report, template)
    story.append(p('Metric results', 'Section'))
    if not rows:
        story.append(p('No aggregate metric values have been measured for this run.'))
    fields = list(dict.fromkeys(k for _,_,values,_ in rows for k in values))
    if rows and len(fields) <= 4:
        story.append(table(['Metric']+[x.replace('_',' ').capitalize() for x in fields],
                           [(name,)+tuple(values.get(k) for k in fields) for name,_,values,_ in rows],
                           [width*.44]+[width*.56/len(fields)]*len(fields) if fields else [width]))
    elif rows:
        for name, label, values, _ in rows:
            story += [p(label+' ('+name+')', 'Metric'),
                      table(['Statistic','Value'],list(values.items()),[width*.55,width*.45])]
    story.append(p('All values are unscaled: 1.0000 represents 100% only when the metric is a proportion. Counts and rank statistics retain their own units. Means use scored cases; missing cases are excluded, not scored as zero.'))
    definitions=[(name, interpretation) for name,_,_,interpretation in rows if name in template.get('metrics',{})]
    if definitions:
        story += [p('Metric interpretation', 'Section'), table(['Metric','How to read it'], definitions, [width*.34,width*.66])]
    if any(name not in template.get('metrics',{}) for name,_,_,_ in rows):
        story.append(p('Metrics without a registered interpretation use their raw key. Consult their definitions; no direction, unit or acceptance threshold is assumed. Add explanations in reports/pdf_template.json.'))

    # All remaining top-level fields, including future additions, are discovered.
    story.append(p('Coverage, outcomes and provenance', 'Section'))
    for key, value in report.items():
        if key in ('metrics', 'per_contract', 'limitations', 'identity'):
            continue
        story += [p(key.replace('_', ' ').capitalize(), 'Metric')]
        if isinstance(value, dict) and value:
            story.append(table(['Item', 'Value'], list(value.items()), [width*.62,width*.38]))
        else:
            story.append(p(value))
    story.append(p('Per-contract metrics', 'Section'))
    if not report.get('per_contract'):
        story.append(p('No per-contract metric values available.'))
    for agreement, values in sorted(report.get('per_contract', {}).items()):
        contract_fields=list(dict.fromkeys(k for v in values.values() for k in v))
        story += [p(agreement, 'Metric'), table(['Metric']+contract_fields,
                  [(k,)+tuple(v.get(f) for f in contract_fields) for k,v in values.items()],
                  [width*.55]+[width*.45/len(contract_fields)]*len(contract_fields) if contract_fields else [width])]
    story.append(KeepTogether([p('Interpretation and limitations', 'Section')]+
                             [p('- '+x) for x in report.get('limitations', [])]))
    story += [p('Run identity', 'Section'), table(['Field', 'Value'], list(identity.items()), [width*.29,width*.71])]

    output = Path(output); output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix('.pdf.tmp')
    def page(canvas, doc):
        canvas.saveState()
        canvas.setStrokeColor(accent); canvas.line(18*mm, 17*mm, A4[0]-18*mm, 17*mm)
        canvas.setFont('AccordVera', 6.2); canvas.setFillColor(colors.HexColor('#52646E'))
        canvas.drawString(18*mm, 12*mm, template.get('footer', 'AccordTrace evaluation'))
        canvas.drawRightString(A4[0]-18*mm, 12*mm, str(doc.page))
        canvas.restoreState()
    try:
        SimpleDocTemplate(str(temporary), pagesize=A4, leftMargin=18*mm, rightMargin=18*mm,
                          topMargin=17*mm, bottomMargin=23*mm, title=template.get('title'),
                          author='AccordTrace Testbed').build(story, onFirstPage=page, onLaterPages=page)
        temporary.replace(output)
    finally:
        temporary.unlink(missing_ok=True)
    return output
