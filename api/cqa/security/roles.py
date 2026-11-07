"""Roles, permissions and the write-route map: the source of truth for docs/ARCH_CONTRACTS.md §1–§2.

There is no login. The UI sends the chosen role and the person's name in headers:

    X-CQA-Role: operator                              no header (or empty) → role "demo" with every permission
    X-CQA-User: %D0%90%D0%BB%D0%B8%D0%BD%D0%B0        URL-encoded UTF-8 (encodeURIComponent); raw UTF-8 is accepted too
    X-Request-ID: any short id                        optional correlation id (the platform request log assigns one)

`cqa.security.middleware` resolves the actor of every /api request, answers 400 `unknown_role` for an unknown role,
checks the ROUTE_MAP permission of every write request (403 `forbidden`) and records the audit trail.
Handlers read the actor with `current_actor(request)` (= `request.state.actor`).
"""
from __future__ import annotations

import re
import secrets
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import unquote

from fastapi import HTTPException, Request

ROLE_HEADER = "X-CQA-Role"
USER_HEADER = "X-CQA-User"
REQUEST_ID_HEADER = "X-Request-ID"
DEFAULT_ROLE = "demo"
SYSTEM_ROLE = "system"  # actor_role of audit events written by background services (no request)
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
WRITE_METHODS = ("POST", "PUT", "PATCH", "DELETE")
NAME_MAX = 128  # appeals.author_name / audit_events.actor_name
REQUEST_ID_MAX = 64  # same limit as cqa.platform.requestlog and audit_events.request_id (VARCHAR 64)
LANGS = ("ru", "en", "kk")


def _l(ru: str, en: str, kk: str) -> dict[str, str]:
    return {"ru": ru, "en": en, "kk": kk}


# ----------------------------------------------------------------------------- permissions

PERMISSIONS: dict[str, dict[str, Any]] = {
    "read": {
        "label": _l("Просмотр", "View", "Қарау"),
        "description": _l("Все GET-запросы: диалоги, оценки, аналитика, чек-листы, журнал действий.",
                          "Every GET request: conversations, scores, analytics, checklists, audit log.",
                          "Барлық GET сұраулары: диалогтар, бағалар, аналитика, чек-листтер, әрекеттер журналы."),
    },
    "conversations.import": {
        "label": _l("Импорт диалогов", "Import conversations", "Диалогтарды импорттау"),
        "description": _l("Импорт выборки по group_id, загрузка файла, вставка транскрипта.",
                          "Import by group_id, file upload, transcript paste.",
                          "group_id бойынша импорт, файл жүктеу, транскриптті қою."),
    },
    "evaluations.run": {
        "label": _l("Запуск оценки", "Run evaluations", "Бағалауды іске қосу"),
        "description": _l("Запуск, расчёт стоимости, остановка и возобновление оценки.",
                          "Start, estimate, cancel and resume evaluations.",
                          "Бағалауды іске қосу, құнын есептеу, тоқтату және жалғастыру."),
    },
    "appeals.create": {
        "label": _l("Подача апелляций", "File appeals", "Апелляция беру"),
        "description": _l("Апелляция на оценку критерия; оператор — только по своим диалогам.",
                          "Appeal a criterion score; an operator only for own conversations.",
                          "Критерий бағасына апелляция; оператор — тек өз диалогтары бойынша."),
    },
    "appeals.decide": {
        "label": _l("Решения по апелляциям", "Decide appeals", "Апелляциялар бойынша шешім"),
        "description": _l("Подтвердить или изменить ответ с пересчётом итога.",
                          "Confirm or change the answer; the total is recomputed.",
                          "Жауапты растау немесе өзгерту; жиын қайта есептеледі."),
    },
    "checklists.write": {
        "label": _l("Редактирование чек-листов", "Edit checklists", "Чек-листтерді өңдеу"),
        "description": _l("Создание чек-листов и версий, активация, пересборка спецификаций, тест критерия.",
                          "Create checklists and versions, activate, regenerate judge specs, test a criterion.",
                          "Чек-листтер мен нұсқаларды құру, белсендіру, спецификацияларды қайта құру, критерийді тексеру."),
    },
    "settings.write": {
        "label": _l("Изменение настроек", "Change settings", "Баптауларды өзгерту"),
        "description": _l("Провайдер и модель оценки, маскирование ПДн, оформление и другие настройки.",
                          "Judge provider and model, PII masking, appearance and other settings.",
                          "Бағалау провайдері мен моделі, дербес деректерді жасыру, безендіру және басқа баптаулар."),
    },
    "calibration.write": {
        "label": _l("Эталонная разметка", "Gold labels", "Эталондық белгілеу"),
        "description": _l("Эталонные метки для калибровки и согласия ИИ с экспертами.",
                          "Gold labels for calibration and AI-vs-expert agreement.",
                          "Калибрлеу және ЖИ мен сарапшылар келісімі үшін эталондық белгілер."),
    },
    "admin": {
        "label": _l("Администрирование", "Administration", "Әкімшілік"),
        "description": _l("Снимки и восстановление базы, выгрузка журнала, настройка интеграций.",
                          "Database snapshots and restore, audit export, integration configuration.",
                          "Дерекқор көшірмелері мен қалпына келтіру, журналды экспорттау, интеграцияларды баптау."),
    },
    "integrations.ingest": {
        "label": _l("Приём из интеграций", "Integration ingest", "Интеграциялардан қабылдау"),
        "description": _l("Приём транскриптов из Dynamics 365.", "Dynamics 365 transcript push.",
                          "Dynamics 365-тен транскрипттерді қабылдау."),
    },
}
ALL_PERMISSIONS: tuple[str, ...] = tuple(PERMISSIONS)

