# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Andexor Network, Inc.
# Author: Ed Jenkins

"""What the API answers and accepts where that is more than one stored record, for the OpenAPI specification.

The records themselves are in models.py, which is generated from the database schema. An answer
here joins tables, adds computed fields, or is a small result. The smoke test checks every answer
it gets against these models (tools/contract_check.py), so one that disagrees with the code is found.
See specs/032-openapi-models/spec.md.
"""

from collections.abc import Callable
from typing import Any, Generic, TypeVar

from pydantic import BaseModel, RootModel

import models
from models import Row

T = TypeVar("T")
Endpoint = TypeVar("Endpoint", bound=Callable[..., Any])


def takes(model: type[BaseModel]) -> Callable[[Endpoint], Endpoint]:
    """Declare the body of a route for the specification. The code still reads and checks the body
    itself, so nothing the API accepts today is refused because of a model."""

    def mark(endpoint: Endpoint) -> Endpoint:
        setattr(endpoint, "body_model", model)
        return endpoint

    return mark


# ---- Lists ---------------------------------------------------------------------------------------


class Page(Row, Generic[T]):
    """One page of a list. next_cursor is passed as cursor to read the next page, and is null on the last one."""

    items: list[T]
    next_cursor: str | None


class Items(Row, Generic[T]):
    """A whole list."""

    items: list[T]


# ---- Records with more than their row ------------------------------------------------------------


class DealOut(models.Deal):
    company_name: str
    service_ids: list[str]


class AgreementOut(models.Agreement):
    company_name: str
    service_ids: list[str]


class AgreementDetail(AgreementOut):
    children: list[AgreementOut]
    documents: list[models.Document]


class ServiceScore(Row):
    """How much a company needs one service."""

    service_id: str
    name: str
    offered: int
    need_score: int | None
    confidence: int | None


class CompanyDetail(models.Company):
    contacts: list[models.Contact]
    open_tasks: list[models.Task]
    services: list[ServiceScore]
    open_deals: list[DealOut]
    active_agreements: list[AgreementOut]


class Scores(Row):
    """A company's scores after its answers changed."""

    fit_score: int | None
    need_score: int | None
    confidence: int | None
    recommendation: str | None
    services: list[ServiceScore]


class Answer(models.RubricCriterion):
    """A criterion with the company's answer to it, if it has one."""

    value: str | None
    points: float | None
    source: str | None
    ai_run_id: str | None
    answered_by: str | None
    answered_at: str | None


class FactKey(Row):
    """A fact key: one from the catalog, or one that only stored facts use, which has no label."""

    key: str
    label: str | None
    value_type: str
    description: str | None
    sort_order: int | None


class ModelOut(models.AiModel):
    runnable: bool


class ModelStats(Row):
    ai_model_id: str
    label: str
    provider: str
    kind: str
    runs: int
    failures: int
    average_rating: float | None
    average_duration_ms: float | None
    average_tokens: float | None
    agreement_rate: float | None


# ---- AI runs -------------------------------------------------------------------------------------


class RunOut(models.AiRun):
    model_label: str
    provider: str


class RunAnswer(Row):
    criterion_id: str
    section: str
    service_id: str | None
    prompt: str
    answer_type: str
    value: str | None
    points: float | None
    probability: float | None
    rationale: str | None


class RunDetail(RunOut):
    """A research run carries its dossier and facts; a scoring run its answers and services."""

    dossier: str | None = None
    facts: list[models.AiRunFact] | None = None
    answers: list[RunAnswer] | None = None
    services: list[ServiceScore] | None = None


class RunIds(Row):
    run_ids: list[str]


class CompareAnswer(Row):
    value: str | None
    points: float | None
    probability: float | None
    rationale: str | None


class CompareItem(Row):
    criterion_id: str
    section: str
    service_id: str | None
    prompt: str
    answer_type: str
    in_effect: str | None
    runs: dict[str, CompareAnswer | None]
    disagreement: bool


class Comparison(Row):
    runs: list[RunOut]
    items: list[CompareItem]


# ---- Service proposals ---------------------------------------------------------------------------


class ProposalAnswer(Row):
    criterion_id: str
    prompt: str
    value: str | None
    points: float | None
    source: str | None
    rationale: str | None


