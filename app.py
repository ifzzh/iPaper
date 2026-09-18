
from ipaper.environment import getenv as brand_getenv
import argparse
import atexit
import json
import os
import sqlite3
import threading
import time
import uuid
from datetime import datetime, timezone
from functools import partial
from typing import Optional

from dotenv import load_dotenv

load_dotenv()

import re
import requests
from flask import Flask, current_app, g, jsonify, make_response, redirect, render_template, request

from ipaper.logging_setup import install_app_logging
from ipaper.workbench import register_workbench, render_workspace

install_app_logging()
from ipaper.core.base_paper import Paper
from ipaper.auth import (
    AuthConfig,
    AuthConfigurationError,
    FixedWindowRateLimiter,
)
from ipaper.local_auth import LocalAuthError, LocalAuthService
from ipaper.security.identity import (
    DEVELOPMENT_USER_ID,
    Identity,
    current_user_id,
    set_background_identity,
)
from ipaper.security.paths import UserScopedPath, paper_directory
from ipaper.security.agentic_credentials import AgenticCredentialStore
from ipaper.security.credentials import CredentialError
from ipaper.security.outbound import DynamicOutboundPolicy, OutboundPolicyError
from ipaper.migrations.agentic_secrets import (
    AgenticSecretMigrationError,
    assert_no_plaintext_credentials,
)
from ipaper.migrations.tenant_storage import (
    TenantMigrationError,
    assert_tenant_migrated,
)
from ipaper.core.paper_store import paper_store
from ipaper.core.search_index import TenantSearchIndex
from ipaper.database.connection import DB_PATH
from ipaper.database.connection import init_db as register_db_teardown
from ipaper.database.dao.settings_dao import SettingsDAO
from ipaper.document_worker.client import DocumentWorkerClient
from ipaper.runtime.task_queue import BoundedExecutor, QueueFull
from ipaper.tools.basic_tools.daily_arxiv_assets import DailyAssetCoordinator
from ipaper.tools.agent_tools.translation_worker_client import TranslationWorkerClient
from ipaper.database.db_manager import init_db_schema
from ipaper.routes.agent_routes.agent_summary_route import (
    register_agent_summary_routes,
)
from ipaper.routes.agent_routes.agent_chat_route import (
    register_agent_chat_routes,
)
from ipaper.routes.agent_routes.agent_translate_route import (
    register_agent_translate_routes,
)
from ipaper.routes.basic_routes.category_tree_route import register_category_routes
from ipaper.routes.basic_routes.daily_arxiv_route import register_daily_arxiv_routes
from ipaper.routes.basic_routes.export_route import register_export_routes
from ipaper.routes.basic_routes.import_route import register_import_routes
from ipaper.routes.basic_routes.institution_mapping_route import (
    register_institution_mapping_routes,
)
from ipaper.routes.basic_routes.paper_operation_route import (
    register_paper_operation_routes,
)
from ipaper.routes.basic_routes.search_route import register_search_routes
from ipaper.routes.basic_routes.settings_route import register_settings_routes
from ipaper.routes.basic_routes.update_from_url_route import (
    register_update_from_url_routes,
)
from ipaper.routes.basic_routes.upload_from_pdf_route import (
    register_upload_from_pdf_routes,
)
from ipaper.tools.basic_tools import category_manager, paper_repository
from ipaper.tools.basic_tools.daily_arxiv import (
    DEFAULT_MAX_DAILY_PAPERS,
    DEFAULT_MAX_NEW_PAPERS_PER_CATEGORY_PER_FETCH,
    DEFAULT_REPLACEMENT_CANDIDATE_LIMIT,
)
from ipaper.tools.basic_tools.daily_arxiv_quality import get_default_quality_config
from ipaper.tools.basic_tools.daily_arxiv_profile import DEFAULT_RESEARCH_TOPICS

parser = argparse.ArgumentParser(description="iPaper")
parser.add_argument(
    "--papers-dir",
    type=str,
    default="./papers",
    help="iPaper papers directory path (default: ./papers)",
)
parser.add_argument(
    "--host",
    type=str,
    default="0.0.0.0",
    help="Server listening address (default: 0.0.0.0)",
)
parser.add_argument(
    "--port", type=int, default=7191, help="Server listening port (default: 7191)"
)
parser.add_argument("--debug", action="store_true", help="Enable debug mode")

app = Flask(__name__)
register_workbench(app)
app.config["IPAPER_START_BACKGROUND_TASKS"] = True
register_db_teardown(app)
app.config["MAX_CONTENT_LENGTH"] = 210 * 1024 * 1024

AUTH_CONFIG: Optional[AuthConfig] = None
AUTH_COOKIE_NAME = "paperpilot_session"
CSRF_COOKIE_NAME = "paperpilot_csrf"
_rate_limiter = FixedWindowRateLimiter()
AUTH_SERVICE: Optional[LocalAuthService] = None
AGENTIC_CREDENTIAL_STORE: Optional[AgenticCredentialStore] = None
OUTBOUND_POLICY: Optional[DynamicOutboundPolicy] = None


def _browser_security_headers() -> dict[str, str]:
    csp = "; ".join(
        [
            "default-src 'self'",
            "base-uri 'self'",
            "object-src 'none'",
            "frame-ancestors 'self'",
            "form-action 'self'",
            "script-src 'self'",
            "script-src-attr 'none'",
            "style-src 'self'",
            "style-src-attr 'none'",
            "font-src 'self' data:",
            "img-src 'self' data: blob:",
            "connect-src 'self'",
            "frame-src 'self' blob:",
            "worker-src 'self' blob:",
        ]
    )
    return {
        "X-Content-Type-Options": "nosniff",
        "Referrer-Policy": "same-origin",
        "X-Frame-Options": "SAMEORIGIN",
        "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
        "Content-Security-Policy": csp,
    }


def _authenticate_session(session_token: str | None):
    if not session_token:
        g.audit_reason = "missing_token"
        return None, (jsonify({"error": "未登录"}), 401)
    identity = AUTH_SERVICE.authenticate(session_token) if AUTH_SERVICE else None
    if identity is None:
        g.audit_reason = "invalid_session"
        return None, (jsonify({"error": "登录已失效"}), 401)
    g.identity = identity
    g.user_id = identity.user_id
    g.username = identity.username
    g.user_role = identity.role
    g.session_token = session_token
    return identity, None


def _rate_limit_response(bucket: str, identity: str, limit: int, window: int):
    result = _rate_limiter.check(
        bucket,
        identity,
        limit=limit,
        window_seconds=window,
    )
    if result.allowed:
        return None
    g.audit_reason = f"rate_limit:{bucket}"
    response = jsonify({"error": "请求过于频繁，请稍后重试"})
    response.status_code = 429
    response.headers["Retry-After"] = str(result.retry_after)
    return response


