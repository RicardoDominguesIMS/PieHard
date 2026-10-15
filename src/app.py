"""Dessert recipe finder web application."""

import argparse
import json
import logging
import os
from pathlib import Path
from typing import Any

from flask import Flask, jsonify, render_template, request

from recipe_assistant import RecipeAssistant
from recipe_chunk_editor import RecipeChunkEditor
from recipe_index import RecipeIndex


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_RESOURCES = PROJECT_ROOT / "resources"
DEFAULT_DATABASE = PROJECT_ROOT / "data" / "recipes.sqlite3"
MAX_REQUEST_BYTES = 64 * 1024


def create_app(
    database_path: Path | None = None, resources_path: Path | None = None
) -> Flask:
    app = Flask(
        __name__,
        template_folder=str(Path(__file__).resolve().parent / "templates"),
        static_folder=str(Path(__file__).resolve().parent / "static"),
    )
    app.config["MAX_CONTENT_LENGTH"] = MAX_REQUEST_BYTES
    index = RecipeIndex(database_path or DEFAULT_DATABASE, resources_path or DEFAULT_RESOURCES)
    assistant = RecipeAssistant(index)
    chunk_editor = RecipeChunkEditor(index)
    app.extensions["recipe_index"] = index
    app.extensions["recipe_assistant"] = assistant
    app.extensions["chunk_editor"] = chunk_editor

    @app.get("/")
    def home() -> str:
        return render_template("index.html")

    @app.get("/api/status")
    def status() -> Any:
        return jsonify({"recipe_count": index.count()})

    @app.post("/api/index")
    def index_recipes() -> Any:
        try:
            return jsonify(index.index_resources())
        except Exception:
            app.logger.exception("Recipe indexing failed.")
            return jsonify({"error": "Recipe indexing failed. Check the application logs."}), 500

    @app.post("/api/search")
    def search_recipes() -> Any:
        payload = _json_payload()
        query = payload.get("query")
        if not isinstance(query, str) or len(query) > 4000:
            return jsonify({"error": "Please enter a recipe request of 4,000 characters or fewer."}), 400
        try:
            return jsonify(assistant.start_search(query))
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        except RuntimeError as exc:
            app.logger.warning("Recipe search service unavailable: %s", exc)
            return jsonify({"error": str(exc)}), 503
        except Exception:
            app.logger.exception("Recipe search failed.")
            return jsonify({"error": "Recipe search failed. Check the application logs."}), 500

    @app.post("/api/clarify")
    def clarify_search() -> Any:
        payload = _json_payload()
        session_id = payload.get("session_id")
        answer = payload.get("answer")
        if not isinstance(session_id, str) or not isinstance(answer, str):
            return jsonify({"error": "A search session and clarification answer are required."}), 400
        if len(answer) > 2000:
            return jsonify({"error": "Please keep your answer to 2,000 characters or fewer."}), 400
        try:
            return jsonify(assistant.answer_clarification(session_id, answer))
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        except RuntimeError as exc:
            app.logger.warning("Recipe search service unavailable: %s", exc)
            return jsonify({"error": str(exc)}), 503
        except Exception:
            app.logger.exception("Recipe clarification failed.")
            return jsonify({"error": "Recipe clarification failed. Check the application logs."}), 500

    @app.get("/backoffice")
    def backoffice() -> str:
        return render_template("backoffice.html")

    @app.get("/api/chunks")
    def list_chunks() -> Any:
        return jsonify({"chunks": index.list_chunks()})

    @app.post("/api/chunks/<recipe_id>/update")
    def update_chunk(recipe_id: str) -> Any:
        payload = _json_payload()
        instruction = payload.get("instruction")
        if not isinstance(instruction, str):
            return jsonify({"error": "Please describe the change you want to make."}), 400
        try:
            return jsonify(chunk_editor.update_chunk(recipe_id, instruction))
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        except LookupError as exc:
            return jsonify({"error": str(exc)}), 404
        except RuntimeError as exc:
            app.logger.warning("Chunk update service unavailable: %s", exc)
            return jsonify({"error": str(exc)}), 503
        except Exception:
            app.logger.exception("Chunk update failed.")
            return jsonify({"error": "The chunk update failed. Check the application logs."}), 500

    @app.errorhandler(413)
    def request_too_large(_: Any) -> Any:
        return jsonify({"error": "The submitted request is too large."}), 413

    def _json_payload() -> dict[str, Any]:
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict):
            raise ValueError("Request body must be a JSON object.")
        return payload

    @app.errorhandler(ValueError)
    def invalid_json_request(error: ValueError) -> Any:
        return jsonify({"error": str(error)}), 400

    return app


def main() -> None:
    parser = argparse.ArgumentParser(description="Dessert recipe finder")
    parser.add_argument(
        "command",
        nargs="?",
        choices=("serve", "index"),
        default="serve",
        help="index recipe documents or run the web application",
    )
    arguments = parser.parse_args()
    app = create_app()
    if arguments.command == "index":
        summary = app.extensions["recipe_index"].index_resources()
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        if summary["errors"]:
            raise SystemExit(1)
        return

    app.run(
        host=os.environ.get("HOST", "127.0.0.1"),
        port=int(os.environ.get("PORT", "5000")),
        debug=False,
    )


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    main()