# ----------------------------------------------------------------------------- navigation hints for the UI

NAV_ITEMS: dict[str, dict[str, Any]] = {
    "home": {"route": "#/", "label": _l("Главная", "Home", "Басты бет")},
    "my": {"route": "#/my", "label": _l("Мои диалоги", "My conversations", "Менің диалогтарым")},
    "overview": {"route": "#/overview", "label": _l("Обзор", "Overview", "Шолу")},
    "conversations": {"route": "#/conversations", "label": _l("Диалоги", "Conversations", "Диалогтар")},
    "violations": {"route": "#/violations", "label": _l("Грубые нарушения", "Gross violations", "Өрескел бұзушылықтар")},
    "review": {"route": "#/review", "label": _l("Очередь ревью", "Review Queue", "Ревью кезегі")},
    "operators": {"route": "#/operators", "label": _l("Операторы", "Operators", "Операторлар")},
    "analytics": {"route": "#/analytics", "label": _l("Аналитика", "Analytics", "Аналитика")},
    "checklists": {"route": "#/checklists", "label": _l("Чек-листы", "Checklists", "Чек-листтер")},
    "calibration": {"route": "#/calibration", "label": _l("Калибровка", "Calibration", "Калибрлеу")},
    "audit": {"route": "#/audit", "label": _l("Журнал действий", "Audit log", "Әрекеттер журналы")},
    "settings": {"route": "#/settings", "label": _l("Настройки", "Settings", "Баптаулар")},
    "admin": {"route": "#/admin", "label": _l("Администрирование", "Administration", "Әкімшілік")},
}

# ----------------------------------------------------------------------------- roles

ROLES: dict[str, dict[str, Any]] = {
    "demo": {
        "label": _l("Демо-режим", "Demo mode", "Демо режимі"),
        "description": _l("Роль не выбрана: доступны все действия (обратная совместимость и демонстрация).",
                          "No role selected: every action is allowed (backward compatibility and demos).",
                          "Рөл таңдалмаған: барлық әрекеттер қолжетімді (кері үйлесімділік және демонстрация)."),
        "permissions": ALL_PERMISSIONS,
        "home": "#/conversations",
        "nav": ("conversations", "overview", "violations", "review", "operators", "analytics", "checklists", "calibration",
                "audit", "settings", "admin"),
    },
    "visitor": {
        "label": _l("Гость", "Visitor", "Қонақ"),
        "description": _l("Только просмотр: диалоги, оценки и аналитика; изменить ничего нельзя.",
                          "Read-only: conversations, scores and analytics; nothing can be changed.",
                          "Тек қарау: диалогтар, бағалар және аналитика; ештеңені өзгертуге болмайды."),
        "permissions": ("read",),
        "home": "#/",
        "nav": ("home", "overview", "conversations", "violations", "analytics"),
    },
    "operator": {
        "label": _l("Оператор", "Operator", "Оператор"),
        "description": _l("Видит свои диалоги и оценки, оспаривает оценки только своих диалогов.",
                          "Sees own conversations and scores; appeals only own conversations.",
                          "Өз диалогтары мен бағаларын көреді, тек өз диалогтарының бағаларына апелляция береді."),
        "permissions": ("read", "appeals.create"),
        "home": "#/my",
        "nav": ("my", "checklists"),
        "constraints": {"appeals.create": "own_dialogs"},
    },
    "analyst": {
        "label": _l("QA-аналитик", "QA analyst", "QA-талдаушы"),
        "description": _l("Импортирует диалоги, запускает оценку, подаёт апелляции.",
                          "Imports conversations, runs evaluations, files appeals.",
                          "Диалогтарды импорттайды, бағалауды іске қосады, апелляция береді."),
        "permissions": ("read", "conversations.import", "evaluations.run", "appeals.create", "integrations.ingest"),
        "home": "#/conversations",
        "nav": ("conversations", "violations", "review", "operators", "analytics", "checklists"),
    },
    "reviewer": {
        "label": _l("Ревьюер", "Reviewer", "Ревьюер"),
        "description": _l("Разбирает апелляции и принимает решения, ведёт эталонную разметку.",
                          "Resolves appeals and makes decisions, maintains gold labels.",
                          "Апелляцияларды қарап, шешім қабылдайды, эталондық белгілеуді жүргізеді."),
        "permissions": ("read", "evaluations.run", "appeals.create", "appeals.decide", "calibration.write"),
        "home": "#/review",
        "nav": ("review", "conversations", "violations", "calibration", "analytics", "checklists"),
    },
    "lead": {
        "label": _l("Руководитель группы", "Team lead", "Топ жетекшісі"),
        "description": _l("Ведёт группу: всё, кроме системных настроек и администрирования.",
                          "Runs the team: everything except system settings and administration.",
                          "Топты басқарады: жүйелік баптаулар мен әкімшіліктен басқасының бәрі."),
        "permissions": ("read", "conversations.import", "evaluations.run", "appeals.create", "appeals.decide",
                        "checklists.write", "calibration.write", "integrations.ingest"),
        "home": "#/overview",
        "nav": ("overview", "conversations", "violations", "review", "operators", "analytics", "checklists", "calibration",
                "audit"),
    },
    "director": {
        "label": _l("Директор", "Director", "Директор"),
        "description": _l("Ключевые показатели и аналитика, только просмотр.",
                          "Executive KPIs and analytics, read-only.",
                          "Негізгі көрсеткіштер мен аналитика, тек қарау."),
        "permissions": ("read",),
        "home": "#/overview",
        "nav": ("overview", "analytics", "violations", "operators"),
    },
    "methodologist": {
        "label": _l("Методолог", "Methodologist", "Әдіскер"),
        "description": _l("Ведёт чек-листы и критерии, эталонную разметку, импорт и оценку.",
                          "Owns checklists and criteria, gold labels, imports and evaluations.",
                          "Чек-листтер мен критерийлерді, эталондық белгілеуді, импорт пен бағалауды жүргізеді."),
        "permissions": ("read", "conversations.import", "evaluations.run", "checklists.write", "calibration.write"),
        "home": "#/checklists",
        "nav": ("checklists", "calibration", "conversations", "violations", "analytics"),
    },
    "admin": {
        "label": _l("Администратор", "Administrator", "Әкімші"),
        "description": _l("Все действия, включая настройки, снимки базы и интеграции.",
                          "Everything, including settings, database snapshots and integrations.",
                          "Барлық әрекеттер, соның ішінде баптаулар, дерекқор көшірмелері және интеграциялар."),
        "permissions": ALL_PERMISSIONS,
        "home": "#/settings",
        "nav": ("settings", "admin", "audit", "overview", "conversations", "violations", "review", "operators", "analytics",
                "checklists", "calibration"),
    },
}