def _sensitive_rate_policy():
    path = request.path
    if request.endpoint and request.endpoint.startswith("topics."):
        if request.endpoint == "topics.create_job":
            return "topic_jobs", 60, 3600
        if request.method in {"POST", "PATCH", "PUT", "DELETE"}:
            return "topic_edits", 180, 60
    if request.endpoint and request.endpoint.startswith("keywords."):
        if request.endpoint == "keywords.create":
            return "keyword_jobs", 60, 3600
        if request.method in {"POST", "PATCH", "PUT", "DELETE"}:
            return "keyword_edits", 180, 60
    if request.endpoint in {"workspace_state", "reading_position", "api_record_read_time", "api_record_reading", "processing.understanding_position", "processing.document_position", "processing.position"} and request.method in {"POST", "PUT"}:
        return "reading_state", 120, 60
    if request.endpoint in {"processing.document", "processing.search", "processing.selection_preview"} and request.method == "POST":
        return "reading_tools", 120, 60
    if request.endpoint in {"processing.bookmarks", "processing.bookmark"} and request.method in {"POST", "PUT", "DELETE"}:
        return "reading_bookmarks", 120, 60
    if request.endpoint in {"processing.create", "processing.selection_create"} and request.method == "POST":
        return "processing", 30, 3600
    if request.endpoint and request.endpoint.startswith("metadata."):
        if request.method in {"POST","PATCH"}:
            return "bibliography", 120, 3600
        if request.endpoint == "metadata.citation":
            return "bibliography_export", 120, 60
    if path in {
        "/api/paper/analyze",
        "/api/paper/translate",
        "/api/paper/chat",
        "/api/daily-arxiv/extract-affiliations",
        "/api/daily-arxiv/generate-summary",
    }:
        return "ai", 30, 3600
    if (
        path in {"/api/upload", "/api/upload/arxiv", "/api/export/start"}
        or path.startswith("/api/import/")
    ):
        return "data_transfer", 20, 3600
    if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
        return "mutation", 60, 3600
    return None


@app.before_request
def _require_auth_for_api():
    g.request_id = uuid.uuid4().hex
    protects_api = request.path.startswith("/api/")
    if not protects_api:
        return None

    if request.path in {"/healthz", "/readyz"} and request.method in {"GET", "HEAD"}:
        return None

    if request.path in {
        "/api/auth/login", "/api/auth/register", "/api/auth/reset-password"
    }:
        return None

    if AUTH_CONFIG is None:
        g.audit_reason = "auth_not_configured"
        return jsonify({"error": "鉴权服务未配置"}), 503
    if not AUTH_CONFIG.enabled:
        g.identity = Identity(DEVELOPMENT_USER_ID, "development", "admin")
        g.user_id = DEVELOPMENT_USER_ID
        g.username = "development"
        g.user_role = "admin"
        return None

    session_token = request.cookies.get(AUTH_COOKIE_NAME)
    identity, failure = _authenticate_session(session_token)
    if failure is not None:
        if request.path == "/api/auth/session" and request.method == "GET":
            return jsonify({"authenticated": False})
        return failure

    if request.method not in {"GET", "HEAD", "OPTIONS"}:
        csrf = request.headers.get("X-CSRF-Token")
        if AUTH_SERVICE is None or not AUTH_SERVICE.verify_csrf(session_token, csrf):
            g.audit_reason = "csrf_failed"
            return jsonify({"error": "csrf_failed"}), 403

    if identity.must_change_password and request.path not in {
        "/api/auth/session", "/api/auth/change-password"
    }:
        g.audit_reason = "password_change_required"
        return jsonify({"error": "password_change_required"}), 403

    if request.path.startswith("/api/admin/") and identity.role != "admin":
        g.audit_reason = "administrator_required"
        return jsonify({"error": "administrator_required"}), 403

    policy = _sensitive_rate_policy()
    if policy is not None:
        bucket, limit, window = policy
        limited = _rate_limit_response(bucket, identity.user_id, limit, window)
        if limited is not None:
            return limited
    return None


def _private_paper_asset_response(response):
    # Apply after authentication as well, so denied/range/error responses cannot be cached.
    if re.fullmatch(r"/api/paper/[^/]+/(?:chinese/)?file", request.path):
        response.headers["Cache-Control"] = "private, no-store"
        response.vary.add("Cookie")
    return response


@app.after_request
def _audit_sensitive_request(response):
    request_id = getattr(g, "request_id", uuid.uuid4().hex)
    response.headers["X-Request-ID"] = request_id
    for header, value in _browser_security_headers().items():
        response.headers[header] = value
    _private_paper_asset_response(response)
    is_api = request.path.startswith("/api/")
    should_audit = is_api and (
        request.method in {"POST", "PUT", "PATCH", "DELETE"}
        or response.status_code in {401, 403, 429, 503}
    )
    if should_audit:
        record = {
            "event": "api_audit",
            "request_id": request_id,
            "method": request.method,
            "endpoint": request.endpoint or "unmatched",
            "status": response.status_code,
            "user": getattr(g, "username", "anonymous"),
            "remote_addr": request.remote_addr or "unknown",
            "reason": getattr(g, "audit_reason", "completed"),
            "timestamp": datetime.now(timezone.utc)
            .isoformat(timespec="seconds")
            .replace("+00:00", "Z"),
        }
        print(json.dumps(record, ensure_ascii=True, sort_keys=True), flush=True)
    return response

# Configuration file storage path (will be set in main function according to parameters)
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
UPLOAD_FOLDER = None  # Will be set in main
CATEGORIES_FILE = None  # Will be set in main
READING_LIST_FILE = None  # Will be set in main
USER_SETTINGS_FILE = None  # User settings (name, avatar, etc.)
READING_HISTORY_FILE = None  # Daily reading history
AGENTIC_SETTINGS_FILE = None  # AI feature settings (uniform LLM configuration)
DAILY_ARXIV_SETTINGS_FILE = None  # Daily arXiv settings
AVATARS_DIR = None  # Avatar image directory
TEMP_PAPERS_DIR = None  # Daily arXiv temporary paper directory
READING_LIST_TEMP_DIR = None  # Reading list temporary paper directory
SEARCH_INDEX_DB = None  # Search index database path
search_index = None  # Search index instance
# No longer use the unified papers_db.json file, use one JSON file for each PDF
# Default user settings
DEFAULT_USER_SETTINGS = {
    "name": "Paper Reader",
    "avatar": None,  # Avatar file name, e.g. "avatar.jpg"
    "heatmapColorScheme": "green",
    "onboardingDontShow": False,  # Whether to no longer show the新手教程
    "aiLanguage": "zh",  # AI output language (en/zh), applies to AI translation, AI interpretation, and Daily arXiv summary
}

