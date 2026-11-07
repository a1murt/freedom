"""PDF card for a single evaluation (reportlab).

Page 1: header (conversation, channel, language, operator, checklist version, provider/model, date),
total score and gross-error flag, one row per criterion (code, title, answer, points/max, weight,
earned, explanation + evidence quotes with turn index). Page 2+: the full transcript
(idx, speaker, timestamp, text; bot/system turns in grey).

Cyrillic: DejaVuSans / DejaVuSans-Bold from ``cqa/export/fonts`` are registered with reportlab.
If they are missing, the macOS "Arial Unicode" font is used as a development fallback (logged).
"""
from __future__ import annotations

import io
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
from sqlalchemy.orm import Session

from ..models import ChecklistVersion, Criterion, Evaluation, EvaluationCriterion, Turn

log = logging.getLogger(__name__)

FONTS_DIR = Path(__file__).resolve().parent / "fonts"
DEV_FALLBACK_FONT = Path("/System/Library/Fonts/Supplemental/Arial Unicode.ttf")
PAGE_WIDTH = A4[0] - 2 * 15 * mm

SPEAKER_LABELS = {"client": "Клиент", "operator": "Оператор", "bot": "Бот", "system": "Система"}
GREY_SPEAKERS = {"bot", "system"}

_fonts: tuple[str, str] | None = None


class EvaluationNotFound(LookupError):
    pass


def register_fonts() -> tuple[str, str]:
    """Register the Unicode fonts once; returns (regular, bold) font names."""
    global _fonts
    if _fonts is not None:
        return _fonts
    regular, bold = FONTS_DIR / "DejaVuSans.ttf", FONTS_DIR / "DejaVuSans-Bold.ttf"
    if regular.exists():
        pdfmetrics.registerFont(TTFont("DejaVuSans", str(regular)))
        bold_name = "DejaVuSans"
        if bold.exists():
            pdfmetrics.registerFont(TTFont("DejaVuSans-Bold", str(bold)))
            bold_name = "DejaVuSans-Bold"
        pdfmetrics.registerFontFamily("DejaVuSans", normal="DejaVuSans", bold=bold_name, italic="DejaVuSans", boldItalic=bold_name)
        _fonts = ("DejaVuSans", bold_name)
    elif DEV_FALLBACK_FONT.exists():
        log.warning("DejaVu fonts not found in %s; using dev fallback %s", FONTS_DIR, DEV_FALLBACK_FONT)
        pdfmetrics.registerFont(TTFont("ArialUnicode", str(DEV_FALLBACK_FONT)))
        pdfmetrics.registerFontFamily("ArialUnicode", normal="ArialUnicode", bold="ArialUnicode", italic="ArialUnicode", boldItalic="ArialUnicode")
        _fonts = ("ArialUnicode", "ArialUnicode")
    else:
        log.warning("No Unicode TTF font available; Cyrillic text will not render in PDFs")
        _fonts = ("Helvetica", "Helvetica-Bold")
    return _fonts


def _styles(regular: str, bold: str) -> dict[str, ParagraphStyle]:
    base = ParagraphStyle("base", fontName=regular, fontSize=8.5, leading=11, alignment=TA_LEFT)
    return {
        "base": base,
        "title": ParagraphStyle("title", parent=base, fontName=bold, fontSize=15, leading=19, spaceAfter=4),
        "h2": ParagraphStyle("h2", parent=base, fontName=bold, fontSize=11, leading=14, spaceBefore=6, spaceAfter=4),
        "label": ParagraphStyle("label", parent=base, fontName=bold, textColor=colors.HexColor("#444444")),
        "cell": ParagraphStyle("cell", parent=base, fontSize=8, leading=10),
        "cell_bold": ParagraphStyle("cell_bold", parent=base, fontName=bold, fontSize=8, leading=10),
        "cell_grey": ParagraphStyle("cell_grey", parent=base, fontSize=8, leading=10, textColor=colors.HexColor("#808080")),
        "evidence": ParagraphStyle("evidence", parent=base, fontSize=7.2, leading=9, textColor=colors.HexColor("#555555"), leftIndent=4),
        "score": ParagraphStyle("score", parent=base, fontName=bold, fontSize=12, leading=15),
        "flag_bad": ParagraphStyle("flag_bad", parent=base, fontName=bold, fontSize=10, leading=13, textColor=colors.HexColor("#B00020")),
        "flag_ok": ParagraphStyle("flag_ok", parent=base, fontName=bold, fontSize=10, leading=13, textColor=colors.HexColor("#2E7D32")),
    }


def _p(text: Any, style: ParagraphStyle) -> Paragraph:
    s = "" if text is None else str(text)
    return Paragraph(escape(s).replace("\n", "<br/>"), style)


