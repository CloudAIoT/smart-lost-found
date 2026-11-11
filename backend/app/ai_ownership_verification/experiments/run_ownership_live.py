"""Exercise the actual API and Gemini against an isolated 100-item SQLite corpus."""

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
from time import perf_counter
from uuid import UUID

import httpx
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, func, select, URL
from sqlalchemy.orm import Session

from app.ai_ownership_verification.api import get_ownership_ai
from app.ai_ownership_verification.search import find_candidates, stored_items
from app.ai_ownership_verification.workflow import get_ownership_result
from app.ai_ownership_verification.workflow_ai import GeminiWorkflowAI
from app.ai_ownership_verification.workflow_schemas import SearchClues
from app.config import Settings, get_settings
from app.db import Base, get_session
from app.errors import IntegrationError
from app.main import app
from app.models import FoundItem, OwnershipAttempt
from .ownership_live_corpus import seed


BASE = "/api/ownership/attempts"
FATAL = {"OWNERSHIP_AI_RATE_LIMITED", "OWNERSHIP_AI_AUTHENTICATION_FAILED", "OWNERSHIP_AI_MODEL_UNAVAILABLE",
         "OWNERSHIP_AI_NOT_CONFIGURED", "OWNERSHIP_AI_UNREACHABLE", "LIVE_EXPERIMENT_CALL_LIMIT"}
CASES = [
    ("owner_five_questions", "도서관에서 필통을 찾고 있어요.", "completed", "supported",
     {"item": "P01", "initial_count": 5, "rounds": 5, "answers": {
         "장식": "파란 원형 핀", "문구": "MJ", "재질": "fabric", "브랜드": "Carhartt", "색상": "navy"}}),
    ("two_wrong", "수첩을 잃어버렸어요.", "completed", "not_confirmed",
     {"initial_count": 1, "rounds": 2, "matches": 0, "wrong_for": {"장식": "물방울 배지", "문구": "QX"}}),
    ("one_wrong", "수첩을 잃어버렸어요.", "completed", "insufficient_evidence",
     {"item": "N01", "initial_count": 1, "rounds": 2, "matches": 1,
      "wrong_for": {"장식": "물방울 배지"}, "answers": {"문구": "QX"}}),
    ("similar_color", "진한 파란색 필통을 잃어버렸어요.", "question", None,
     {"search_only": True, "items": ["P01"], "required_field": ["P01", "color"]}),
    ("all_bags", "가방을 잃어버렸어요.", "question", None,
     {"search_only": True, "items": ["B01", "B02", "B03", "B04", "B05", "N02", "N07", "N12", "N17", "N22"]}),
    ("brand_missing", "흰색 애플 이어폰을 잃어버렸어요.", "question", None,
     {"search_only": True, "items": ["M01"], "required_field": ["M01", "color"], "missing_brand": "M01"}),
    ("no_candidate", "보라색 바이올린을 잃어버렸어요.", "no_candidates", None,
     {"search_only": True, "items": []}),
]