# Default Agentic settings (uniform AI feature configuration)
DEFAULT_AGENTIC_SETTINGS = {
    "llmConfigs": {
        "translate": {
            "llmModel": "",
            "llmBaseUrl": "",
        },
        "interpret": {
            "llmModel": "",
            "llmBaseUrl": "",
        },
        "dailyArxiv": {
            "llmModel": "",
            "llmBaseUrl": "",
        },
    },
    "mineruServerUrl": "",  # PDF parsing service address (for local mode)
    "mineruUseApi": False,  # Toggle between local CLI mode and cloud API mode
    # Note: System prompts are now built-in and selected based on user's aiLanguage setting
    # Custom prompts are no longer supported
}

# Default Daily arXiv settings
DEFAULT_DAILY_ARXIV_SETTINGS = {
    "enabled": False,  # Default to disabled
    "categories": ["cs.RO", "cs.CV", "cs.AI", "cs.LG", "cs.DC", "cs.NI", "cs.OS", "cs.PF", "cs.CL"],
    "checkIntervalMinutes": 30,  # Check interval (minutes)
    "retentionDays": 7,  # arXiv release dates retained
    "maxDailyPapers": 24,
    "maxNewPapersPerCategoryPerFetch": DEFAULT_MAX_NEW_PAPERS_PER_CATEGORY_PER_FETCH,
    "replacementCandidateLimit": DEFAULT_REPLACEMENT_CANDIDATE_LIMIT,
    "researchTopics": DEFAULT_RESEARCH_TOPICS,
    "topicFilteringEnabled": True,
    "maxKeywords": 2,  # Maximum number of keywords (1-3)
    "keywordList": [
        "LLM",
        "MLLM",
        "Agent",
        "Image Generation",
        "Video Generation",
        "3D Generation",
        "2D Perception",
        "3D Perception",
        "Embodied AI & Robotics",
        "Audio & Speech",
        "ML Fundamentals & RL",
    ],  # Keyword list
    "qualityConfig": get_default_quality_config(),
    "affiliationPrompt": """I will provide you with the first-page information of a paper. You need to extract all affiliations (institution names) from it and also extract the homepage and GitHub repo URL if there is. For affiliations, do not include author names. If an affiliation includes details such as region, department, school, or college, those should be omitted. Only keep the main institution name (e.g., School of Computer Science, Fudan University → Fudan University).

Additional rules:

If the institution is well-known and has a commonly used abbreviation (e.g., University of Illinois Urbana–Champaign → UIUC), return the abbreviation instead of the full name. If the institution is not well-known or does not have a standard abbreviation, keep the full name.

You must also return the nationality (country) for each affiliation, in the same order.

Output the result directly in JSON format, and make sure it is valid JSON. The structure should be:

{
"affiliations": ["Affiliation1", "Affiliation2", ...],
"countries": ["Country_of_Affiliation1", "Country_of_Affiliation2", ...],
"homepage": "homepage_url_or_null",
"github": "github_url_or_null"
}

Notes:
1. If there is no homepage or github url, use the JSON value null (not the string "null" and not Python None).
2. Do NOT add a trailing comma after the last field.
3. Do not include any explanation or extra text, only output the JSON object.

Now the input is:
""",
    # Summary prompts - language-specific (built-in, not customizable)
    "summaryPromptZh": """我会给你一篇 AI 文章的英文摘要，以及一个可选关键词列表（英文）。你需要：

用中文简要总结这篇文章在解决什么问题、如何解决的，字数控制在 100-200 字。

从我提供的关键词列表中挑选最能代表文章类型的关键词（英文）

按如下 JSON 格式输出结果：

{"summary": "这篇文章主要解决...的问题。作者提出...方法，通过...实现了...", "keywords": ["Keyword"]}

注意

summary 必须中文，简洁、客观。

keywords 必须来自我提供的关键词列表：[{keyword_list}], 最多{max_keywords}个关键词。一定要是符合这篇文章的关键词，不能随意猜测。

直接输出 JSON，不要有其他解释。

现在输入的摘要是：
""",
    "summaryPromptEn": """I will give you an English abstract of an AI paper, and an optional keyword list (in English). You need to:

Briefly summarize in English what problem this paper solves and how it solves it, keep it within 100-200 words.

Select keywords (in English) from the keyword list I provide that best represent the type of paper.

Output the result in the following JSON format:

{"summary": "This paper mainly solves...problem. The authors propose...method, through...achieved...", "keywords": ["Keyword"]}

Notes:

summary must be in English, concise and objective.

keywords must come from the keyword list I provide: [{keyword_list}], at most {max_keywords} keywords. They must be keywords that match this paper, do not guess randomly.

Output JSON directly, no other explanations.

Now the input abstract is:
""",
}

# Global variables (will be initialized in init_app)
init_categories = None
get_categories = None
save_categories = None
create_category_folder = None
find_category_node = category_manager.find_category_node
get_category_path = category_manager.get_category_path
add_pdf_counts_to_categories = category_manager.add_pdf_counts_to_categories
get_category_pdf_count = category_manager.get_category_pdf_count

get_papers_in_category = None
load_paper_metadata = paper_repository.load_paper_metadata
scan_papers_in_directory = paper_repository.scan_papers_in_directory


def save_paper_metadata(pdf_path: str, paper_data) -> None:
    """Save paper metadata and update search index"""
    paper_repository.save_paper_metadata(
        pdf_path,
        paper_data,
        upload_root=UPLOAD_FOLDER,
    )

    # Update search index
    if search_index:
        try:
            if isinstance(paper_data, Paper):
                paper = paper_data
            else:
                paper = Paper.from_dict(paper_data) if paper_data else None

            if paper:
                # First try to get the latest category ID from paper_store (most accurate)
                category_id = None
                entry = paper_store.get_entry(paper.id)
                if entry:
                    category_id = entry.category_id

                # If paper_store does not have it, try to get it from the paper data
                if not category_id:
                    if hasattr(paper, "category_id") and paper.category_id:
                        category_id = paper.category_id
                    elif isinstance(paper_data, dict):
                        category_id = paper_data.get("category_id")

                search_index.index_paper(paper, category_id)
        except Exception as e:
            print(f"Failed to update search index: {e}")


def delete_paper_files(pdf_path: str) -> None:
    """Delete paper files and remove from search index"""
    # First try to get the paper ID (if possible)
    paper_id = None
    try:
        paper = load_paper_metadata(pdf_path)
        if paper:
            paper_id = paper.id
    except Exception:
        pass

    # Delete files
    paper_repository.delete_paper_files(UPLOAD_FOLDER, pdf_path)

    # Remove from search index
    if paper_id and search_index:
        try:
            search_index.remove_paper(paper_id)
        except Exception as e:
            print(f"Failed to remove from search index: {e}")


