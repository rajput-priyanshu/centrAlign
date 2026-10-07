"""Tool schemas (Anthropic tool-use format) and the router that executes them.

Tools are intentionally domain-agnostic (browser_*, remember/recall, ask_user, done) -
only the system prompt and seed data are specific to the invoice workflow. Swapping in
a different task/environment should not require touching this file. See README
"Generalization" section.
"""

from .browser import ToolError

TOOL_DEFINITIONS = [
    {
        "name": "browser_navigate",
        "description": "Navigate the browser to a URL. Returns a snapshot of the resulting page.",
        "input_schema": {
            "type": "object",
            "properties": {"url": {"type": "string"}},
            "required": ["url"],
        },
    },
    {
        "name": "browser_click",
        "description": (
            "Click a link or button on the current page, identified by its visible text "
            "(e.g. 'Open attachment', 'Submit invoice'). Returns a snapshot of the resulting page."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"description": {"type": "string", "description": "Visible text of the link/button to click"}},
            "required": ["description"],
        },
    },
    {
        "name": "browser_type",
        "description": (
            "Type text into a form field on the current page, identified by its label, "
            "placeholder, or name (e.g. 'Username', 'Invoice number'). Optionally click a "
            "button afterwards (e.g. to submit). Returns a snapshot of the resulting page."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "field": {"type": "string", "description": "Label/placeholder/name of the field"},
                "text": {"type": "string"},
                "submit_label": {
                    "type": "string",
                    "description": "Optional: visible text of a button to click right after typing",
                },
            },
            "required": ["field", "text"],
        },
    },
    {
        "name": "browser_read",
        "description": "Re-read the current page without taking any action. Returns a snapshot.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "remember",
        "description": "Save a fact you discovered (e.g. an extracted amount or due date) under a short key, so you can recall it later without re-reading the page.",
        "input_schema": {
            "type": "object",
            "properties": {"key": {"type": "string"}, "value": {"type": "string"}},
            "required": ["key", "value"],
        },
    },
    {
        "name": "recall",
        "description": "Retrieve a previously remembered fact by key. Omit key to list everything remembered so far.",
        "input_schema": {
            "type": "object",
            "properties": {"key": {"type": "string"}},
            "required": [],
        },
    },
    {
        "name": "ask_user",
        "description": (
            "Pause the task and ask the human a clarifying question or request approval. "
            "Use this when data is missing/ambiguous/contradictory, an action is risky or "
            "hard to reverse, or you are stuck after reasonable attempts. The task will "
            "resume with the human's answer as the result of this tool call."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"question": {"type": "string"}},
            "required": ["question"],
        },
    },
    {
        "name": "done",
        "description": "End the task. Call exactly once, whether you succeeded or not.",
        "input_schema": {
            "type": "object",
            "properties": {
                "success": {"type": "boolean"},
                "summary": {"type": "string", "description": "Concise human-readable summary of what you did and the outcome"},
                "submitted_to_ap_system": {
                    "type": "boolean",
                    "description": (
                        "True ONLY if you actually submitted/recorded an invoice into the AP system "
                        "during this task and confirmed it appears there. False if you only looked "
                        "something up, were told to hold off, or could not complete the write."
                    ),
                },
                "vendor_name": {"type": "string"},
                "invoice_number": {"type": "string"},
                "amount": {"type": "string"},
                "due_date": {"type": "string"},
                "reason_if_failed": {"type": "string"},
            },
            "required": ["success", "summary", "submitted_to_ap_system"],
        },
    },
]


class ToolRouter:
    def __init__(self, browser, memory, ask_user_fn, on_event):
        self.browser = browser
        self.memory = memory
        self.ask_user_fn = ask_user_fn
        self.on_event = on_event
        self.done_result = None

    def execute(self, name, args):
        """Returns (content_blocks, is_error). content_blocks is a list of Anthropic
        content blocks (text and/or image) suitable for a tool_result."""
        try:
            if name == "browser_navigate":
                self.browser.navigate(args["url"])
                return self._observation_blocks(), False
            if name == "browser_click":
                self.browser.click(args["description"])
                return self._observation_blocks(), False
            if name == "browser_type":
                self.browser.type(args["field"], args["text"], args.get("submit_label"))
                return self._observation_blocks(), False
            if name == "browser_read":
                return self._observation_blocks(), False
            if name == "remember":
                self.memory.remember(args["key"], args["value"])
                return [{"type": "text", "text": f"Remembered {args['key']!r}."}], False
            if name == "recall":
                key = args.get("key")
                value = self.memory.recall(key) if key else self.memory.all()
                return [{"type": "text", "text": f"{value!r}"}], False
            if name == "ask_user":
                answer = self.ask_user_fn(args["question"])
                return [{"type": "text", "text": f"Human answered: {answer}"}], False
            if name == "done":
                self.done_result = args
                return [{"type": "text", "text": "Task marked done."}], False
            return [{"type": "text", "text": f"Unknown tool {name!r}"}], True
        except ToolError as e:
            self.on_event({"type": "tool_error", "tool": name, "error": str(e)})
            return [{"type": "text", "text": f"Error: {e}"}], True
        except Exception as e:
            self.on_event({"type": "tool_error", "tool": name, "error": repr(e)})
            return [{"type": "text", "text": f"Unexpected error: {e}"}], True

    def _observation_blocks(self):
        import os as _os
        obs = self.browser.observe()
        event_payload = {k: v for k, v in obs.items() if k != "screenshot_b64"}
        event_payload["screenshot_filename"] = _os.path.basename(obs["screenshot_path"])
        self.on_event({"type": "observation", **event_payload})
        summary = (
            f"URL: {obs['url']}\nTitle: {obs['title']}\n"
            f"Links: {obs['links']}\nButtons: {obs['buttons']}\nInput fields: {obs['input_fields']}\n"
            f"Visible text: {obs['visible_text']}"
        )
        return [
            {"type": "text", "text": summary},
            {
                "type": "image",
                "source": {"type": "base64", "media_type": "image/png", "data": obs["screenshot_b64"]},
            },
        ]