_ROLE_PERMS: dict[str, frozenset[str]] = {code: frozenset(spec["permissions"]) for code, spec in ROLES.items()}

# ----------------------------------------------------------------------------- audit actions (labels for the audit log UI)

ACTIONS: dict[str, dict[str, Any]] = {
    "dataset.import": {"label": _l("Импорт выборки", "Dataset import", "Деректер жинағын импорттау"),
                       "summary": "Импорт выборки «{body.group_id}»: {response.imported} диалогов"},
    "conversation.upload": {"label": _l("Загрузка файла с диалогами", "Conversation file upload", "Диалогтар файлын жүктеу"),
                            "summary": "Загружен файл «{upload.filename}»"},
    "conversation.paste": {"label": _l("Добавление диалога вручную", "Conversation pasted", "Диалогты қолмен қосу"),
                           "summary": "Добавлен диалог {entity_id}"},
    "evaluation.run": {"label": _l("Запуск оценки", "Evaluation run", "Бағалауды іске қосу"),
                       "summary": "Запущена оценка: {response.queued} диалогов, задача {response.job_id}"},
    "evaluation.estimate": {"label": _l("Расчёт стоимости оценки", "Evaluation cost estimate", "Бағалау құнын есептеу"),
                            "summary": "Расчёт стоимости: {response.queued} диалогов ≈ ${response.estimated_cost_usd}"},
    "evaluation.update": {"label": _l("Изменение оценки", "Evaluation change", "Бағалауды өзгерту")},
    "job.cancel": {"label": _l("Остановка задачи", "Job cancelled", "Тапсырманы тоқтату"),
                   "summary": "Остановлена задача {path.job_id}"},
    "job.resume": {"label": _l("Возобновление задачи", "Job resumed", "Тапсырманы жалғастыру"),
                   "summary": "Возобновлена задача {path.job_id}: {response.queued} диалогов, новая задача {response.job_id}"},
    "job.update": {"label": _l("Управление задачей", "Job control", "Тапсырманы басқару")},
    "job.completed": {"label": _l("Задача завершена", "Job completed", "Тапсырма аяқталды")},
    "job.interrupted": {"label": _l("Задача прервана перезапуском", "Job interrupted by restart",
                                    "Тапсырма қайта іске қосумен үзілді")},
    "checklist.create": {"label": _l("Создание чек-листа", "Checklist created", "Чек-лист құру"),
                         "summary": "Создан чек-лист «{body.name}» (#{response.checklist_id}, версия {response.version_no})"},
    "checklist.import": {"label": _l("Импорт чек-листа", "Checklist imported", "Чек-листті импорттау"),
                         "summary": "Импортирован чек-лист #{entity_id}"},
    "checklist.update": {"label": _l("Изменение чек-листа", "Checklist change", "Чек-листті өзгерту")},
    "checklist.delete": {"label": _l("Удаление чек-листа", "Checklist deleted", "Чек-листті жою")},
    "checklist.version.create": {"label": _l("Новая версия чек-листа", "Checklist version created", "Чек-листтің жаңа нұсқасы"),
                                 "summary": "Создана версия {response.version_no} чек-листа #{path.checklist_id}"},
    "checklist.version.activate": {"label": _l("Активация версии чек-листа", "Checklist version activated",
                                               "Чек-лист нұсқасын белсендіру"),
                                   "summary": "Активирована версия #{path.version_id} чек-листа #{path.checklist_id}"},
    "checklist.spec.regenerate": {"label": _l("Пересборка спецификации судьи", "Judge spec regenerated",
                                              "Төреші спецификациясын қайта құру"),
                                  "summary": "Пересобрана спецификация критерия {path.code} (версия #{path.version_id})"},
    "checklist.criterion.test": {"label": _l("Проверка критерия на диалоге", "Criterion test run", "Критерийді диалогта тексеру"),
                                 "summary": "Проверка критерия на диалоге {body.conversation_id}"},
    "appeal.create": {"label": _l("Подача апелляции", "Appeal filed", "Апелляция беру"),
                      "summary": "Апелляция #{response.id}: {response.code}, диалог {response.conversation_id}"},
    "appeal.decide": {"label": _l("Решение по апелляции", "Appeal decided", "Апелляция бойынша шешім"),
                      "summary": "Апелляция #{path.appeal_id} ({response.appeal.code}, диалог {response.appeal.conversation_id}): "
                                 "{decision}"},
    "appeal.preview": {"label": _l("Предпросмотр итога", "Total preview", "Жиынды алдын ала қарау")},
    "checklist.impact": {"label": _l("Предпросмотр влияния изменений", "Change impact preview", "Өзгерістер әсерін алдын ала қарау")},
    "conversation.operator.update": {"label": _l("Назначение оператора диалогу", "Dialog operator set", "Диалогқа оператор тағайындау"),
                                     "summary": "Диалог {path.conversation_id}: оператор «{response.operator_name}»"},
    "conversation.paste.preview": {"label": _l("Предпросмотр вставленного диалога", "Pasted dialog preview", "Қойылған диалогты алдын ала қарау"),
                                   "summary": "Предпросмотр диалога {response.conversation_id}: ничего не сохранено"},
    "appeal.withdraw": {"label": _l("Отзыв апелляции", "Appeal withdrawn", "Апелляцияны кері қайтару"),
                        "summary": "Апелляция #{path.appeal_id} ({response.code}, диалог {response.conversation_id}) отозвана"},
    "settings.update": {"label": _l("Изменение настроек", "Settings changed", "Баптауларды өзгерту"),
                        "summary": "Изменены настройки: {body_keys}"},
    "snapshot.create": {"label": _l("Снимок базы данных", "Database snapshot created", "Дерекқор көшірмесін жасау"),
                        "summary": "Создан снимок базы {entity_id}"},
    "snapshot.restore": {"label": _l("Восстановление из снимка", "Snapshot restored", "Көшірмеден қалпына келтіру"),
                         "summary": "База восстановлена из снимка {path.name}"},
    "snapshot.delete": {"label": _l("Удаление снимка", "Snapshot deleted", "Көшірмені жою"),
                        "summary": "Удалён снимок {path.name}"},
    "admin.write": {"label": _l("Административное действие", "Administrative action", "Әкімшілік әрекет")},
    "system.write": {"label": _l("Системное действие", "System action", "Жүйелік әрекет")},
    "snapshot.download": {"label": _l("Скачивание снимка базы", "Snapshot downloaded", "Дерекқор көшірмесін жүктеп алу")},
    "calibration.labels.upsert": {"label": _l("Сохранение эталонных меток", "Gold labels saved", "Эталондық белгілерді сақтау"),
                                  "summary": "Эталонные метки: создано {response.created}, обновлено {response.updated}, "
                                             "удалено {response.removed}"},
    "calibration.labels.import": {"label": _l("Импорт эталонных меток", "Gold labels imported",
                                              "Эталондық белгілерді импорттау"),
                                  "summary": "Импорт эталона «{response.labeler}»: диалогов {response.dialogs}, "
                                             "создано {response.created}, обновлено {response.updated}"},
    "calibration.labels.preview": {"label": _l("Проверка эталонных меток без сохранения", "Gold labels dry run",
                                               "Эталондық белгілерді сақтамай тексеру")},
    "calibration.labels.delete": {"label": _l("Удаление эталонных меток", "Gold labels deleted", "Эталондық белгілерді жою")},
    "calibration.update": {"label": _l("Изменение калибровки", "Calibration change", "Калибрлеуді өзгерту")},
    "integration.dynamics.ingest": {"label": _l("Приём транскриптов из Dynamics 365", "Dynamics 365 transcripts received",
                                                "Dynamics 365-тен транскрипттерді қабылдау"),
                                    "summary": "Транскрипт Dynamics 365: диалог {response.conversation_id}, реплик {response.turns}"},
    "integration.dynamics.ingest_evaluate": {"label": _l("Приём транскрипта из Dynamics 365 с оценкой",
                                                         "Dynamics 365 transcript received and evaluated",
                                                         "Dynamics 365 транскриптін қабылдау және бағалау"),
                                             "summary": "Транскрипт Dynamics 365: диалог {response.conversation_id}, "
                                                        "реплик {response.turns}, оценка {response.evaluation.status}"},
    "integration.config.update": {"label": _l("Настройка интеграции", "Integration configured", "Интеграцияны баптау")},
    "api.write": {"label": _l("Прочее изменение", "Other write", "Басқа өзгеріс"),
                  "summary": "{request.method} {request.path}"},
}