def init_app(papers_dir=None):
    """Initialize application configuration and directories"""
    os.makedirs(os.path.dirname(os.path.abspath(DB_PATH)), exist_ok=True)
    init_db_schema(DB_PATH)

    global UPLOAD_FOLDER, CATEGORIES_FILE, READING_LIST_FILE
    global USER_SETTINGS_FILE, READING_HISTORY_FILE, AGENTIC_SETTINGS_FILE, AVATARS_DIR
    global DAILY_ARXIV_SETTINGS_FILE, TEMP_PAPERS_DIR, READING_LIST_TEMP_DIR
    global SEARCH_INDEX_DB, search_index
    global init_categories, get_categories, save_categories, create_category_folder, get_papers_in_category

    # Set paper directory
    if papers_dir:
        # If a relative path is specified, it is relative to the current working directory
        if not os.path.isabs(papers_dir):
            UPLOAD_FOLDER = os.path.abspath(papers_dir)
        else:
            UPLOAD_FOLDER = papers_dir
    else:
        UPLOAD_FOLDER = os.path.join(BASE_DIR, "papers")

    # Configuration files are all in the paper directory
    CATEGORIES_FILE = UserScopedPath(UPLOAD_FOLDER, "categories.json")
    READING_LIST_FILE = UserScopedPath(UPLOAD_FOLDER, "reading_list.json")
    USER_SETTINGS_FILE = UserScopedPath(UPLOAD_FOLDER, "user_settings.json")
    READING_HISTORY_FILE = UserScopedPath(UPLOAD_FOLDER, "reading_history.json")
    AGENTIC_SETTINGS_FILE = UserScopedPath(UPLOAD_FOLDER, "agentic_settings.json")
    DAILY_ARXIV_SETTINGS_FILE = UserScopedPath(UPLOAD_FOLDER, "daily_arxiv_settings.json")
    AVATARS_DIR = UserScopedPath(UPLOAD_FOLDER, ".avatars")
    TEMP_PAPERS_DIR = UserScopedPath(UPLOAD_FOLDER, ".daily_arxiv_temp")
    READING_LIST_TEMP_DIR = UserScopedPath(UPLOAD_FOLDER, "_ReadingListTemp")
    SEARCH_INDEX_DB = os.path.join(UPLOAD_FOLDER, ".users")

    # Ensure necessary directories exist
    os.makedirs(UPLOAD_FOLDER, exist_ok=True)
    os.makedirs(AVATARS_DIR, exist_ok=True)
    os.makedirs(TEMP_PAPERS_DIR, exist_ok=True)
    os.makedirs(READING_LIST_TEMP_DIR, exist_ok=True)

    # Initialize search index
    search_index = TenantSearchIndex(UPLOAD_FOLDER)

    # Initialize Daily arXiv settings file (still file-based)
    if not os.path.exists(DAILY_ARXIV_SETTINGS_FILE):
        with open(DAILY_ARXIV_SETTINGS_FILE, "w", encoding="utf-8") as f:
            json.dump(DEFAULT_DAILY_ARXIV_SETTINGS, f, ensure_ascii=False, indent=2)


    # Bind basic tool functions
    init_categories = partial(category_manager.init_categories, CATEGORIES_FILE)
    get_categories = partial(category_manager.get_categories, CATEGORIES_FILE)
    save_categories = partial(category_manager.save_categories, CATEGORIES_FILE)
    create_category_folder = partial(
        category_manager.create_category_folder, UPLOAD_FOLDER
    )
    get_papers_in_category = partial(
        paper_repository.get_papers_in_category, UPLOAD_FOLDER
    )

    print("iPaper managed storage initialized")
    print("Settings storage initialized")


# Translation task management
translation_tasks = (
    {}
)  # {task_id: {paper_id, process, logs, status, start_time, log_lock}}
translation_tasks_lock = threading.Lock()  # Protect translation task dictionary

# AI interpretation task management
analysis_tasks = (
    {}
)  # {task_id: {paper_id, process, logs, status, start_time, log_lock, step}}
analysis_tasks_lock = threading.Lock()  # Protect interpretation task dictionary

_application_lock = threading.RLock()
_application_initialized = False
_application_papers_dir: Optional[str] = None
_daily_arxiv_manager = None
_daily_asset_coordinator = None
_shutdown_registered = False
_analysis_executor = BoundedExecutor(
    max_workers=1, max_queue=2, thread_name_prefix="analysis"
)
_export_executor = BoundedExecutor(
    max_workers=1, max_queue=2, thread_name_prefix="export"
)
_daily_arxiv_executor = BoundedExecutor(
    max_workers=1, max_queue=1, thread_name_prefix="daily-arxiv"
)
_daily_aux_executor = BoundedExecutor(
    max_workers=2, max_queue=8, thread_name_prefix="daily-aux"
)
_search_executor = BoundedExecutor(
    max_workers=1, max_queue=1, thread_name_prefix="search-rebuild"
)
_search_rebuild_lock = threading.Lock()
_search_rebuild_running = False
_search_rebuild_pending = False


@app.route("/")
def index():
    return render_workspace()


@app.route("/healthz", methods=["GET"])
def healthz():
    return jsonify({"status": "ok"})


@app.route("/readyz", methods=["GET"])
def readyz():
    components = {
        "database": "unavailable",
        "translation_worker": "unavailable",
        "document_worker": "unavailable",
    }
    try:
        with sqlite3.connect(DB_PATH, timeout=3) as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "CREATE TABLE __ipaper_readiness_probe (probe INTEGER)"
            )
            connection.rollback()
        components["database"] = "ok"
    except (OSError, sqlite3.Error):
        pass
    if TranslationWorkerClient().health():
        components["translation_worker"] = "ok"
    if DocumentWorkerClient().health():
        components["document_worker"] = "ok"
    ready = all(value == "ok" for value in components.values())
    return jsonify({"status": "ready" if ready else "not_ready", **components}), (
        200 if ready else 503
    )


@app.route("/api/papers-dir", methods=["GET"])
def get_papers_dir():
    """Describe managed storage without disclosing its server path."""
    return jsonify({"success": True, "storage": "managed"})


def _auth_payload(identity: Identity, csrf_token: str | None = None) -> dict:
    payload = {
        "authenticated": True,
        "user": {
            "id": identity.user_id,
            "username": identity.username,
            "role": identity.role,
            "must_change_password": identity.must_change_password,
        },
    }
    if csrf_token:
        payload["csrf_token"] = csrf_token
    return payload


