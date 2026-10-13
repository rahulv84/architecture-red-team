"""PDF assessment report rendered locally with ReportLab (no browser or system renderer)."""
from __future__ import annotations

import os
from pathlib import Path
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import KeepTogether, PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from ..models.schemas import AssessmentReport, ReportFinding

SEV_COLORS = {
    "CRITICAL": colors.HexColor("#b91c1c"), "HIGH": colors.HexColor("#c2410c"), "MEDIUM": colors.HexColor("#a16207"),
    "LOW": colors.HexColor("#1d4ed8"), "INFORMATIONAL": colors.HexColor("#4b5563"),
}
BAND_COLORS = {"Strong": "#15803d", "Adequate": "#4d7c0f", "Needs attention": "#b45309", "At risk": "#b91c1c", "Not assessed": "#6b7280"}


def _safe(text: object) -> str:
    """Escape for ReportLab paragraphs and fold characters outside Latin-1 (standard fonts)."""
    s = str(text if text is not None else "")
    for src, dst in (("→", "->"), ("—", "-"), ("–", "-"), ("’", "'"), ("“", '"'), ("”", '"'), ("…", "..."), ("≥", ">="), ("≤", "<=")):
        s = s.replace(src, dst)
    s = s.encode("latin-1", "replace").decode("latin-1")
    return escape(s).replace("\n", "<br/>")


def _styles() -> dict[str, ParagraphStyle]:
    base = getSampleStyleSheet()
    return {
        "title": ParagraphStyle("t", parent=base["Title"], fontSize=22, leading=26, alignment=TA_LEFT, textColor=colors.HexColor("#0f172a")),
        "h1": ParagraphStyle("h1", parent=base["Heading1"], fontSize=15, spaceBefore=14, spaceAfter=6, textColor=colors.HexColor("#0f172a")),
        "h2": ParagraphStyle("h2", parent=base["Heading2"], fontSize=11.5, spaceBefore=8, spaceAfter=3, textColor=colors.HexColor("#1e293b")),
        "body": ParagraphStyle("b", parent=base["BodyText"], fontSize=9, leading=12),
        "small": ParagraphStyle("s", parent=base["BodyText"], fontSize=7.5, leading=9.5, textColor=colors.HexColor("#475569")),
        "banner": ParagraphStyle("bn", parent=base["BodyText"], fontSize=9, leading=12, textColor=colors.HexColor("#92400e"),
                                 backColor=colors.HexColor("#fef3c7"), borderPadding=6),
    }


def _kv_table(rows: list[tuple[str, str]], st: dict[str, ParagraphStyle]) -> Table:
    data = [[Paragraph(f"<b>{_safe(k)}</b>", st["body"]), Paragraph(_safe(v), st["body"])] for k, v in rows]
    t = Table(data, colWidths=[42 * mm, 128 * mm])
    t.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LINEBELOW", (0, 0), (-1, -1), 0.25, colors.HexColor("#e2e8f0"))]))
    return t


def _finding_block(f: ReportFinding, st: dict[str, ParagraphStyle]) -> KeepTogether:
    color = SEV_COLORS.get(f.severity, colors.black)
    head = Paragraph(
        f'<font color="{color.hexval()}"><b>[{f.severity}]</b></font> <b>{_safe(f.title)}</b> '
        f'<font size="7.5" color="#475569">({f.finding_id} | {f.evidence_level} | {f.status})</font>', st["h2"])
    rows: list[tuple[str, str]] = [
        ("Domain / agents", f"{f.domain} (contributors: {', '.join(f.contributing_agents) or f.domain}); moderator: {f.moderator_status}"),
        ("Explanation", f.description),
    ]
    if f.scenario:
        rows.append(("Scenario", f.scenario))
    if f.impact:
        rows.append(("Impact", f.impact))
    rows.append(("Recommendation", f.recommendation))
    if f.affected_components:
        rows.append(("Components", ", ".join(f.affected_components[:8])))
    if f.source_references:
        rows.append(("Evidence", "\n".join("- " + r.short() + (f': "{r.excerpt[:110]}"' if r.excerpt else "") for r in f.source_references[:5])))
    if f.principle:
        rows.append(("Principle / control", f"{f.principle} [{f.principle_origin}]"))
    if f.moderator_notes:
        rows.append(("Moderator", f.moderator_notes))
    if f.architect_decisions:
        rows.append(("Architect decision", "\n".join(
            f"{d['decision_type']} ({d['created_at'][:10]}): {d.get('justification') or d.get('notes') or ''}" for d in f.architect_decisions)))
    for reb in f.rebuttals:
        rows.append(("Critic rebuttal", f"{reb.get('stance', '')} / {reb.get('outcome', '')}: {reb.get('argument', '')} "
                                        f"Moderator: {reb.get('moderator_determination', '')}"))
    rows.append(("Remaining risk", f.remaining_risk))
    if f.simulated:
        rows.append(("Provenance", "SIMULATED finding generated from a demo fixture."))
    return KeepTogether([head, _kv_table(rows, st), Spacer(1, 4)])