# ----------------------------------------------------------------------------- write-route map

_PARAM_RE = re.compile(r"\{(\w+)(?::(path))?\}")


def _compile(path: str) -> re.Pattern[str]:
    """`/api/appeals/{appeal_id}/decision` → regex with named groups (`{rest:path}` also matches slashes)."""
    out, pos = [], 0
    for m in _PARAM_RE.finditer(path):
        out.append(re.escape(path[pos:m.start()]))
        out.append(f"(?P<{m.group(1)}>{'.+' if m.group(2) else '[^/]+'})")
        pos = m.end()
    out.append(re.escape(path[pos:]))
    return re.compile("".join(out))


@dataclass(frozen=True)
class RouteRule:
    """One write route: who may call it and how the call is recorded in the audit trail.

    `entity` lists where the audited entity id comes from, first non-empty wins: `path:<param>`, `response:<field>`
    (dotted for nested fields), `body:<field>`; a `<type>=` prefix overrides `entity_type` for that source.
    `capture` names response fields copied into `details.response`. `refine(body)` may return a more specific action.
    """

    methods: tuple[str, ...]
    path: str
    permission: str
    action: str
    entity_type: str | None = None
    entity: tuple[str, ...] = ()
    capture: tuple[str, ...] = ()
    audit: bool = True
    refine: Callable[[Any], str | None] | None = field(default=None, compare=False)
    pattern: re.Pattern[str] = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "pattern", _compile(self.path))