@app.post("/api/auth/login")
def auth_login():
    if AUTH_CONFIG is None or AUTH_SERVICE is None or not AUTH_CONFIG.enabled:
        return jsonify({"error": "鉴权服务未配置"}), 503
    data = request.get_json(silent=True) or {}
    username = data.get("username")
    limited = _rate_limit_response(
        "auth_login", f"{request.remote_addr or 'unknown'}:{str(username).lower()}", 5, 900
    )
    if limited is not None:
        return limited
    try:
        user, token, csrf = AUTH_SERVICE.login(username, data.get("password"))
    except LocalAuthError:
        g.audit_reason = "invalid_credentials"
        return jsonify({"error": "invalid_credentials"}), 401
    identity = Identity(
        user["id"], user["username"], user["role"], user["must_change_password"]
    )
    response = make_response(jsonify(_auth_payload(identity, csrf)))
    response.set_cookie(
        AUTH_COOKIE_NAME, token, path="/", secure=AUTH_CONFIG.cookie_secure,
        httponly=True, samesite="Lax", max_age=7 * 24 * 3600,
    )
    response.set_cookie(
        CSRF_COOKIE_NAME, csrf, path="/", secure=AUTH_CONFIG.cookie_secure,
        httponly=False, samesite="Lax", max_age=7 * 24 * 3600,
    )
    return response


@app.post("/api/auth/register")
def auth_register():
    if AUTH_CONFIG is None or AUTH_SERVICE is None or not AUTH_CONFIG.enabled:
        return jsonify({"error": "鉴权服务未配置"}), 503
    limited = _rate_limit_response("auth_register", request.remote_addr or "unknown", 5, 900)
    if limited is not None:
        return limited
    data = request.get_json(silent=True) or {}
    try:
        user = AUTH_SERVICE.register(
            data.get("username"), data.get("password"), data.get("invite_code")
        )
        return jsonify({"success": True, "user": user}), 201
    except LocalAuthError:
        # Public registration failures are deliberately indistinguishable so an
        # unauthenticated caller cannot enumerate usernames or invite state.
        g.audit_reason = "registration_failed"
        return jsonify({"error": "registration_failed"}), 400


@app.route("/api/auth/session", methods=["GET", "DELETE"])
def auth_session():
    if AUTH_CONFIG is None:
        return jsonify({"error": "鉴权服务未配置"}), 503
    if not AUTH_CONFIG.enabled:
        return jsonify({"authenticated": False, "auth_disabled": True})
    if request.method == "DELETE":
        AUTH_SERVICE.logout(request.cookies.get(AUTH_COOKIE_NAME))
        response = make_response(jsonify({"success": True}))
        response.delete_cookie(
            AUTH_COOKIE_NAME, path="/", secure=AUTH_CONFIG.cookie_secure,
            httponly=True, samesite="Lax",
        )
        response.delete_cookie(
            CSRF_COOKIE_NAME, path="/", secure=AUTH_CONFIG.cookie_secure,
            httponly=False, samesite="Lax",
        )
        return response
    return jsonify(_auth_payload(g.identity))


@app.post("/api/auth/change-password")
def auth_change_password():
    data = request.get_json(silent=True) or {}
    try:
        token, csrf = AUTH_SERVICE.change_password(
            g.user_id,
            data.get("current_password"),
            data.get("new_password"),
            current_session_token=g.session_token,
        )
    except LocalAuthError as exc:
        return jsonify({"error": exc.reason}), 400
    identity = Identity(g.user_id, g.username, g.user_role, False)
    response = make_response(
        jsonify({
            "success": True,
            "reauthenticate": False,
            "user": _auth_payload(identity)["user"],
        })
    )
    response.set_cookie(
        AUTH_COOKIE_NAME, token, path="/", secure=AUTH_CONFIG.cookie_secure,
        httponly=True, samesite="Lax", max_age=7 * 24 * 3600,
    )
    response.set_cookie(
        CSRF_COOKIE_NAME, csrf, path="/", secure=AUTH_CONFIG.cookie_secure,
        httponly=False, samesite="Lax", max_age=7 * 24 * 3600,
    )
    return response


@app.post("/api/auth/reset-password")
def auth_reset_password():
    if AUTH_CONFIG is None or AUTH_SERVICE is None or not AUTH_CONFIG.enabled:
        return jsonify({"error": "鉴权服务未配置"}), 503
    limited = _rate_limit_response("password_reset", request.remote_addr or "unknown", 5, 900)
    if limited is not None:
        return limited
    data = request.get_json(silent=True) or {}
    try:
        AUTH_SERVICE.reset_password(data.get("reset_code"), data.get("new_password"))
        return jsonify({"success": True})
    except LocalAuthError as exc:
        return jsonify({"error": exc.reason}), 400


@app.route("/api/admin/invites", methods=["GET", "POST"])
def admin_invites():
    if request.method == "GET":
        return jsonify({"invites": AUTH_SERVICE.list_invites()})
    metadata, code = AUTH_SERVICE.create_invite(g.user_id)
    return jsonify({**metadata, "invite_code": code}), 201


@app.delete("/api/admin/invites/<invite_id>")
def admin_revoke_invite(invite_id: str):
    AUTH_SERVICE.revoke_invite(invite_id)
    return jsonify({"success": True})


@app.get("/api/admin/users")
def admin_users():
    return jsonify({"users": AUTH_SERVICE.list_users()})


@app.patch("/api/admin/users/<user_id>")
def admin_update_user(user_id: str):
    data = request.get_json(silent=True) or {}
    if set(data) - {"role", "status"}:
        return jsonify({"error": "unknown_user_fields"}), 400
    try:
        user = AUTH_SERVICE.update_user(
            g.user_id, user_id, role=data.get("role"), status=data.get("status")
        )
        return jsonify({"success": True, "user": user})
    except LocalAuthError as exc:
        return jsonify({"error": exc.reason}), 409 if exc.reason == "last_administrator_required" else 404


@app.post("/api/admin/users/<user_id>/password-reset")
def admin_password_reset(user_id: str):
    try:
        metadata, code = AUTH_SERVICE.create_password_reset(g.user_id, user_id)
        return jsonify({**metadata, "reset_code": code}), 201
    except LocalAuthError as exc:
        return jsonify({"error": exc.reason}), 404


@app.route("/api/admin/ai-providers", methods=["GET", "POST"])
def admin_ai_providers():
    if OUTBOUND_POLICY is None:
        return jsonify({"error": "outbound_policy_unavailable"}), 503
    if request.method == "GET":
        return jsonify({"providers": OUTBOUND_POLICY.list_providers()})
    data = request.get_json(silent=True) or {}
    try:
        provider = OUTBOUND_POLICY.approve_public_url(
            data.get("url"), name=data.get("name")
        )
        return jsonify({"success": True, "provider": provider}), 201
    except OutboundPolicyError as exc:
        return jsonify({"error": exc.reason}), 400