USER_FLOW_CASES = [
    ("notebook_all_correct", "학생회관에서 잃어버린 수첩을 찾고 있어요.", "completed", "supported",
     {"initial_items": ["N01"], "remaining_items": ["N01"], "item": "N01", "rounds": 2, "matches": 2,
      "answers": {"장식": "물방울 배지", "문구": "QX"}}),
    ("wallet_all_correct_tied", "잃어버린 지갑이 있는지 찾아주세요.", "completed", "insufficient_evidence",
     {"initial_items": ["W01", "W02", "W03", "W04", "W05"], "remaining_items": ["W01", "W02"],
      "rounds": 2, "answers": {"장식": "은색 나뭇잎", "문구": "AB"}}),
    ("wallet_answers_split", "지갑을 잃어버렸어요.", "completed", "insufficient_evidence",
     {"initial_items": ["W01", "W02", "W03", "W04", "W05"],
      "remaining_items": ["W01", "W02", "W03", "W04", "W05"], "rounds": 2,
      "answers": {"장식": "은색 나뭇잎", "문구": "CD"}}),
    ("alternate_pencil", "도서관에서 필통을 찾고 있어요.", "completed", "insufficient_evidence",
     {"initial_items": ["P01", "P02", "P03", "P04", "P05"], "remaining_items": ["P02", "P03", "P04", "P05"],
      "rounds": 2, "answers": {"장식": "빨간 사각 핀", "문구": "AB"}}),
    ("umbrella_public_match", "검은색 우산을 잃어버렸어요.", "completed", "insufficient_evidence",
     {"initial_items": ["U01", "U02", "U03", "U04", "U05"], "remaining_items": ["U01"],
      "item": "U01", "rounds": 1, "matches": 0, "answers": {"재질": "nylon"}}),
    ("umbrella_generated_choice", "우산을 잃어버려서 찾고 있어요.", "completed", "insufficient_evidence",
     {"initial_items": ["U01", "U02", "U03", "U04", "U05"],
      "remaining_items": ["U01", "U02", "U03", "U04", "U05"], "rounds": 1,
      "wrong_for": {"재질": ["nylon", "vinyl"]}}),
    ("earbuds_missing_brand", "흰색 Apple 이어폰을 잃어버렸어요.", "completed", "insufficient_evidence",
     {"initial_items": ["M01"], "remaining_items": ["M01"], "item": "M01", "rounds": 0, "matches": 0}),
    ("thermos_no_private_features", "보온병을 찾고 있어요.", "completed", "insufficient_evidence",
     {"initial_items": ["M03"], "remaining_items": ["M03"], "item": "M03", "rounds": 0, "matches": 0}),
    ("helmet_not_in_storage", "검은색 헬멧을 잃어버렸어요.", "no_candidates", None,
     {"initial_items": [], "remaining_items": [], "rounds": 0}),
    ("vague_input", "잘 모르겠어요.", "needs_clues", None,
     {"initial_items": [], "remaining_items": [], "rounds": 0}),
]


class ObservedAI(GeminiWorkflowAI):
    def __init__(self, settings, client, limit):
        super().__init__(settings, client)
        self.limit = limit
        self.events = []
        self.sent = 0
        self.case = None
        self.stop_code = None

    def _observe(self, stage, call, **details):
        if self.sent >= self.limit:
            self.stop_code = "LIVE_EXPERIMENT_CALL_LIMIT"
            raise IntegrationError(self.stop_code)
        self.sent += 1
        event = {"case": self.case, "stage": stage, **details}
        self.events.append(event)
        started = perf_counter()
        try:
            result = call()
            if stage == "search":
                event["result"] = [match.model_dump() for match in result]
            else:
                event["result"] = result.model_dump() if isinstance(result, SearchClues) else result
            return result
        except IntegrationError as exc:
            event["error"] = exc.code
            if exc.code in FATAL:
                self.stop_code = exc.code
            raise
        finally:
            event["milliseconds"] = round((perf_counter() - started) * 1000)

    def extract(self, description):
        return self._observe("extract", lambda: super(ObservedAI, self).extract(description))

    def similar_items(self, clues, items):
        return self._observe("search", lambda: super(ObservedAI, self).similar_items(clues, items),
                             items_compared=len(items))

    def distractors(self, field, known, count):
        return self._observe(f"options:{field}", lambda: super(ObservedAI, self).distractors(field, known, count),
                             known=known, required_count=count)

    def record_http_status(self, response):
        self.events[-1]["provider_http_status"] = response.status_code


def read_attempt(engine, attempt_id):
    if not attempt_id:
        return {"candidates": [], "answers": []}
    with Session(engine) as session:
        return session.get(OwnershipAttempt, UUID(attempt_id)).state


def snapshots(state, mapping):
    return [{"item": mapping[entry["item_id"]], "initial_matches": entry["initial_matches"]}
            for entry in state["candidates"]]