def rule(methods: str | Iterable[str], path: str, permission: str, action: str, entity_type: str | None = None,
         entity: str | Iterable[str] = (), *, capture: Iterable[str] = (), audit: bool = True,
         refine: Callable[[Any], str | None] | None = None) -> RouteRule:
    if permission not in PERMISSIONS:
        raise ValueError(f"unknown permission {permission!r}")
    ms = (methods,) if isinstance(methods, str) else tuple(methods)
    ents = (entity,) if isinstance(entity, str) else tuple(entity)
    return RouteRule(tuple(m.upper() for m in ms), path, permission, action, entity_type, ents, tuple(capture), audit, refine)


def _truthy(value: Any) -> bool:
    return value is True or (isinstance(value, (str, int)) and str(value).strip().lower() in ("1", "true", "yes", "on"))


def _paste_preview(body: Any) -> str | None:
    return "conversation.paste.preview" if isinstance(body, dict) and _truthy(body.get("dry_run")) else None


def _dry_run(body: Any) -> str | None:
    return "evaluation.estimate" if isinstance(body, dict) and body.get("dry_run") else None


def _calibration_dry_run(body: Any, query: Mapping[str, str] | None = None) -> str | None:
    """PUT labels / import with `dry_run` (body field, or the import's ?dry_run=true) saves nothing: a preview event."""
    if (isinstance(body, dict) and _truthy(body.get("dry_run"))) or _truthy((query or {}).get("dry_run")):
        return "calibration.labels.preview"
    return None


def _dynamics_evaluate(body: Any) -> str | None:
    return "integration.dynamics.ingest_evaluate" if isinstance(body, dict) and _truthy(body.get("evaluate")) else None