@app.patch("/api/admin/ai-providers/<provider_id>")
def admin_update_ai_provider(provider_id: str):
    data = request.get_json(silent=True) or {}
    if set(data) - {"name", "enabled"}:
        return jsonify({"error": "unknown_provider_fields"}), 400
    try:
        provider = OUTBOUND_POLICY.update_provider(
            provider_id, name=data.get("name"), enabled=data.get("enabled")
        )
        return jsonify({"success": True, "provider": provider})
    except OutboundPolicyError as exc:
        return jsonify({"error": exc.reason}), 404 if exc.reason == "provider_not_found" else 400


@app.delete("/api/admin/ai-providers/<provider_id>")
def admin_delete_ai_provider(provider_id: str):
    OUTBOUND_POLICY.delete_provider(provider_id)
    return jsonify({"success": True})


def register_routes():
    """Register all routes (must be called after init_app)"""
    global _daily_arxiv_manager, _daily_asset_coordinator
    register_category_routes(
        app,
        get_categories=get_categories,
        save_categories=save_categories,
        find_category_node=find_category_node,
        get_category_path=get_category_path,
        get_papers_in_category=get_papers_in_category,
        add_pdf_counts_to_categories=add_pdf_counts_to_categories,
        get_category_pdf_count=get_category_pdf_count,
        paper_store=paper_store,
        upload_folder=UPLOAD_FOLDER,
    )

    register_search_routes(
        app,
        get_categories=get_categories,
        get_category_path=get_category_path,
        upload_folder=UPLOAD_FOLDER,
        search_index=search_index,
    )

    # First register Daily arXiv routes, get manager instance
    from ipaper.tools.basic_tools.daily_arxiv import get_manager

    daily_arxiv_manager = get_manager(TEMP_PAPERS_DIR, DAILY_ARXIV_SETTINGS_FILE)
    _daily_arxiv_manager = daily_arxiv_manager
    daily_arxiv_manager.set_document_client(DocumentWorkerClient())

    # Set LLM configuration callback
    def get_llm_config():
        try:
            cfg = SettingsDAO.get_setting("agentic_settings", {}) or {}
            llm_configs = cfg.get("llmConfigs")
            if isinstance(llm_configs, dict) and isinstance(
                llm_configs.get("dailyArxiv"), dict
            ):
                picked = llm_configs.get("dailyArxiv") or {}
                result = {
                    "llmModel": (picked.get("llmModel") or "").strip(),
                    "llmBaseUrl": (picked.get("llmBaseUrl") or "").strip(),
                    "llmApiKey": AGENTIC_CREDENTIAL_STORE.get("dailyArxiv")
                    if AGENTIC_CREDENTIAL_STORE else "",
                }
                if result["llmBaseUrl"] and OUTBOUND_POLICY:
                    OUTBOUND_POLICY.validate(result["llmBaseUrl"], purpose="ai")
                return result
            result = {
                "llmModel": (cfg.get("llmModel") or "").strip(),
                "llmBaseUrl": (cfg.get("llmBaseUrl") or "").strip(),
                "llmApiKey": AGENTIC_CREDENTIAL_STORE.get("dailyArxiv")
                if AGENTIC_CREDENTIAL_STORE else "",
            }
            if result["llmBaseUrl"] and OUTBOUND_POLICY:
                OUTBOUND_POLICY.validate(result["llmBaseUrl"], purpose="ai")
            return result
        except Exception:
            return {}

    daily_arxiv_manager.set_llm_config_callback(get_llm_config)

    # Set user settings callback (for getting aiLanguage)
    def get_user_settings():
        try:
            return SettingsDAO.get_setting("user_settings", {}) or {}
        except Exception:
            return {}

    daily_arxiv_manager.set_user_settings_callback(get_user_settings)

    _daily_asset_coordinator = DailyAssetCoordinator(
        DB_PATH,
        lambda _owner_id, arxiv_id, stage: daily_arxiv_manager.process_paper_asset(
            arxiv_id, stage
        ),
    )
    daily_arxiv_manager.set_asset_enqueue_callback(
        lambda arxiv_id: _daily_asset_coordinator.enqueue(
            current_user_id(), arxiv_id
        )
    )
    daily_arxiv_manager.set_asset_queue_position_callback(
        lambda arxiv_id: _daily_asset_coordinator.queue_position(
            current_user_id(), arxiv_id
        )
    )

    def submit_daily_arxiv(function, *args, **kwargs):
        try:
            return _daily_arxiv_executor.submit(function, *args, **kwargs)
        except QueueFull:
            print("[DailyArxiv] scheduled fetch skipped because the queue is full")
            return None

    daily_arxiv_manager.set_scheduler_dispatch_callback(submit_daily_arxiv)

    # Check if LLM configuration is complete
    def is_llm_configured() -> bool:
        llm_config = get_llm_config()
        return bool(
            llm_config.get("llmBaseUrl")
            and llm_config.get("llmApiKey")
            and llm_config.get("llmModel")
        )

    # Start Daily arXiv callback function
    def start_daily_arxiv_if_configured():
        """If LLM configuration is complete, start Daily arXiv scheduler"""
        if is_llm_configured() and not daily_arxiv_manager._scheduler_running:
            daily_arxiv_manager.start_scheduler()
            print(
                "[DailyArxiv] LLM configuration is complete, scheduler has been started"
            )

    # Only start scheduler if LLM configuration is complete
    if is_llm_configured():
        daily_arxiv_manager.start_scheduler()
        print("[DailyArxiv] LLM configuration is complete, scheduler has been started")
    else:
        print(
            "[DailyArxiv] LLM configuration is incomplete, scheduler has not been started. Please configure LLM API in settings and start manually."
        )

    register_daily_arxiv_routes(
        app,
        daily_arxiv_settings_file=DAILY_ARXIV_SETTINGS_FILE,
        default_daily_arxiv_settings=DEFAULT_DAILY_ARXIV_SETTINGS,
        temp_papers_dir=TEMP_PAPERS_DIR,
        get_categories=get_categories,
        get_category_path=get_category_path,
        create_category_folder=create_category_folder,
        save_paper_metadata=save_paper_metadata,
        reading_list_file=READING_LIST_FILE,
        reading_list_temp_dir=READING_LIST_TEMP_DIR,
        agentic_settings_file=AGENTIC_SETTINGS_FILE,
        credential_store=AGENTIC_CREDENTIAL_STORE,
        outbound_policy=OUTBOUND_POLICY,
        document_client=DocumentWorkerClient(),
        task_executor=_daily_arxiv_executor,
        auxiliary_executor=_daily_aux_executor,
        asset_coordinator=_daily_asset_coordinator,
    )

    from ipaper.processing.service import ProcessingService
    from ipaper.processing.routes import register_processing_routes
    processing_service = ProcessingService(
        DB_PATH, UPLOAD_FOLDER,
        brand_getenv("IPAPER_SETTINGS_KEY_FILE", "/run/secrets/ipaper_settings_key"),
        AGENTIC_CREDENTIAL_STORE, OUTBOUND_POLICY,
    )
    register_processing_routes(app, processing_service)
    from ipaper.metadata.service import MetadataService
    from ipaper.metadata.routes import register_metadata_routes
    metadata_service = MetadataService(DB_PATH, processing_service, paper_store, search_index)
    register_metadata_routes(app, metadata_service)
    from ipaper.keywords.service import KeywordService
    from ipaper.keywords.routes import register_keyword_routes
    keyword_service = KeywordService(DB_PATH, processing_service)
    register_keyword_routes(app, keyword_service)
    from ipaper.topics.service import TopicService
    from ipaper.topics.routes import register_topic_routes
    topic_service = TopicService(DB_PATH, processing_service)
    register_topic_routes(app, topic_service)
    processing_service.initialize_ifzzh()
    if app.config.get("IPAPER_START_BACKGROUND_TASKS", False):
        processing_service.start()
        metadata_service.start()
        keyword_service.start()
        topic_service.start()

    register_settings_routes(
        app,
        user_settings_file=USER_SETTINGS_FILE,
        default_user_settings=DEFAULT_USER_SETTINGS,
        reading_history_file=READING_HISTORY_FILE,
        agentic_settings_file=AGENTIC_SETTINGS_FILE,
        default_agentic_settings=DEFAULT_AGENTIC_SETTINGS,
        avatars_dir=AVATARS_DIR,
        start_daily_arxiv_callback=start_daily_arxiv_if_configured,
        credential_store=AGENTIC_CREDENTIAL_STORE,
        outbound_policy=OUTBOUND_POLICY,
        daily_task_executor=_daily_arxiv_executor,
    )

    register_paper_operation_routes(
        app,
        get_categories=get_categories,
        get_category_path=get_category_path,
        find_category_node=find_category_node,
        get_papers_in_category=get_papers_in_category,
        save_paper_metadata=save_paper_metadata,
        delete_paper_files=delete_paper_files,
        extract_pdf_metadata=None,  # No longer needed, use new upload_paper module
        search_arxiv_by_title=None,  # No longer needed, use new upload_paper module
        reading_list_file=READING_LIST_FILE,
        upload_folder=UPLOAD_FOLDER,
        paper_store=paper_store,
        document_client=DocumentWorkerClient(),
    )

    register_upload_from_pdf_routes(
        app,
        get_categories=get_categories,
        get_category_path=get_category_path,
        create_category_folder=create_category_folder,
        save_paper_metadata=save_paper_metadata,
        reading_list_file=READING_LIST_FILE,
        paper_store=paper_store,
        document_client=DocumentWorkerClient(),
    )

    register_update_from_url_routes(
        app,
        get_categories=get_categories,
        get_category_path=get_category_path,
        create_category_folder=create_category_folder,
        save_paper_metadata=save_paper_metadata,
        reading_list_file=READING_LIST_FILE,
        reading_list_temp_dir=READING_LIST_TEMP_DIR,
        paper_store=paper_store,
        document_client=DocumentWorkerClient(),
    )

    register_agent_summary_routes(
        app,
        analysis_tasks=analysis_tasks,
        analysis_tasks_lock=analysis_tasks_lock,
        get_categories=get_categories,
        get_category_path=get_category_path,
        get_papers_in_category=get_papers_in_category,
        save_paper_metadata=save_paper_metadata,
        agentic_settings_file=AGENTIC_SETTINGS_FILE,
        upload_folder=UPLOAD_FOLDER,
        credential_store=AGENTIC_CREDENTIAL_STORE,
        outbound_policy=OUTBOUND_POLICY,
        document_client=DocumentWorkerClient(),
        task_executor=_analysis_executor,
    )

    register_agent_chat_routes(
        app,
        get_categories=get_categories,
        get_category_path=get_category_path,
        get_papers_in_category=get_papers_in_category,
        agentic_settings_file=AGENTIC_SETTINGS_FILE,
        credential_store=AGENTIC_CREDENTIAL_STORE,
        outbound_policy=OUTBOUND_POLICY,
    )

    register_agent_translate_routes(
        app,
        translation_tasks=translation_tasks,
        translation_tasks_lock=translation_tasks_lock,
        get_categories=get_categories,
        get_category_path=get_category_path,
        get_papers_in_category=get_papers_in_category,
        save_paper_metadata=save_paper_metadata,
        agentic_settings_file=AGENTIC_SETTINGS_FILE,
        upload_folder=UPLOAD_FOLDER,
        credential_store=AGENTIC_CREDENTIAL_STORE,
        outbound_policy=OUTBOUND_POLICY,
    )

    register_import_routes(
        app,
        get_categories=get_categories,
        save_categories=save_categories,
        get_category_path=get_category_path,
        create_category_folder=create_category_folder,
        save_paper_metadata=save_paper_metadata,
        reading_list_file=READING_LIST_FILE,
        paper_store=paper_store,
        upload_folder=UPLOAD_FOLDER,
        document_client=DocumentWorkerClient(),
    )

    register_export_routes(
        app,
        papers_dir=UPLOAD_FOLDER,
        task_executor=_export_executor,
    )

    register_institution_mapping_routes(
        app,
        daily_arxiv_settings_file=DAILY_ARXIV_SETTINGS_FILE,
    )


