"""Dashboard: submit a natural-language task, watch the agent work step by step,
answer clarification questions, and see the final verified report. This is the
orchestrator's control surface, not part of the simulated company environment."""
import os
import sys

from dotenv import load_dotenv

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
load_dotenv(os.path.join(BASE_DIR, ".env"))
sys.path.insert(0, BASE_DIR)

from flask import Flask, render_template, request, jsonify, send_from_directory

from agent import orchestrator

app = Flask(__name__)


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/task/<task_id>")
def task_page(task_id):
    return render_template("task.html", task_id=task_id)


@app.route("/api/tasks", methods=["GET", "POST"])
def api_tasks():
    if request.method == "POST":
        goal = (request.json or {}).get("goal", "").strip()
        if not goal:
            return jsonify({"error": "goal is required"}), 400
        task = orchestrator.create_task(goal)
        return jsonify({"id": task.id})
    tasks = [t.to_public_dict() for t in orchestrator.list_tasks()]
    for t in tasks:
        t.pop("trace", None)
    return jsonify(tasks)


@app.route("/api/tasks/<task_id>")
def api_task_detail(task_id):
    task = orchestrator.get_task(task_id)
    if not task:
        return jsonify({"error": "not found"}), 404
    return jsonify(task.to_public_dict())


@app.route("/api/tasks/<task_id>/answer", methods=["POST"])
def api_task_answer(task_id):
    answer = (request.json or {}).get("answer", "")
    ok = orchestrator.answer_question(task_id, answer)
    if not ok:
        return jsonify({"error": "task is not waiting for input"}), 400
    return jsonify({"ok": True})


@app.route("/evidence/<task_id>/<filename>")
def evidence_file(task_id, filename):
    evidence_dir = os.path.join(BASE_DIR, "evidence", task_id)
    return send_from_directory(evidence_dir, filename)


if __name__ == "__main__":
    port = int(os.environ.get("DASHBOARD_PORT", 5000))
    app.run(host="127.0.0.1", port=port, debug=False, threaded=True)