W = WRITE_METHODS
# Ordered: the first rule whose method and path match wins. The last rule is the default for unknown writes.
ROUTE_MAP: list[RouteRule] = [
    # conversations and datasets
    rule("POST", "/api/datasets/import", "conversations.import", "dataset.import", "dataset", "response:dataset_id",
         capture=("dataset_id", "group_id", "imported", "by_language", "by_channel")),
    rule("POST", "/api/conversations/upload", "conversations.import", "conversation.upload", "conversation",
         ("response:id", "dataset=response:dataset_id"), capture=("id", "dataset_id", "imported", "conversation_ids")),
    rule("PUT", "/api/conversations/{conversation_id}/operator", "conversations.import", "conversation.operator.update", "conversation",
         "path:conversation_id", capture=("operator_name", "operator_name_detected", "operator_name_source")),
    rule("POST", "/api/conversations/paste", "conversations.import", "conversation.paste", "conversation",
         ("response:id", "response:conversation_id"),
         capture=("id", "conversation_id", "channel", "language", "turn_count", "operator_name", "import_status"), refine=_paste_preview),
    # evaluations and jobs
    rule("POST", "/api/evaluations/run", "evaluations.run", "evaluation.run", "job", "response:job_id", refine=_dry_run,
         capture=("job_id", "queued", "estimated_cost_usd", "checklist_version_id", "rejudge_codes", "guard.code")),
    rule("POST", "/api/jobs/{job_id}/cancel", "evaluations.run", "job.cancel", "job", "path:job_id", capture=("status",)),
    rule("POST", "/api/jobs/{job_id}/resume", "evaluations.run", "job.resume", "job", "path:job_id",
         capture=("job_id", "queued", "estimated_cost_usd", "skipped_completed", "skipped_missing")),
    rule(W, "/api/jobs/{job_id}/{rest:path}", "evaluations.run", "job.update", "job", "path:job_id"),
    rule(W, "/api/evaluations/{evaluation_id}/{rest:path}", "evaluations.run", "evaluation.update", "evaluation",
         "path:evaluation_id"),
    # checklists
    rule("POST", "/api/checklists/test-criterion", "checklists.write", "checklist.criterion.test", "conversation",
         "body:conversation_id", capture=("answer", "score", "applicable", "confidence", "cost_usd")),
    rule("POST", "/api/checklists/import", "checklists.write", "checklist.import", "checklist",
         ("response:checklist_id", "response:id"), capture=("id", "checklist_id", "version_no")),
    rule("POST", "/api/checklists", "checklists.write", "checklist.create", "checklist", "response:checklist_id",
         capture=("id", "checklist_id", "version_no", "is_active")),
    rule("POST", "/api/checklists/{checklist_id}/versions", "checklists.write", "checklist.version.create",
         "checklist_version", "response:id",
         capture=("id", "checklist_id", "version_no", "is_active", "created_from_version_id")),
    rule("POST", "/api/checklists/{checklist_id}/versions/{version_id}/activate", "checklists.write",
         "checklist.version.activate", "checklist_version", "path:version_id", capture=("version_no", "is_active")),
    rule("POST", "/api/checklists/{checklist_id}/versions/{version_id}/judge-spec/{code}/regenerate", "checklists.write",
         "checklist.spec.regenerate", "checklist_version", "path:version_id", capture=("code",)),
    # what-if over stored answers: a calculation, open to every role like the appeal preview, not recorded
    rule("POST", "/api/checklists/{checklist_id}/versions/{version_id}/impact", "read", "checklist.impact", "checklist_version",
         "path:version_id", audit=False),
    rule("DELETE", "/api/checklists/{checklist_id}", "checklists.write", "checklist.delete", "checklist", "path:checklist_id"),
    rule(W, "/api/checklists/{checklist_id}", "checklists.write", "checklist.update", "checklist", "path:checklist_id"),
    rule(W, "/api/checklists/{checklist_id}/{rest:path}", "checklists.write", "checklist.update", "checklist",
         "path:checklist_id"),
    # appeals (preview only computes a hypothetical total: open to everyone, not recorded)
    rule("POST", "/api/appeals/preview", "read", "appeal.preview", "criterion_result", "body:evaluation_criterion_id",
         audit=False),
    rule("POST", "/api/appeals", "appeals.create", "appeal.create", "appeal", "response:id",
         capture=("evaluation_criterion_id", "evaluation_id", "conversation_id", "code", "author_role", "author_name")),
    rule("POST", "/api/appeals/{appeal_id}/withdraw", "appeals.create", "appeal.withdraw", "appeal", "path:appeal_id",
         capture=("conversation_id", "code", "author_role", "author_name", "status")),
    rule("POST", "/api/appeals/{appeal_id}/decision", "appeals.decide", "appeal.decide", "appeal", "path:appeal_id",
         capture=("appeal.conversation_id", "appeal.code", "appeal.decision.action", "appeal.decision.new_answer",
                  "appeal.decision.reviewer_role", "evaluation.id", "evaluation.total_score", "evaluation.final_total_score")),
    # settings
    rule("PUT", "/api/settings", "settings.write", "settings.update", "settings"),
    rule(W, "/api/settings/{key:path}", "settings.write", "settings.update", "settings", "path:key"),
    # calibration (gold labels)
    rule(("PUT", "POST"), "/api/calibration/labels", "calibration.write", "calibration.labels.upsert", "gold_labels",
         capture=("created", "updated", "unchanged", "removed", "split_propagated", "labelers", "dry_run"),
         refine=_calibration_dry_run),
    rule("POST", "/api/calibration/import", "calibration.write", "calibration.labels.import", "gold_labels",
         capture=("labeler", "dialogs", "labels", "created", "updated", "unchanged", "removed", "skipped_conversations",
                  "replace", "dry_run"),
         refine=_calibration_dry_run),
    rule("DELETE", "/api/calibration/labels", "calibration.write", "calibration.labels.delete", "gold_labels"),
    rule("DELETE", "/api/calibration/labels/{label_id}", "calibration.write", "calibration.labels.delete", "gold_label",
         "path:label_id"),
    rule(W, "/api/calibration/{rest:path}", "calibration.write", "calibration.update", "calibration"),
    # integrations (ingest is a write of conversations; configuring an integration is administration)
    rule("POST", "/api/integrations/dynamics/transcripts", "integrations.ingest", "integration.dynamics.ingest", "dataset",
         ("conversation=response:conversation_id", "response:dataset_id", "conversation=response:id"),
         capture=("conversation_id", "dataset_id", "created", "turns", "language", "channel", "job_id", "evaluation.status"),
         refine=_dynamics_evaluate),
    rule(W, "/api/integrations/{name}", "admin", "integration.config.update", "integration", "path:name"),
    rule(W, "/api/integrations/{name}/{rest:path}", "admin", "integration.config.update", "integration", "path:name"),
    # administration
    rule("POST", "/api/admin/snapshots", "admin", "snapshot.create", "snapshot",
         ("response:name", "response:snapshot.name", "response:id"), capture=("name", "size_bytes", "bytes", "created_at")),
    rule("POST", "/api/admin/snapshots/{name}/restore", "admin", "snapshot.restore", "snapshot", "path:name",
         capture=("name", "safety_snapshot", "verified", "counts", "schema_sync")),
    rule("DELETE", "/api/admin/snapshots/{name}", "admin", "snapshot.delete", "snapshot", "path:name"),
    rule(W, "/api/admin/{rest:path}", "admin", "admin.write"),
    rule(W, "/api/system/{rest:path}", "admin", "system.write"),
    # default: any other write needs `admin`
    rule(W, "/{rest:path}", "admin", "api.write"),
]
DEFAULT_RULE: RouteRule = ROUTE_MAP[-1]


def register_route(new_rule: RouteRule) -> RouteRule:
    """Add a rule (e.g. from another package at import time) in front of the default rule; idempotent per method+path."""
    for i, existing in enumerate(ROUTE_MAP):
        if existing.methods == new_rule.methods and existing.path == new_rule.path and existing is not DEFAULT_RULE:
            ROUTE_MAP[i] = new_rule
            return new_rule
    ROUTE_MAP.insert(len(ROUTE_MAP) - 1, new_rule)
    return new_rule


def normalize_path(path: str) -> str:
    path = path or "/"
    return path.rstrip("/") or "/"


