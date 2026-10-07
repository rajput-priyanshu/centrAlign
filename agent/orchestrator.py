"""The orchestrator: a deterministic Python state machine that drives the Gemini
tool-use loop, enforces safety rails (step cap, consecutive-error escalation),
handles human-in-the-loop pauses, and - critically - independently verifies the
outcome against ground truth after the agent reports done(). The LLM decides WHAT
to do next at each step; this file decides HOW the loop as a whole is run.
"""
import base64
import datetime
import os
import re
import threading
import time
import uuid

import requests
from google import genai
from google.genai import errors as genai_errors
from google.genai import types

from .browser import BrowserSession
from .memory import Memory
from .prompts import build_system_prompt
from .tools import TOOL_DEFINITIONS, ToolRouter

VENDOR_MAIL_URL = os.environ.get("VENDOR_MAIL_URL", "http://127.0.0.1:5001")
AP_SYSTEM_URL = os.environ.get("AP_SYSTEM_URL", "http://127.0.0.1:5002")
AP_USERNAME = os.environ.get("AP_SYSTEM_USERNAME", "ap_agent")
AP_PASSWORD = os.environ.get("AP_SYSTEM_PASSWORD", "CentrAlign#2026")
AGENT_MODEL = os.environ.get("AGENT_MODEL", "gemini-flash-lite-latest")
BROWSER_HEADLESS = os.environ.get("BROWSER_HEADLESS", "true").lower() != "false"
MAX_STEPS = int(os.environ.get("AGENT_MAX_STEPS", "40"))
MAX_CONSECUTIVE_ERRORS = 4
MAX_EMPTY_TURNS = 3

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
EVIDENCE_ROOT = os.path.join(BASE_DIR, "evidence")
MEMORY_ROOT = os.path.join(BASE_DIR, "data", "memory")

TASKS = {}
TASKS_LOCK = threading.Lock()

_client = None


def get_client():
    global _client
    if _client is None:
        api_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
        if not api_key:
            raise RuntimeError("GEMINI_API_KEY is not set in .env")
        _client = genai.Client(api_key=api_key)
    return _client


RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}
MAX_WORTHWHILE_RETRY_DELAY_SECONDS = 30
_RETRY_DELAY_RE = re.compile(r"retryDelay['\"]?\s*:\s*['\"](\d+)s")


def _suggested_retry_delay(error):
    """Gemini's 429 responses include a RetryInfo.retryDelay - sometimes a few
    seconds (a short-lived rate limit, worth retrying), sometimes hours (a daily
    quota, not worth retrying at all within a task). Respect that instead of
    blindly backing off."""
    match = _RETRY_DELAY_RE.search(str(error))
    return int(match.group(1)) if match else None


def _generate_with_retry(client, model, contents, config, task, max_retries=3):
    """The Gemini free tier has real rate limits, and the API occasionally returns
    a transient 5xx. Retrying with backoff here means a blip in the LLM provider
    doesn't fail the whole task - this is reliability at the infrastructure layer,
    separate from the agent's own retry/adapt behavior at the task-logic layer."""
    last_error = None
    for attempt in range(max_retries + 1):
        try:
            return client.models.generate_content(model=model, contents=contents, config=config)
        except genai_errors.APIError as e:
            last_error = e
            code = getattr(e, "code", None)
            if code not in RETRYABLE_STATUS_CODES or attempt == max_retries:
                raise
            delay = _suggested_retry_delay(e)
            if delay is not None and delay > MAX_WORTHWHILE_RETRY_DELAY_SECONDS:
                task.add_event({
                    "type": "provider_retry",
                    "error": str(e),
                    "attempt": attempt + 1,
                    "wait_seconds": 0,
                    "note": f"Server asked for a {delay}s wait (likely a daily quota) - giving up rather than stalling the task.",
                })
                raise
            wait = delay if delay is not None else 2 ** attempt
            task.add_event({
                "type": "provider_retry",
                "error": str(e),
                "attempt": attempt + 1,
                "wait_seconds": wait,
            })
            time.sleep(wait)
    raise last_error