@app.route("/viewer/<paper_id>")
def pdf_viewer(paper_id):
    from urllib.parse import urlencode
    return redirect('/?' + urlencode({'paper': paper_id, 'view': 'reader', 'document': 'translated' if request.args.get('chinese') == 'true' else 'original'}))


@app.route("/viewer/analysis/<paper_id>")
def analysis_viewer(paper_id):
    from urllib.parse import urlencode
    return redirect('/?' + urlencode({'paper': paper_id, 'view': 'analysis'}))


def _initialize_application(papers_dir: str) -> None:
    global AUTH_CONFIG, AUTH_SERVICE, AGENTIC_CREDENTIAL_STORE, OUTBOUND_POLICY
    try:
        AUTH_CONFIG = AuthConfig.from_environ()
    except AuthConfigurationError as exc:
        raise RuntimeError(f"unsafe authentication configuration: {exc}") from exc

    if not AUTH_CONFIG.enabled:
        print(
            "WARNING: authentication is explicitly disabled for development; "
            "do not expose this service to an untrusted network."
        )

    os.makedirs(os.path.dirname(os.path.abspath(DB_PATH)), exist_ok=True)
    init_db_schema(DB_PATH)
    AUTH_SERVICE = LocalAuthService()
    if AUTH_CONFIG.enabled and not AUTH_SERVICE.has_active_admin():
        raise RuntimeError("local authentication has no active administrator")
    system_identity = (
        AUTH_SERVICE.first_active_admin()
        if AUTH_CONFIG.enabled
        else Identity(DEVELOPMENT_USER_ID, "development", "admin")
    )
    set_background_identity(system_identity)

    # Refuse mixed or legacy physical layouts before creating any per-user
    # directories or default files.
    os.makedirs(papers_dir, exist_ok=True)
    try:
        assert_tenant_migrated(papers_dir, DB_PATH)
    except TenantMigrationError as exc:
        raise RuntimeError(str(exc)) from exc

    # Initialize application only after a fail-closed system identity and
    # tenant storage layout exist.
    init_app(papers_dir=papers_dir)

    settings_key_file = brand_getenv(
        "IPAPER_SETTINGS_KEY_FILE", "/run/secrets/ipaper_settings_key"
    ).strip()
    try:
        assert_no_plaintext_credentials(DB_PATH)
        AGENTIC_CREDENTIAL_STORE = AgenticCredentialStore.from_key_file(settings_key_file)
        AGENTIC_CREDENTIAL_STORE.validate_all()
        OUTBOUND_POLICY = DynamicOutboundPolicy.from_environ(os.environ)
        OUTBOUND_POLICY.seed_deployment_origins(system_identity.user_id)
    except (
        AgenticSecretMigrationError,
        CredentialError,
        OutboundPolicyError,
        OSError,
        ValueError,
    ) as exc:
        raise RuntimeError(f"unsafe agentic configuration: {exc}") from exc

    # Initialize category system
    init_categories()

    # Register routes only after storage has passed migration checks.
    register_routes()

    # Rebuild search index (in background thread to avoid blocking startup)
    def rebuild_search_index():
        """Rebuild search index in background thread to avoid blocking startup"""
        def _rebuild():
            global _search_rebuild_running, _search_rebuild_pending
            while True:
                time.sleep(1)
                print("Start rebuilding search index...")
                try:
                    categories = get_categories()
                    papers_with_categories = []

                    def collect_papers(node, category_path):
                        node_path = get_category_path(categories, node.get("id"))
                        if node_path and len(node_path) > 1:
                            directory_path = str(
                                paper_directory(UPLOAD_FOLDER, node.get("id"))
                            )
                            if os.path.exists(directory_path):
                                papers = scan_papers_in_directory(
                                    directory_path,
                                    category_id=node.get("id"),
                                    category_path=node_path,
                                )
                                papers_with_categories.extend(
                                    (paper, node.get("id")) for paper in papers
                                )
                        for child in node.get("children", []):
                            collect_papers(child, node_path or [])

                    for child in categories.get("children", []):
                        collect_papers(child, [])
                    if papers_with_categories:
                        search_index.rebuild_index(papers_with_categories)
                    else:
                        print("No papers found, skip index reconstruction")
                except Exception as exc:  # noqa: BLE001
                    print(f"Failed to rebuild search index: {exc}")
                with _search_rebuild_lock:
                    if _search_rebuild_pending:
                        _search_rebuild_pending = False
                        continue
                    _search_rebuild_running = False
                    return

        global _search_rebuild_running, _search_rebuild_pending
        with _search_rebuild_lock:
            if _search_rebuild_running:
                _search_rebuild_pending = True
                return
            _search_rebuild_running = True
        try:
            _search_executor.submit(_rebuild)
        except (QueueFull, RuntimeError):
            with _search_rebuild_lock:
                _search_rebuild_running = False
            print("Search index rebuild skipped because the executor is unavailable")

    # Set rebuild index callback (after defining rebuild_search_index)
    if search_index:
        search_index.set_rebuild_callback(rebuild_search_index)

    # Rebuild search index
    rebuild_search_index()

    # Paper data is now directly stored in the JSON file next to the PDF file