def _fmt_dt(dt: datetime | None) -> str:
    if dt is None:
        return "—"
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC).strftime("%Y-%m-%d %H:%M UTC")


def _fmt_num(value: float | None, digits: int = 2) -> str:
    if value is None:
        return "—"
    f = float(value)
    if f.is_integer():
        return str(int(f))
    return f"{f:.{digits}f}".rstrip("0").rstrip(".")


def _answer_label(crit: Criterion | None, answer: Any) -> str:
    if answer is None:
        return "—"
    options = (crit.options if crit is not None else None) or []
    for opt in options:
        val = opt.get("value")
        if val == answer or str(val).lower() == str(answer).lower() or str(opt.get("label", "")).lower() == str(answer).lower():
            label = opt.get("label")
            return str(label) if label is not None else str(val)
    if isinstance(answer, bool):
        return "Да" if answer else "Нет"
    low = str(answer).strip().lower()
    if low == "yes":
        return "Да"
    if low == "no":
        return "Нет"
    return _fmt_num(answer) if isinstance(answer, (int, float)) else str(answer)


def _criteria_table(ev: Evaluation, crit_by_code: dict[str, Criterion], st: dict[str, ParagraphStyle]) -> Table:
    rows = sorted(
        ev.criteria,
        key=lambda r: (crit_by_code[r.code].order if r.code in crit_by_code else 10**6, r.id),
    )
    data: list[list[Any]] = [
        [
            _p("Код", st["cell_bold"]),
            _p("Критерий", st["cell_bold"]),
            _p("Ответ", st["cell_bold"]),
            _p("Баллы", st["cell_bold"]),
            _p("Вес", st["cell_bold"]),
            _p("Зачтено", st["cell_bold"]),
            _p("Обоснование и доказательства", st["cell_bold"]),
        ]
    ]
    for r in rows:
        crit = crit_by_code.get(r.code)
        title = crit.title if crit is not None else r.code
        data.append(
            [
                _p(r.code, st["cell_bold"]),
                _p(title, st["cell"]),
                _answer_paragraph(r, crit, st),
                _p("—" if not r.applicable else f"{_fmt_num(r.score)} / {_fmt_num(r.max_points)}", st["cell"]),
                _p(_fmt_num(r.weight), st["cell"]),
                _earned_paragraph(r, st),
                _explanation_paragraph(r, st),
            ]
        )
    widths = [11 * mm, 30 * mm, 22 * mm, 16 * mm, 11 * mm, 18 * mm]
    widths.append(PAGE_WIDTH - sum(widths))
    table = Table(data, colWidths=widths, repeatRows=1)
    table.setStyle(
        TableStyle(
            [
                ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#BBBBBB")),
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#EDEDED")),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 3),
                ("RIGHTPADDING", (0, 0), (-1, -1), 3),
                ("TOPPADDING", (0, 0), (-1, -1), 2),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
            ]
        )
    )
    return table


def _answer_paragraph(r: EvaluationCriterion, crit: Criterion | None, st: dict[str, ParagraphStyle]) -> Paragraph:
    if not r.applicable:
        text = "N/A"
        if r.na_reason:
            text += f"<br/><font size='7' color='#555555'>{escape(str(r.na_reason))}</font>"
        return Paragraph(text, st["cell"])
    text = escape(_answer_label(crit, r.answer))
    if r.review_state == "changed" and r.final_answer is not None:
        text += f"<br/><font color='#B00020'>→ {escape(_answer_label(crit, r.final_answer))}</font><br/><font size='7' color='#555555'>(решение ревьюера)</font>"
    elif r.review_state == "confirmed":
        text += "<br/><font size='7' color='#2E7D32'>подтверждено</font>"
    return Paragraph(text, st["cell"])


def _earned_paragraph(r: EvaluationCriterion, st: dict[str, ParagraphStyle]) -> Paragraph:
    if not r.applicable:
        return _p("—", st["cell"])
    text = f"{_fmt_num(r.earned)} / {_fmt_num(r.weight_applied)}"
    if r.review_state == "changed" and r.final_earned is not None:
        text += f"<br/><font color='#B00020'>→ {_fmt_num(r.final_earned)}</font>"
    return Paragraph(text, st["cell"])


def _explanation_paragraph(r: EvaluationCriterion, st: dict[str, ParagraphStyle]) -> list[Paragraph]:
    out: list[Paragraph] = [_p(r.explanation or "—", st["cell"])]
    for ev in r.evidence or []:
        if not isinstance(ev, dict):
            continue
        idx = ev.get("turn_idx")
        speaker = SPEAKER_LABELS.get(str(ev.get("speaker") or ""), str(ev.get("speaker") or ""))
        quote = str(ev.get("quote") or "").strip()
        role = ev.get("role")
        head = f"#{idx}" if idx is not None else "#?"
        if speaker:
            head += f" {speaker}"
        if role and role != "supports":
            head += f" ({role})"
        out.append(Paragraph(f"{escape(head)}: «{escape(quote)}»", st["evidence"]))
    return out