def _gemini_tools():
    return [
        types.Tool(function_declarations=[
            types.FunctionDeclaration(
                name=t["name"],
                description=t["description"],
                parameters_json_schema=t["input_schema"],
            )
            for t in TOOL_DEFINITIONS
        ])
    ]


class Task:
    def __init__(self, goal):
        self.id = uuid.uuid4().hex[:10]
        self.goal = goal
        self.status = "queued"
        self.trace = []
        self.pending_question = None
        self.user_answer = None
        self.input_event = threading.Event()
        self.created_at = datetime.datetime.utcnow().isoformat(timespec="seconds")
        self.result = None
        self.lock = threading.Lock()

    def add_event(self, event):
        event = dict(event)
        event["ts"] = datetime.datetime.utcnow().isoformat(timespec="seconds")
        with self.lock:
            self.trace.append(event)

    def to_public_dict(self):
        with self.lock:
            return {
                "id": self.id,
                "goal": self.goal,
                "status": self.status,
                "created_at": self.created_at,
                "pending_question": self.pending_question,
                "trace": list(self.trace),
                "result": self.result,
            }


def create_task(goal):
    task = Task(goal)
    with TASKS_LOCK:
        TASKS[task.id] = task
    thread = threading.Thread(target=_run_task, args=(task,), daemon=True)
    thread.start()
    return task


def get_task(task_id):
    with TASKS_LOCK:
        return TASKS.get(task_id)


def list_tasks():
    with TASKS_LOCK:
        return sorted(TASKS.values(), key=lambda t: t.created_at, reverse=True)


def answer_question(task_id, answer):
    task = get_task(task_id)
    if not task or task.status != "waiting_for_input":
        return False
    task.user_answer = answer
    task.add_event({"type": "human_answer", "answer": answer})
    task.input_event.set()
    return True


def _ask_user_blocking(task):
    def _ask(question):
        task.status = "waiting_for_input"
        task.pending_question = question
        task.add_event({"type": "ask_user", "question": question})
        task.input_event.clear()
        task.input_event.wait()
        task.status = "running"
        task.pending_question = None
        return task.user_answer or "(no answer provided)"
    return _ask