def build_pdf(report: AssessmentReport, path: Path) -> int:
    st = _styles()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".pdf.part")

    def footer(canvas, doc):  # type: ignore[no-untyped-def]
        canvas.saveState()
        canvas.setFont("Helvetica", 7.5)
        canvas.setFillColor(colors.HexColor("#64748b"))
        canvas.drawString(18 * mm, 10 * mm, f"Architecture Red Team - {report.architecture_name} - review {report.review_id}"
                          + (" - SIMULATED DEMO" if report.simulated else ""))
        canvas.drawRightString(A4[0] - 18 * mm, 10 * mm, f"Page {doc.page}")
        canvas.restoreState()

    doc = SimpleDocTemplate(str(tmp), pagesize=A4, leftMargin=18 * mm, rightMargin=18 * mm, topMargin=16 * mm, bottomMargin=18 * mm,
                            title=f"Architecture Review - {report.architecture_name}", author="Architecture Red Team")
    s: list = []
    s.append(Paragraph("Architecture Red Team - Adversarial Design Review", st["title"]))
    s.append(Paragraph(_safe(report.architecture_name), st["h1"]))
    if report.simulated:
        s.append(Paragraph("<b>SIMULATED DEMO MODE</b> - findings in this report were produced from deterministic demo fixtures through the real "
                           "workflow. They are not model-discovered results.", st["banner"]))
        s.append(Spacer(1, 6))
    es = report.executive_summary
    s.append(_kv_table([
        ("Project", report.project_name), ("Review date", es.get("review_date", "")), ("Architecture revision", str(report.architecture_revision)),
        ("Review status", report.review_status), ("Provider / model", f"{report.provider} / {report.model_id}"),
        ("Overall assessment", es.get("overall_assessment", "")), ("Review coverage", es.get("review_coverage", "")),
    ], st))
    s.append(Paragraph("1. Executive summary", st["h1"]))
    s.append(Paragraph(_safe(es.get("narrative", "")), st["body"]))
    if es.get("major_risks"):
        s.append(Paragraph("Major risks", st["h2"]))
        for r in es["major_risks"]:
            s.append(Paragraph(f"- <b>[{_safe(r['severity'])}]</b> {_safe(r['title'])} <font size='7.5'>({_safe(r['status'])})</font>", st["body"]))
    if es.get("clarifications"):
        s.append(Paragraph("Areas requiring clarification", st["h2"]))
        for c in es["clarifications"][:10]:
            s.append(Paragraph("- " + _safe(c), st["body"]))
    s.append(Paragraph("2. Scorecard", st["h1"]))
    rows = [["Domain", "Score*", "Confirmed-only", "Band", "Coverage", "Open", "Unverified"]]
    for d in report.scorecard.domains:
        rows.append([d.label, str(d.score) if d.coverage != "NONE" else "n/a", str(d.confirmed_only_score) if d.coverage != "NONE" else "n/a",
                     d.band, d.coverage, str(d.open_findings), str(d.unverified_findings)])
    t = Table(rows, colWidths=[32 * mm, 18 * mm, 28 * mm, 34 * mm, 24 * mm, 16 * mm, 20 * mm])
    ts = TableStyle([("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#0f172a")), ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                     ("FONTSIZE", (0, 0), (-1, -1), 8.5), ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#cbd5e1")),
                     ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f8fafc")])])
    for i, d in enumerate(report.scorecard.domains, start=1):
        ts.add("TEXTCOLOR", (3, i), (3, i), colors.HexColor(BAND_COLORS.get(d.band, "#111827")))
    t.setStyle(ts)
    s.append(t)
    s.append(Paragraph("* " + _safe(report.scorecard.methodology), st["small"]))
    s.append(Paragraph(_safe(report.scorecard.overall_statement), st["body"]))
    for d in report.scorecard.domains:
        s.append(Paragraph(f"<b>{_safe(d.label)}</b>: {_safe(d.explanation)}", st["small"]))
    s.append(Paragraph("3. Findings", st["h1"]))
    by_domain: dict[str, list[ReportFinding]] = {}
    for f in report.findings:
        by_domain.setdefault(f.domain, []).append(f)
    for domain in ("security", "resilience", "finops", "compliance", "performance", "operability"):
        items = by_domain.get(domain, [])
        s.append(Paragraph(f"3.{['security','resilience','finops','compliance','performance','operability'].index(domain) + 1} {domain.capitalize()} "
                           f"({len(items)} finding(s))", st["h2"]))
        if not items:
            s.append(Paragraph("No findings in this domain.", st["small"]))
        for f in items:
            s.append(_finding_block(f, st))
    s.append(PageBreak())
    s.append(Paragraph("4. Architect decision history", st["h1"]))
    if report.decisions:
        for d in report.decisions:
            s.append(Paragraph(f"<b>{_safe(d['decision_type'])}</b> on {_safe(d['finding_id'])} - {_safe(d['created_at'][:19])} - "
                               f"{_safe(d.get('justification') or d.get('notes') or '')}", st["body"]))
    else:
        s.append(Paragraph("No architect decisions were recorded.", st["body"]))
    s.append(Paragraph("5. Architecture decision records", st["h1"]))
    for a in report.adrs or []:
        s.append(Paragraph(f"<b>ADR-{a['number']:03d}</b> {_safe(a['title'])} - {_safe(a['status'])}; related: {_safe(', '.join(a['related_finding_ids']))}",
                           st["body"]))
    if not report.adrs:
        s.append(Paragraph("No ADR drafts were generated.", st["body"]))
    s.append(Paragraph("6. Recommendations", st["h1"]))
    for r in es.get("recommendations", []):
        s.append(Paragraph("- " + _safe(r), st["body"]))
    s.append(Paragraph("7. Review history and coverage", st["h1"]))
    hist = report.review_history
    s.append(Paragraph(_safe(f"Rounds: {hist.get('rounds')}. Revisions: {len(hist.get('revisions', []))}. "
                             f"Events recorded: {hist.get('event_count')}."), st["body"]))
    for rev in hist.get("revisions", []):
        s.append(Paragraph(f"Revision {rev['number']} ({_safe(rev['source'])}): {_safe(rev.get('change_notes', ''))[:240]} "
                           f"{_safe(rev.get('impact_summary', ''))}", st["small"]))
    cov = report.coverage
    for ag in cov.get("agents", []):
        s.append(Paragraph(f"{_safe(ag['agent_id'])}: {_safe(ag['status'])}, {ag['findings']} finding(s), tools {ag['tool_count']} "
                           f"({ag['tool_violations']} violations){' - ' + _safe(ag['error']) if ag.get('error') else ''}", st["small"]))
    for tool in cov.get("tools", []):
        s.append(Paragraph(f"Tool {_safe(tool['tool_name'])}: {_safe(tool['status'])} {_safe(tool.get('detail', ''))}", st["small"]))
    s.append(Paragraph("8. Limitations and caveats", st["h1"]))
    for item in report.limitations + report.disclaimers:
        s.append(Paragraph("- " + _safe(item), st["body"]))
    doc.build(s, onFirstPage=footer, onLaterPages=footer)
    os.replace(tmp, path)
    return path.stat().st_size