def match_route(method: str, path: str) -> tuple[RouteRule, dict[str, str]]:
    """The rule for a write request (DEFAULT_RULE when nothing more specific matches) and its path parameters."""
    m, p = (method or "").upper(), normalize_path(path)
    for r in ROUTE_MAP:
        if m in r.methods:
            found = r.pattern.fullmatch(p)
            if found:
                return r, {k: v for k, v in found.groupdict().items() if v is not None and k != "rest"}
    return DEFAULT_RULE, {}


# ----------------------------------------------------------------------------- permission checks


def has_permission(role: str | None, permission: str) -> bool:
    """Whether `role` holds `permission` (None means the default role `demo`; unknown roles hold nothing)."""
    return permission in _ROLE_PERMS.get(role or DEFAULT_ROLE, frozenset())


def permissions_for(role: str | None) -> list[str]:
    perms = _ROLE_PERMS.get(role or DEFAULT_ROLE, frozenset())
    return [p for p in ALL_PERMISSIONS if p in perms]


def label(catalog: Mapping[str, Mapping[str, Any]], code: str | None, lang: str = "ru") -> str | None:
    if code is None:
        return None
    spec = catalog.get(code)
    if not spec:
        return code
    labels = spec.get("label") or {}
    return labels.get(lang) or labels.get("ru") or code


def role_label(code: str | None, lang: str = "ru") -> str | None:
    if code == SYSTEM_ROLE:
        return {"ru": "Система", "en": "System", "kk": "Жүйе"}.get(lang, "Система")
    return label(ROLES, code, lang)


def permission_label(code: str | None, lang: str = "ru") -> str | None:
    return label(PERMISSIONS, code, lang)


def action_label(code: str | None, lang: str = "ru") -> str | None:
    return label(ACTIONS, code, lang)


class UnknownRoleError(ValueError):
    def __init__(self, role: str) -> None:
        super().__init__(role)
        self.role = role


def unknown_role_detail(role: str) -> dict[str, Any]:
    return {"code": "unknown_role",
            "message": f"Неизвестная роль «{role}» в заголовке {ROLE_HEADER}. Допустимые роли: {', '.join(ROLES)}.",
            "role": role, "roles": list(ROLES)}


def forbidden_detail(role: str, permission: str, action: str | None = None) -> dict[str, Any]:
    rl, pl = role_label(role), permission_label(permission)
    if action:
        message = f"Недостаточно прав: роли «{rl}» недоступно действие «{action_label(action)}» (нужно право «{pl}»)."
    else:
        message = f"Недостаточно прав: роли «{rl}» нужно право «{pl}»."
    detail: dict[str, Any] = {"code": "forbidden", "message": message, "role": role, "permission": permission}
    if action:
        detail["action"] = action
    return detail


# ----------------------------------------------------------------------------- actor resolution

_RID_STRIP = re.compile(r"[^A-Za-z0-9._:/+=@\-]")  # the character set cqa.platform.requestlog accepts


def _text(raw: str | bytes | None) -> str | None:
    if raw is None:
        return None
    return raw.decode("latin-1") if isinstance(raw, (bytes, bytearray)) else str(raw)


def headerless_role() -> str:
    """Role of a request without X-CQA-Role. `demo` (every permission) unless the deployment sets CQA_HEADERLESS_ROLE,
    e.g. `visitor` once the web app sends role headers: then direct API calls without headers can only read (DEMO-04).
    Read on every request, so a Railway variable change applies after the restart it triggers."""
    import os

    value = (os.environ.get("CQA_HEADERLESS_ROLE") or "").strip().lower()
    return value if value in ROLES else DEFAULT_ROLE


def normalize_role(raw: str | bytes | None) -> str:
    """Header value → role code: trimmed, case-insensitive; empty → `demo` (or CQA_HEADERLESS_ROLE); unknown → UnknownRoleError."""
    text = (_text(raw) or "").strip()
    if not text:
        return headerless_role()
    code = text.lower()
    if code not in ROLES:
        raise UnknownRoleError((decode_user(raw) or text)[:64])  # echoed readably even if sent as UTF-8 / %-encoded
    return code


def decode_user(raw: str | bytes | None) -> str | None:
    """X-CQA-User → person name. URL-encoded UTF-8 is decoded; raw UTF-8 bytes (and UTF-8 mis-decoded as latin-1,
    as Starlette's Headers do) are accepted too. Control characters are dropped, whitespace collapsed, ≤128 chars."""
    if raw is None:
        return None
    if isinstance(raw, (bytes, bytearray)):
        try:
            text = bytes(raw).decode("utf-8")
        except UnicodeDecodeError:
            text = bytes(raw).decode("latin-1")
    else:
        text = str(raw)
        try:
            text = text.encode("latin-1").decode("utf-8")
        except (UnicodeEncodeError, UnicodeDecodeError):
            pass
    if "%" in text:
        try:
            text = unquote(text, encoding="utf-8", errors="strict")
        except UnicodeDecodeError:  # not UTF-8 percent-encoding (encodeURIComponent always is): try latin-1
            text = unquote(text, encoding="latin-1")
    text = " ".join("".join(ch if ch.isprintable() else " " for ch in text).split())
    return text[:NAME_MAX] or None


def clean_request_id(raw: str | bytes | None) -> str | None:
    """A correlation id safe for logs and headers: printable ASCII from a small character set, at most 64 chars."""
    text = _RID_STRIP.sub("", (_text(raw) or "").strip())
    return text[:REQUEST_ID_MAX] or None