def _transcript_table(turns: list[Turn], st: dict[str, ParagraphStyle]) -> Table:
    data: list[list[Any]] = [
        [_p("#", st["cell_bold"]), _p("Спикер", st["cell_bold"]), _p("Время", st["cell_bold"]), _p("Текст", st["cell_bold"])]
    ]
    for t in turns:
        style = st["cell_grey"] if t.speaker in GREY_SPEAKERS else st["cell"]
        label = SPEAKER_LABELS.get(t.speaker, t.speaker)
        data.append([_p(t.idx, style), _p(label, style), _p(t.timestamp_raw, style), _p(t.text, style)])
    widths = [9 * mm, 22 * mm, 22 * mm]
    widths.append(PAGE_WIDTH - sum(widths))
    table = Table(data, colWidths=widths, repeatRows=1)
    table.setStyle(
        TableStyle(
            [
                ("GRID", (0, 0), (-1, -1), 0.3, colors.HexColor("#CCCCCC")),
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#EDEDED")),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 3),
                ("RIGHTPADDING", (0, 0), (-1, -1), 3),
                ("TOPPADDING", (0, 0), (-1, -1), 2),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
            ]
        )
    )
    return table


def evaluation_pdf(db: Session, evaluation_id: int) -> bytes:
    ev = db.get(Evaluation, evaluation_id)
    if ev is None:
        raise EvaluationNotFound(f"evaluation {evaluation_id} not found")
    conv = ev.conversation
    version: ChecklistVersion | None = db.get(ChecklistVersion, ev.checklist_version_id)
    checklist_name = version.checklist.name if version is not None and version.checklist is not None else "—"
    crit_by_code = {c.code: c for c in (version.criteria if version is not None else [])}

    regular, bold = register_fonts()
    st = _styles(regular, bold)
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=A4,
        leftMargin=15 * mm,
        rightMargin=15 * mm,
        topMargin=14 * mm,
        bottomMargin=14 * mm,
        title=f"Оценка {ev.id} · {ev.conversation_id}",
        author="CQA",
    )

    story: list[Any] = [_p(f"Оценка диалога {ev.conversation_id}", st["title"])]

    version_text = f"{checklist_name} · v{version.version_no} (id {version.id})" if version is not None else str(ev.checklist_version_id)
    header = [
        [_p("Диалог", st["label"]), _p(ev.conversation_id, st["base"]), _p("Канал", st["label"]), _p(conv.channel if conv else "—", st["base"])],
        [_p("Язык", st["label"]), _p(conv.language if conv else "—", st["base"]), _p("Оператор", st["label"]), _p((conv.operator_name if conv else None) or "—", st["base"])],
        [_p("Чек-лист", st["label"]), _p(version_text, st["base"]), _p("Провайдер / модель", st["label"]), _p(f"{ev.provider} / {ev.model or '—'}", st["base"])],
        [_p("Дата оценки", st["label"]), _p(_fmt_dt(ev.completed_at or ev.created_at), st["base"]), _p("Оценка №", st["label"]), _p(f"{ev.id} ({ev.mode})", st["base"])],
    ]
    header_table = Table(header, colWidths=[26 * mm, 64 * mm, 34 * mm, PAGE_WIDTH - 124 * mm])
    header_table.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LINEBELOW", (0, -1), (-1, -1), 0.5, colors.HexColor("#999999")),
                ("LEFTPADDING", (0, 0), (-1, -1), 2),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
            ]
        )
    )
    story += [header_table, Spacer(1, 5 * mm)]

    total_text = f"Итоговый балл: {_fmt_num(ev.total_score)} / 100"
    if ev.final_total_score is not None and ev.final_total_score != ev.total_score:
        total_text += f"  →  после ревью: {_fmt_num(ev.final_total_score)}"
    story.append(_p(total_text, st["score"]))
    if ev.critical_error:
        story.append(_p("Грубая ошибка: ДА", st["flag_bad"]))
    else:
        story.append(_p("Грубая ошибка: нет", st["flag_ok"]))
    story += [Spacer(1, 4 * mm), _p("Критерии", st["h2"]), _criteria_table(ev, crit_by_code, st)]

    story += [PageBreak(), _p("Транскрипт", st["h2"])]
    turns = sorted(conv.turns, key=lambda t: t.idx) if conv is not None else []
    if turns:
        story.append(_transcript_table(turns, st))
    else:
        story.append(_p("Транскрипт отсутствует.", st["cell_grey"]))

    doc.build(story)
    return buf.getvalue()