def shutdown_application() -> None:
    topic_service = app.extensions.get('topics')
    if topic_service:
        topic_service.shutdown()
    keyword_service = app.extensions.get('keywords')
    if keyword_service:
        keyword_service.shutdown()
    metadata_service = app.extensions.get("metadata")
    if metadata_service:
        metadata_service.shutdown()
    processing_service = app.extensions.get("processing")
    if processing_service:
        processing_service.shutdown()
    """Stop process-owned schedulers before the WSGI worker exits."""
    if _daily_arxiv_manager is not None and getattr(
        _daily_arxiv_manager, "_scheduler_running", False
    ):
        _daily_arxiv_manager.stop_scheduler()
    if _daily_asset_coordinator is not None:
        _daily_asset_coordinator.shutdown(timeout=5)
    with analysis_tasks_lock:
        for task in analysis_tasks.values():
            if task.get("status") not in {"queued", "running"}:
                continue
            future = task.get("future")
            if future is not None:
                future.cancel()
            process = task.get("process")
            if process is not None and process.poll() is None:
                try:
                    os.killpg(process.pid, 15)
                except (OSError, ProcessLookupError):
                    pass
            task["status"] = "interrupted"
            task["result"] = {"success": False, "error": "interrupted"}
    for executor in (
        _analysis_executor,
        _export_executor,
        _daily_arxiv_executor,
        _daily_aux_executor,
        _search_executor,
    ):
        executor.shutdown(wait=False, cancel_futures=True)


def create_app(papers_dir: Optional[str] = None) -> Flask:
    """Create the process-local application exactly once."""
    global _application_initialized, _application_papers_dir, _shutdown_registered
    selected_papers_dir = papers_dir or brand_getenv(
        "IPAPER_PAPERS_DIR", "./papers"
    )
    selected_papers_dir = os.path.abspath(selected_papers_dir)
    with _application_lock:
        if _application_initialized:
            if selected_papers_dir != _application_papers_dir:
                raise RuntimeError("application is already initialized for another paper root")
            return app
        _initialize_application(selected_papers_dir)
        _application_papers_dir = selected_papers_dir
        _application_initialized = True
        if not _shutdown_registered:
            atexit.register(shutdown_application)
            _shutdown_registered = True
        return app


if __name__ == "__main__":
    args = parser.parse_args()
    try:
        create_app(args.papers_dir)
    except RuntimeError as exc:
        parser.error(str(exc))
    print(f"Start development server: http://{args.host}:{args.port}")
    app.run(host=args.host, port=args.port, debug=args.debug)