def new_request_id() -> str:
    return secrets.token_hex(6)


def make_actor(role: str = DEFAULT_ROLE, name: str | None = None, request_id: str | None = None) -> dict[str, Any]:
    return {"role": role, "name": name, "request_id": request_id or new_request_id()}


def header_value(headers: Iterable[tuple[bytes, bytes]], name: bytes) -> bytes | None:
    for key, value in headers or ():
        if bytes(key).lower() == name:
            return bytes(value)
    return None


def actor_from_headers(headers: Iterable[tuple[bytes, bytes]], request_id: str | None = None) -> dict[str, Any]:
    """Resolve the actor from raw ASGI headers. Raises UnknownRoleError for an unknown X-CQA-Role."""
    headers = list(headers or ())
    role = normalize_role(header_value(headers, b"x-cqa-role"))
    name = decode_user(header_value(headers, b"x-cqa-user"))
    rid = clean_request_id(request_id) or clean_request_id(header_value(headers, b"x-request-id")) or new_request_id()
    return make_actor(role, name, rid)


def current_actor(request: Request) -> dict[str, Any]:
    """`{"role", "name", "request_id"}` of the request: set by the security middleware, or resolved from the headers
    when the middleware is not installed (e.g. an app built in a test). Unknown role → HTTP 400 `unknown_role`."""
    scope = request.scope
    state = scope.get("state")
    if state is None:
        state = scope["state"] = {}
    actor = state.get("actor")
    if isinstance(actor, dict) and actor.get("role"):
        return actor
    try:
        actor = actor_from_headers(scope.get("headers") or [], request_id=state.get("request_id"))
    except UnknownRoleError as e:
        raise HTTPException(400, detail=unknown_role_detail(e.role)) from None
    state["actor"] = actor
    return actor


def require_permission(permission: str) -> Callable[[Request], dict[str, Any]]:
    """FastAPI dependency for handler-level checks (e.g. sensitive GET endpoints): 403 `forbidden` without `permission`."""
    if permission not in PERMISSIONS:
        raise ValueError(f"unknown permission {permission!r}")

    def dependency(request: Request) -> dict[str, Any]:
        actor = current_actor(request)
        if not has_permission(actor["role"], permission):
            raise HTTPException(403, detail=forbidden_detail(actor["role"], permission))
        return actor

    return dependency


def actor_display(actor: Mapping[str, Any] | None) -> str | None:
    """Label for appeals/decisions: "Алина (Оператор)", "Ревьюер", the bare name in demo mode, None for anonymous demo."""
    if not actor:
        return None
    role, name = actor.get("role") or DEFAULT_ROLE, actor.get("name")
    if role == DEFAULT_ROLE:
        return name[:64] if name else None
    rl = role_label(role) or role
    if not name:
        return rl[:64]
    text = f"{name} ({rl})"
    return text if len(text) <= 64 else name[:64]


def person_key(name: str | None) -> str:
    """Comparable form of a person name: case-folded, ё→е, whitespace collapsed."""
    return " ".join((name or "").casefold().replace("ё", "е").split())


def same_person(a: str | None, b: str | None) -> bool:
    return bool(a and b) and person_key(a) == person_key(b)


# ----------------------------------------------------------------------------- catalog for GET /api/roles and /api/me


def nav_for(role: str) -> list[dict[str, Any]]:
    return [{"key": key, "route": NAV_ITEMS[key]["route"], "label": NAV_ITEMS[key]["label"]}
            for key in ROLES.get(role, {}).get("nav", ()) if key in NAV_ITEMS]


def role_info(code: str) -> dict[str, Any]:
    spec = ROLES[code]
    return {"code": code, "label": spec["label"], "description": spec["description"], "permissions": permissions_for(code),
            "home": spec["home"], "nav": nav_for(code), "constraints": dict(spec.get("constraints") or {}),
            "is_default": code == DEFAULT_ROLE}


def catalog() -> dict[str, Any]:
    return {
        "default_role": DEFAULT_ROLE,
        "headers": {"role": ROLE_HEADER, "user": USER_HEADER, "request_id": REQUEST_ID_HEADER,
                    "user_encoding": "URL-encoded UTF-8 (encodeURIComponent); raw UTF-8 is accepted"},
        "roles": [role_info(code) for code in ROLES],
        "permissions": [{"code": code, "label": spec["label"], "description": spec["description"]}
                        for code, spec in PERMISSIONS.items()],
        "matrix": {perm: {code: has_permission(code, perm) for code in ROLES} for perm in ALL_PERMISSIONS},
        "nav_items": [{"key": key, "route": spec["route"], "label": spec["label"]} for key, spec in NAV_ITEMS.items()],
        "actions": [{"code": code, "label": spec["label"]} for code, spec in ACTIONS.items()],
        "write_routes": [{"methods": list(r.methods), "path": r.path, "permission": r.permission, "action": r.action,
                          "entity_type": r.entity_type, "audited": r.audit} for r in ROUTE_MAP],
    }


def describe_actor(actor: Mapping[str, Any], *, enforced: bool) -> dict[str, Any]:
    info = role_info(actor["role"])
    return {"role": actor["role"], "name": actor.get("name"), "request_id": actor.get("request_id"),
            "display": actor_display(actor), "label": info["label"], "description": info["description"],
            "permissions": info["permissions"], "home": info["home"], "nav": info["nav"], "constraints": info["constraints"],
            "is_default": info["is_default"], "enforced": enforced}