class ProposalFact(Row):
    key: str
    value: str | None


class Proposal(Row):
    service_id: str
    name: str
    category: str
    need_score: int | None
    confidence: int | None
    answers: list[ProposalAnswer]
    facts: list[ProposalFact]


class Proposals(Row):
    scored_at: str | None
    items: list[Proposal]


class Queued(Row):
    queued: bool


# ---- Uptime, Today, imports, and small results ---------------------------------------------------


class Uptime(Row):
    percent: float | None
    checks: int
    up: int
    window_days: int
    measuring: bool
    items: list[models.UptimeCheck]


class TaskOfCompany(models.Task):
    company_name: str | None


class DealOfCompany(models.Deal):
    company_name: str


class AgreementRenewing(models.Agreement):
    company_name: str
    notice_date: str


class RunInProgress(Row):
    id: str
    company_id: str
    company_name: str
    kind: str
    ai_model_id: str
    status: str
    created_at: str
    started_at: str | None


class Today(Row):
    date: str
    time_zone: str
    tasks_overdue: list[TaskOfCompany]
    tasks_due: list[TaskOfCompany]
    leads_unscored: list[models.Company]
    top_leads: list[models.Company]
    deals_without_task: list[DealOfCompany]
    agreements_renewing: list[AgreementRenewing]
    runs_in_progress: list[RunInProgress]


class ImportDuplicate(Row):
    """A row that repeats a website: of an earlier row of the file, or of a company already stored."""

    row: int
    domain: str
    of_row: int | None = None
    company_id: str | None = None


class ImportError(Row):
    row: int
    error: str


class ImportStatus(models.ImportBatch):
    status: str
    message: str | None
    duplicates: list[ImportDuplicate]
    errors: list[ImportError]


class ImportPreview(Row):
    headers: list[str]
    mapping: dict[str, str | None]
    targets: list[str]
    row_count: int
    rows: list[list[str]]


class ImportUndone(Row):
    deleted: int
    kept: int


class Created(Row):
    id: str


class LeadResult(Row):
    company_id: str
    created: bool


class FactsAdded(Row):
    added: int


class Recomputed(Row):
    companies: int


class SchedulerRun(Row):
    completed: int
    expired: int
    uptime: int
    alerts: int
    digest: str | None
    failed: list[str]


# ---- Bodies that are not one record's fields -----------------------------------------------------
# The fields of a record's body are in models.py, generated from the field lists in records.py.


class FactIn(BaseModel):
    key: str | None = None
    value: str | int | float | bool | None = None
    source_detail: str | None = None
    notes: str | None = None
    observed_at: str | None = None


class FactsBody(FactIn):
    """One fact, or several under facts."""

    facts: list[FactIn] | None = None


class AnswerIn(BaseModel):
    criterion_id: str
    value: str | int | float | None = None


class AnswersBody(BaseModel):
    answers: list[AnswerIn]


class DealBody(models.DealBody):
    service_ids: list[str] | None = None


class AgreementBody(models.AgreementBody):
    service_ids: list[str] | None = None


class StartRunsBody(BaseModel):
    kind: str | None = None
    ai_model_ids: list[str] | None = None
    input_run_id: str | None = None


class ResearchAndScoreBody(BaseModel):
    research_model_id: str | None = None
    scoring_model_ids: list[str] | None = None


class Decision(BaseModel):
    id: str
    decision: str


class DecideBody(BaseModel):
    """One decision for every fact of the run (all), or one for each fact named (decisions)."""

    all: str | None = None
    decisions: list[Decision] | None = None


class CsvBody(BaseModel):
    csv: str


class ImportBody(CsvBody):
    file_name: str | None = None
    mapping: dict[str, str | None] | None = None


class LeadContact(BaseModel):
    name: str | None = None
    email: str | None = None


class LeadBody(BaseModel):
    source: str
    source_ref: str | None = None
    name: str | None = None
    website: str | None = None
    note: str | None = None
    contact: LeadContact | None = None
    facts: list[FactIn] | None = None


class MachineFactsBody(BaseModel):
    domain: str | None = None
    company_id: str | None = None
    source: str
    facts: list[FactIn]


class SettingsBody(RootModel[dict[str, str | int]]):
    """Setting keys with their new values."""