def _run_task(task):
    task.status = "running"
    task.add_event({"type": "status", "status": "running", "detail": "Starting task"})

    evidence_dir = os.path.join(EVIDENCE_ROOT, task.id)
    memory_path = os.path.join(MEMORY_ROOT, f"{task.id}.json")
    browser = BrowserSession(evidence_dir, headless=BROWSER_HEADLESS)
    memory = Memory(memory_path)
    router = ToolRouter(browser, memory, _ask_user_blocking(task), task.add_event)

    system_prompt = build_system_prompt(VENDOR_MAIL_URL, AP_SYSTEM_URL, AP_USERNAME, AP_PASSWORD)
    messages = [types.Content(role="user", parts=[types.Part.from_text(text=f"Task: {task.goal}")])]

    client = get_client()
    tools = _gemini_tools()
    config = types.GenerateContentConfig(system_instruction=system_prompt, tools=tools, max_output_tokens=2048)
    consecutive_errors = 0
    empty_turns = 0

    try:
        for step in range(1, MAX_STEPS + 1):
            response = _generate_with_retry(client, AGENT_MODEL, messages, config, task)

            if not response.candidates or response.candidates[0].content is None:
                empty_turns += 1
                if empty_turns >= MAX_EMPTY_TURNS:
                    router.done_result = {
                        "success": False,
                        "summary": f"Model returned no usable response (finish_reason may indicate a safety block or token limit).",
                        "reason_if_failed": "empty_model_response",
                    }
                    break
                messages.append(types.Content(role="user", parts=[types.Part.from_text(
                    text="No response was produced. Continue the task by calling a tool."
                )]))
                continue

            model_content = response.candidates[0].content
            messages.append(model_content)

            text_parts = [p.text for p in (model_content.parts or []) if getattr(p, "text", None)]
            for t in text_parts:
                if t.strip():
                    task.add_event({"type": "reasoning", "text": t.strip()})

            fn_calls = [p.function_call for p in (model_content.parts or []) if getattr(p, "function_call", None)]

            if not fn_calls:
                empty_turns += 1
                if empty_turns >= MAX_EMPTY_TURNS:
                    router.done_result = {
                        "success": False,
                        "summary": "Agent stopped producing tool calls without finishing.",
                        "reason_if_failed": "no_tool_calls",
                    }
                    break
                messages.append(types.Content(role="user", parts=[types.Part.from_text(
                    text="Continue the task by calling a tool. Call done(...) once you have finished or determined the task cannot be completed."
                )]))
                continue
            empty_turns = 0

            response_parts = []
            images = []  # list of (bytes, mime_type)
            done_called = False
            for fc in fn_calls:
                args = dict(fc.args or {})
                task.add_event({"type": "tool_call", "tool": fc.name, "input": args, "step": step})
                content_blocks, is_error = router.execute(fc.name, args)
                result_text = next((c["text"] for c in content_blocks if c["type"] == "text"), "")
                task.add_event({
                    "type": "tool_result",
                    "tool": fc.name,
                    "is_error": is_error,
                    "text": result_text,
                    "step": step,
                })
                consecutive_errors = consecutive_errors + 1 if is_error else 0

                response_dict = {"error": result_text} if is_error else {"result": result_text}
                response_parts.append(types.Part(function_response=types.FunctionResponse(
                    id=getattr(fc, "id", None), name=fc.name, response=response_dict,
                )))
                for c in content_blocks:
                    if c["type"] == "image":
                        images.append((base64.b64decode(c["source"]["data"]), c["source"]["media_type"]))

                if fc.name == "done":
                    done_called = True

            messages.append(types.Content(role="user", parts=response_parts))

            if images:
                img_parts = [types.Part.from_text(text="Screenshot(s) of the resulting page(s), in order:")]
                img_parts += [types.Part.from_bytes(data=data, mime_type=mime) for data, mime in images]
                messages.append(types.Content(role="user", parts=img_parts))

            if consecutive_errors >= MAX_CONSECUTIVE_ERRORS:
                messages.append(types.Content(role="user", parts=[types.Part.from_text(text=(
                    f"[orchestrator] You have hit {consecutive_errors} consecutive tool errors. "
                    "Stop retrying the same approach - either try a genuinely different approach, "
                    "ask_user for help, or call done(success=false, ...) with what you learned."
                ))]))
                consecutive_errors = 0

            if done_called:
                break
        else:
            router.done_result = router.done_result or {
                "success": False,
                "summary": f"Exceeded the maximum step budget ({MAX_STEPS} steps) without finishing.",
                "reason_if_failed": "step_limit_exceeded",
            }

        done_result = router.done_result or {
            "success": False,
            "summary": "Agent loop ended unexpectedly without calling done().",
            "reason_if_failed": "loop_ended_without_done",
        }

        task.status = "verifying"
        task.add_event({"type": "status", "status": "verifying"})
        verification = _independent_verify(done_result)
        task.add_event({"type": "verification", **verification})

        final_status = _final_status(done_result, verification)
        task.status = final_status
        task.result = {
            "done": done_result,
            "verification": verification,
            "memory": memory.all(),
            "evidence_dir": os.path.relpath(evidence_dir, BASE_DIR),
        }
        task.add_event({"type": "status", "status": final_status, "detail": "Task finished"})

    except Exception as e:
        task.status = "failed"
        task.add_event({"type": "fatal_error", "error": repr(e)})
        task.result = {"done": {"success": False, "summary": f"Fatal orchestrator error: {e}"}, "verification": {}}
    finally:
        browser.close()


def _final_status(done_result, verification):
    agent_claims_success = bool(done_result.get("success"))
    if not agent_claims_success:
        return "failed"
    if verification.get("checked") is False:
        return "completed_unverified"
    if verification.get("overall_match"):
        return "verified_complete"
    return "verification_mismatch"


def _normalize_amount(s):
    if not s:
        return ""
    return "".join(ch for ch in s if ch.isdigit() or ch == ".")