def run_search_case(engine, ai, mapping, entry):
    name, description, status, _, expected = entry
    result = {"case": name, "description": description, "expected": {"status": status, **expected}}
    ai.case = name
    try:
        clues = ai.extract(description)
        with Session(engine) as session:
            actual_status, candidates = find_candidates(session, clues, ai)
    except IntegrationError as exc:
        return {**result, "actual": {"error": exc.code}, "passed": False}
    found = {mapping[str(candidate.item_id)]: candidate.initial_matches for candidate in candidates}
    expected_items = set(expected["items"])
    checks = {"status": actual_status == status, "candidate_set": found.keys() == expected_items}
    if "required_field" in expected:
        item, field = expected["required_field"]
        checks["required_field"] = field in found.get(item, [])
    if "missing_brand" in expected:
        item = expected["missing_brand"]
        checks["missing_brand_not_matched"] = item in found and "brand" not in found[item]
    return {**result, "actual": {"status": actual_status, "candidates": found,
                                 "missed": sorted(expected_items - found.keys()),
                                 "unexpected": sorted(found.keys() - expected_items)},
            "checks": checks, "passed": all(checks.values())}


def run_case(client, engine, ai, mapping, entry):
    name, description, status, decision, expected = entry
    result = {"case": name, "description": description, "expected": {"status": status, "decision": decision, **expected},
              "steps": [], "checks": {}}
    ai.case = name
    response = client.post(BASE, json={"description": description})
    initial = None
    previous_step = None
    while response.status_code == 200:
        view = response.json()
        state = read_attempt(engine, view["attempt_id"])
        candidates = snapshots(state, mapping)
        if initial is None:
            initial = candidates
        if previous_step is not None:
            recorded = state["answers"][-1]
            previous_step.update(
                purpose=recorded["purpose"], candidates_after=candidates,
                matched_items=[mapping[item] for item in recorded["matched_item_ids"]],
                conflicting_items=[mapping[item] for item in recorded["conflicting_item_ids"]],
            )
        if view["status"] != "question":
            result["actual"] = {"status": view["status"], "decision": view["decision"], "initial_candidates": initial,
                                "remaining_candidates": candidates}
            if view["attempt_id"]:
                with Session(engine) as session:
                    try:
                        handoff = get_ownership_result(session, UUID(view["attempt_id"]))
                    except IntegrationError as exc:
                        result["actual"]["error"] = exc.code
                    else:
                        if handoff:
                            result["actual"]["handoff"] = {
                                "item": mapping.get(str(handoff.item_id)), "decision": handoff.decision,
                                "matched_evidence_groups": handoff.matched_evidence_groups,
                                "answered_questions": handoff.answered_questions, "policy_version": handoff.policy_version,
                            }
            break
        if len(result["steps"]) >= 8:
            result["actual"] = {"error": "EXPERIMENT_TOO_MANY_QUESTIONS"}
            break
        question = view["question"]
        labels = [option["label"] for option in question["options"]]
        desired = next((label for term, label in expected.get("answers", {}).items() if term in question["text"]), None)
        wrong_for = next((label for term, label in expected.get("wrong_for", {}).items() if term in question["text"]), None)
        if wrong_for:
            known = [wrong_for] if isinstance(wrong_for, str) else wrong_for
            desired = next((label for label in labels if label not in known), None)
        option = next((option for option in question["options"] if option["label"] == desired), None)
        previous_step = {"question": question["text"], "options": labels, "selected": desired,
                         "expected_wrong": wrong_for is not None, "candidates_before": candidates}
        result["steps"].append(previous_step)
        if option is None:
            result["actual"] = {"error": "EXPECTED_CHOICE_ABSENT"}
            break
        response = client.post(f'{BASE}/{view["attempt_id"]}/answers',
                               json={"question_id": question["id"], "option_id": option["id"]})
    if response.status_code != 200:
        try:
            code = response.json().get("detail", {}).get("code", "UNEXPECTED_HTTP_RESPONSE")
        except (ValueError, AttributeError):
            code = "UNEXPECTED_HTTP_RESPONSE"
        result["actual"] = {"http": response.status_code, "error": code, "initial_candidates": initial}

    actual = result["actual"]
    handoff = actual.get("handoff", {})
    checks = {"status": actual.get("status") == status, "decision": actual.get("decision") == decision}
    if "initial_count" in expected:
        checks["initial_count"] = len(initial or []) == expected["initial_count"]
    if "initial_items" in expected:
        checks["initial_items"] = {entry["item"] for entry in initial or []} == set(expected["initial_items"])
    if "remaining_items" in expected:
        remaining = actual.get("remaining_candidates", [])
        checks["remaining_items"] = {entry["item"] for entry in remaining} == set(expected["remaining_items"])
    if "rounds" in expected:
        checks["rounds"] = len(result["steps"]) == expected["rounds"]
        checks["five_unique_options"] = all(len(set(step["options"])) == 5 and "모르겠어요" not in step["options"]
                                            for step in result["steps"])
    if "item" in expected:
        checks["handoff_item"] = handoff.get("item") == expected["item"]
    if decision is not None:
        checks["handoff_decision"] = handoff.get("decision") == decision
    if "matches" in expected:
        checks["matched_groups"] = handoff.get("matched_evidence_groups") == expected["matches"]
    result["checks"] = checks
    result["passed"] = all(checks.values()) and "error" not in actual
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--max-requests", type=int, default=30, choices=range(1, 41), metavar="1..40")
    parser.add_argument("--suite", choices=("baseline", "user-flows"), default="baseline")
    parser.add_argument("--case", choices=[entry[0] for entry in CASES + USER_FLOW_CASES], action="append")
    args = parser.parse_args()
    cases = USER_FLOW_CASES if args.suite == "user-flows" else CASES
    if args.case and set(args.case) - {entry[0] for entry in cases}:
        parser.error("--case must belong to the selected suite")
    configured = get_settings()
    if not configured.gemini_api_key or not configured.gemini_api_key.get_secret_value().strip() or not configured.gemini_model:
        print("Gemini 키와 모델이 설정되어 있지 않습니다.")
        return 2
    args.output_dir.mkdir(parents=True, exist_ok=False)
    engine = create_engine(URL.create("sqlite", database=str((args.output_dir / "synthetic.sqlite3").resolve())))

    @event.listens_for(engine, "connect")
    def enable_fks(connection, record):
        connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    with Session(engine) as session:
        mapping, distribution = seed(session)
        item_count = session.scalar(select(func.count()).select_from(FoundItem))
        searchable_count = len(session.execute(stored_items()).all())
    assert (item_count, searchable_count) == (100, 90)
    local_settings = Settings(_env_file=None, postgres_db="unused", postgres_user="unused", postgres_password="unused",
                              local_development=True)

    def sessions():
        with Session(engine) as session:
            yield session

    source_dir = Path(__file__).resolve().parent.parent
    source_files = ["search.py", "workflow.py", "workflow_ai.py", "workflow_schemas.py",
                    "experiments/ownership_live_corpus.py", "experiments/run_ownership_live.py"]
    report = {
        "created_at": datetime.now(timezone.utc).isoformat(), "model": configured.gemini_model,
        "suite": args.suite,
        "data": {"synthetic": True, "item_count": item_count, "searchable_count": searchable_count,
                 "distribution": distribution},
        "request_limit": args.max_requests,
        "source_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=source_dir, text=True).strip(),
        "source_hashes": {name: hashlib.sha256((source_dir / name).read_bytes()).hexdigest() for name in source_files},
        "cases": [],
    }
    with httpx.Client(trust_env=False) as http:
        ai = ObservedAI(configured, http, args.max_requests)
        http.event_hooks["response"].append(ai.record_http_status)
        app.dependency_overrides.update({get_session: sessions, get_settings: lambda: local_settings,
                                         get_ownership_ai: lambda: ai})
        try:
            with TestClient(app, raise_server_exceptions=False) as client:
                for entry in cases:
                    if args.case and entry[0] not in args.case:
                        continue
                    if ai.stop_code:
                        report["cases"].append({"case": entry[0], "not_run": ai.stop_code})
                        continue
                    if entry[-1].get("search_only"):
                        result = run_search_case(engine, ai, mapping, entry)
                    else:
                        result = run_case(client, engine, ai, mapping, entry)
                    report["cases"].append(result)
                    print(f'{entry[0]}: {"PASS" if result["passed"] else "FAIL"} ({ai.sent} API requests)', flush=True)
        finally:
            app.dependency_overrides.clear()
            report.update(requests=ai.sent, api_events=ai.events, stop_code=ai.stop_code)
            (args.output_dir / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            engine.dispose()
    return int(any(not case.get("passed") for case in report["cases"]))


if __name__ == "__main__":
    raise SystemExit(main())