def _independent_verify(done_result):
    """Checks the ACTUAL state of the internal AP system (ground truth), and cross-
    checks it against the vendor mail oracle - deliberately not trusting anything the
    agent said about its own success."""
    vendor_name = done_result.get("vendor_name")
    invoice_number = done_result.get("invoice_number")
    submitted = done_result.get("submitted_to_ap_system")

    if not submitted:
        # The agent claims it did NOT write anything to the AP system this run (e.g. a
        # read-only check, or it was told to hold off). Don't penalize that as a
        # "mismatch" just because nothing shows up - but DO sanity-check the claim
        # itself against the ground truth, in case the agent is wrong about what it did.
        if not vendor_name or not invoice_number:
            return {
                "checked": False,
                "note": "Agent reports it did not submit anything to the AP system this run, and gave no vendor/invoice to sanity-check that claim against.",
            }
        try:
            ap_resp = requests.get(
                f"{AP_SYSTEM_URL}/api/verify",
                params={"vendor_name": vendor_name, "invoice_number": invoice_number},
                timeout=5,
            ).json()
        except Exception as e:
            return {"checked": False, "note": f"Agent reports no AP submission; could not reach AP system to sanity-check that claim: {e}"}
        if ap_resp.get("found"):
            return {
                "checked": True,
                "overall_match": False,
                "vendor_name": vendor_name,
                "invoice_number": invoice_number,
                "ap_system_entry": ap_resp,
                "discrepancies": ["Agent reported it did NOT submit anything to the AP system, but a matching entry actually exists there."],
            }
        return {
            "checked": True,
            "overall_match": True,
            "vendor_name": vendor_name,
            "invoice_number": invoice_number,
            "discrepancies": [],
            "note": "Agent reports it did not submit anything to the AP system this run - confirmed no matching entry exists, consistent with that claim.",
        }

    if not vendor_name or not invoice_number:
        return {
            "checked": False,
            "note": "Agent reported submitting to the AP system but did not give a vendor_name/invoice_number to verify against it.",
        }

    result = {"checked": True, "vendor_name": vendor_name, "invoice_number": invoice_number, "discrepancies": []}

    try:
        ap_resp = requests.get(
            f"{AP_SYSTEM_URL}/api/verify",
            params={"vendor_name": vendor_name, "invoice_number": invoice_number},
            timeout=5,
        ).json()
    except Exception as e:
        return {"checked": True, "error": f"Could not reach AP system verify API: {e}", "overall_match": False}

    result["ap_system_entry"] = ap_resp
    if not ap_resp.get("found"):
        result["overall_match"] = False
        result["discrepancies"].append("No matching invoice entry found in the AP system at all.")
        return result

    try:
        oracle_resp = requests.get(
            f"{VENDOR_MAIL_URL}/api/latest_invoice", params={"vendor_name": vendor_name}, timeout=5
        ).json()
    except Exception as e:
        oracle_resp = {"found": False, "error": str(e)}
    result["source_oracle"] = oracle_resp

    agent_amount = _normalize_amount(done_result.get("amount", ""))
    stored_amount = _normalize_amount(ap_resp.get("amount", ""))
    if agent_amount and stored_amount and agent_amount != stored_amount:
        result["discrepancies"].append(
            f"Amount agent reported ({done_result.get('amount')}) does not match what was actually stored ({ap_resp.get('amount')})."
        )

    if oracle_resp.get("found"):
        oracle_amount = _normalize_amount(oracle_resp.get("amount", ""))
        if oracle_amount and stored_amount and oracle_amount != stored_amount:
            result["discrepancies"].append(
                f"Amount stored in AP system ({ap_resp.get('amount')}) does not match the latest source invoice ({oracle_resp.get('amount')})."
            )
        if oracle_resp.get("due_date") and ap_resp.get("due_date") and oracle_resp["due_date"] != ap_resp["due_date"]:
            result["discrepancies"].append(
                f"Due date stored ({ap_resp.get('due_date')}) does not match source invoice due date ({oracle_resp.get('due_date')})."
            )
        if oracle_resp.get("invoice_number") and oracle_resp["invoice_number"] != invoice_number:
            result["discrepancies"].append(
                f"Invoice number used ({invoice_number}) is not the latest invoice number per the source ({oracle_resp.get('invoice_number')})."
            )

    result["overall_match"] = len(result["discrepancies"]) == 0
    return result
