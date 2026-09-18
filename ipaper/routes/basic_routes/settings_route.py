from __future__ import annotations

import base64
import json
import os
import uuid
from typing import Any, Dict

from flask import Flask, g, jsonify, request, send_from_directory
from ipaper.timeutil import today_app
from ipaper.database.dao.settings_dao import SettingsDAO
from ipaper.security.agentic_credentials import AgenticCredentialStore
from ipaper.security.credentials import CredentialError
from ipaper.security.outbound import OutboundPolicy, OutboundPolicyError, guarded_request
from ipaper.runtime.task_queue import BoundedExecutor, QueueFull
from ipaper.tools.api_test_utils import create_openai_client


def _normalize_agentic_settings(
    settings: Dict[str, Any] | None,
    default_agentic_settings: Dict[str, Any],
) -> Dict[str, Any]:
    current: Dict[str, Any] = {}
    if isinstance(settings, dict):
        current = settings

    merged = default_agentic_settings.copy()
    merged.update(current)

    llm_configs = merged.get("llmConfigs")
    legacy = {
        "llmModel": (merged.get("llmModel") or "").strip(),
        "llmBaseUrl": (merged.get("llmBaseUrl") or "").strip(),
    }

    normalized_llm_configs: Dict[str, Dict[str, str]] = {}
    if isinstance(llm_configs, dict):
        for k, v in llm_configs.items():
            if isinstance(v, dict):
                normalized_llm_configs[k] = {
                    "llmModel": (v.get("llmModel") or "").strip(),
                    "llmBaseUrl": (v.get("llmBaseUrl") or "").strip(),
                }

    for k in ("translate", "interpret", "dailyArxiv"):
        if k not in normalized_llm_configs:
            normalized_llm_configs[k] = legacy.copy()
        else:
            cfg = normalized_llm_configs[k]
            if not cfg.get("llmModel"):
                cfg["llmModel"] = legacy.get("llmModel", "")
            if not cfg.get("llmBaseUrl"):
                cfg["llmBaseUrl"] = legacy.get("llmBaseUrl", "")

    merged["llmConfigs"] = normalized_llm_configs
    merged.pop("llmModel", None)
    merged.pop("llmBaseUrl", None)
    merged.pop("llmApiKey", None)
    merged.pop("mineruApiToken", None)
    return merged

def register_settings_routes(
    app: Flask,
    *,
    user_settings_file: str,
    default_user_settings: Dict[str, Any],
    reading_history_file: str,
    agentic_settings_file: str,
    default_agentic_settings: Dict[str, Any],
    avatars_dir: str,
    start_daily_arxiv_callback=None,
    credential_store: AgenticCredentialStore | None = None,
    outbound_policy: OutboundPolicy | None = None,
    daily_task_executor: BoundedExecutor | None = None,
) -> None:

    # ========================================
    # User Settings (name, avatar, heatmap color)
    # ========================================
    @app.route("/api/settings/user", methods=["GET", "POST"])
    def api_user_settings():
        if request.method == "GET":
            try:
                settings = SettingsDAO.get_setting('user_settings', {})
            except Exception as exc:
                print(f"Failed to read user settings: {exc}")
                settings = {}
            merged = default_user_settings.copy()
            merged.update(settings)
            return jsonify(merged)

        data = request.json or {}
        try:
            # Read existing settings
            current = SettingsDAO.get_setting('user_settings', default_user_settings.copy())
            
            # Update settings
            current.update(data)

            SettingsDAO.save_setting('user_settings', current)
            return jsonify({"success": True})
        except Exception as exc:
            return jsonify({"success": False, "error": str(exc)}), 500

    # ========================================
    # Avatar Upload
    # ========================================
    @app.route("/api/settings/avatar", methods=["POST"])
    def api_upload_avatar():
        try:
            data = request.json or {}
            avatar_data = data.get("avatarData")  # Base64 encoded image

            if not avatar_data:
                return jsonify({"success": False, "error": "No avatar data"}), 400

            # parse Base64 data
            if "," in avatar_data:
                header, encoded = avatar_data.split(",", 1)
                # Get file type
                if "jpeg" in header or "jpg" in header:
                    ext = "jpg"
                elif "png" in header:
                    ext = "png"
                elif "gif" in header:
                    ext = "gif"
                else:
                    ext = "jpg"
            else:
                encoded = avatar_data
                ext = "jpg"

            # decode and save
            image_data = base64.b64decode(encoded)
            filename = f"avatar.{ext}"
            os.makedirs(avatars_dir, mode=0o700, exist_ok=True)
            filepath = os.path.join(avatars_dir, filename)

            with open(filepath, "wb") as f:
                f.write(image_data)

            # Update user settings
            settings = SettingsDAO.get_setting('user_settings', default_user_settings.copy())
            settings["avatar"] = filename
            SettingsDAO.save_setting('user_settings', settings)

            return jsonify({"success": True, "avatar": filename})
        except Exception as exc:
            return jsonify({"success": False, "error": str(exc)}), 500

    @app.route("/api/settings/avatar", methods=["GET"])
    def api_get_avatar():
        """Get avatar picture"""
        try:
            settings = SettingsDAO.get_setting('user_settings', {})
            avatar_file = settings.get("avatar")
            if avatar_file and os.path.exists(os.path.join(avatars_dir, avatar_file)):
                return send_from_directory(avatars_dir, avatar_file)
            return jsonify({"error": "No avatar"}), 404
        except Exception as exc:
            return jsonify({"error": str(exc)}), 500

    # ========================================
    # Reading History (daily reading time)
    # ========================================
    @app.route("/api/settings/reading-history", methods=["GET", "POST"])
    def api_reading_history():
        if request.method == "GET":
            try:
                history = SettingsDAO.get_setting('reading_history', {})
            except Exception as exc:
                print(f"Failed to read reading history: {exc}")
                history = {}
            return jsonify(history)

        data = request.json or {}
        try:
            # Read existing history
            current = SettingsDAO.get_setting('reading_history', {})

            # Update history (merged)
            for date, minutes in data.items():
                if date in current:
                    current[date] = current[date] + minutes
                else:
                    current[date] = minutes

            SettingsDAO.save_setting('reading_history', current)
            return jsonify({"success": True})
        except Exception as exc:
            return jsonify({"success": False, "error": str(exc)}), 500

    @app.route("/api/settings/reading-history/record", methods=["POST"])
    def api_record_reading():
        """Record today’s reading time (compatible with new and old formats)"""
        try:
            data = request.json or {}
            minutes = data.get("minutes", 0)
            date = data.get("date")  # YYYY-MM-DD
            paper_id = data.get("paper_id")  # optional, essayID

            if not date:
                # The day bucket is a UTC+8 business date.
                date = today_app().strftime("%Y-%m-%d")

            # Read existing history
            history = SettingsDAO.get_setting('reading_history', {})

            # Update reading history (compatible with new and old formats)
            if date in history:
                if isinstance(history[date], dict):
                    # new format
                    history[date]["total"] = history[date].get("total", 0) + minutes
                    if paper_id and paper_id not in history[date].get("papers", []):
                        if "papers" not in history[date]:
                            history[date]["papers"] = []
                        history[date]["papers"].append(paper_id)
                else:
                    # old format, converted to new format
                    old_minutes = history[date]
                    history[date] = {
                        "total": old_minutes + minutes,
                        "papers": [paper_id] if paper_id else [],
                    }
            else:
                history[date] = {
                    "total": minutes,
                    "papers": [paper_id] if paper_id else [],
                }

            SettingsDAO.save_setting('reading_history', history)

            total = (
                history[date]["total"]
                if isinstance(history[date], dict)
                else history[date]
            )
            return jsonify({"success": True, "total": total})
        except Exception as exc:
            return jsonify({"success": False, "error": str(exc)}), 500

    @app.route("/api/settings/reading-activity", methods=["GET"])
    def api_reading_activity():
        """UTC+8 calendar of effective reading time for the current user.

        Merging and summary rules live in :mod:`ipaper.reading_activity`; this
        route only gathers the current user's rows.
        """
        try:
            from ipaper.database.dao.user_data_dao import ReadingHistoryDAO
            from ipaper.reading_activity import activity_payload, activity_range
            from ipaper.timeutil import APP_TZ_NAME, utc_iso

            try:
                weeks = int(request.args.get("weeks", 12))
            except (TypeError, ValueError):
                weeks = 12
            weeks = max(1, min(weeks, 53))

            today = today_app()
            start, _end = activity_range(today, weeks)
            payload = activity_payload(
                today=today,
                weeks=weeks,
                table_rows=ReadingHistoryDAO.get_activity_since(start.isoformat()),
                legacy_totals=SettingsDAO.get_setting("reading_history", {}) or {},
            )
            return jsonify(
                {
                    "success": True,
                    "timezone": APP_TZ_NAME,
                    "generatedAt": utc_iso(),
                    **payload,
                }
            )
        except Exception as exc:
            return jsonify({"success": False, "error": str(exc)}), 500

    @app.route("/api/settings/reading-activity/papers", methods=["GET"])
    def api_reading_activity_papers():
        """Papers read on one UTC+8 day, limited to the current user."""
        try:
            from ipaper.database.dao.user_data_dao import ReadingHistoryDAO

            date_str = (request.args.get("date") or "").strip()
            if not date_str or len(date_str) != 10:
                return jsonify({"success": False, "error": "invalid_date"}), 400
            papers = ReadingHistoryDAO.get_day_papers(date_str)
            return jsonify({"success": True, "date": date_str, "papers": papers})
        except Exception as exc:
            return jsonify({"success": False, "error": str(exc)}), 500

    @app.route("/api/settings/reading-history/clear", methods=["POST"])
    def api_clear_reading_history():
        """Clear all reading history"""
        try:
            SettingsDAO.save_setting('reading_history', {})
            return jsonify({"success": True})
        except Exception as exc:
            return jsonify({"success": False, "error": str(exc)}), 500

    @app.route("/api/settings/reading-history/week-papers", methods=["GET"])
    def api_week_papers():
        """Get a list of papers to read this week"""
        try:
            from datetime import datetime, timedelta

            # Read reading history
            history = SettingsDAO.get_setting('reading_history', {})

            # Calculate the date range for this week (Monday to today)
            today = today_app()
            day_of_week = today.weekday()  # 0 = Monday, 6 = Sunday
            monday = today - timedelta(days=day_of_week)

            # Collect the papers you read this weekID
            week_paper_ids = set()
            current_date = monday
            while current_date <= today:
                date_str = current_date.strftime("%Y-%m-%d")
                if date_str in history:
                    entry = history[date_str]
                    if isinstance(entry, dict):
                        # new format
                        papers = entry.get("papers", [])
                        week_paper_ids.update(papers)
                    # There is no paper in the old formatIDinformation, skip

                current_date += timedelta(days=1)

            return jsonify(
                {
                    "success": True,
                    "papers": list(week_paper_ids),
                    "count": len(week_paper_ids),
                }
            )
        except Exception as exc:
            return jsonify({"success": False, "error": str(exc)}), 500

    # ========================================
    # Agentic Settings (unifiedAIFunction configuration)
    # ========================================
    @app.route("/api/settings/agentic", methods=["GET", "POST"])
    def api_agentic_settings():
        if credential_store is None or outbound_policy is None:
            return jsonify({"success": False, "error": "agentic_security_unavailable"}), 503
        if request.method == "GET":
            try:
                settings = SettingsDAO.get_setting('agentic_settings', {})
                merged = _normalize_agentic_settings(settings, default_agentic_settings)
                merged.pop("analysisSystemPrompt", None)
                merged.pop("analysisSystemPromptZh", None)
                merged.pop("analysisSystemPromptEn", None)
                for scenario in ("translate", "interpret", "dailyArxiv"):
                    merged["llmConfigs"][scenario]["llmApiKeyConfigured"] = (
                        credential_store.configured(scenario)
                    )
                merged["mineruApiTokenConfigured"] = credential_store.configured("mineru")
                return jsonify(merged)
            except CredentialError:
                return jsonify({"success": False, "error": "credential_decryption_failed"}), 503
            except Exception:
                return jsonify({"success": False, "error": "settings_read_failed"}), 500

        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            return jsonify({"success": False, "error": "invalid_settings_payload"}), 400
        try:
            unknown = sorted(set(data) - {"llmConfigs", "mineruServerUrl", "mineruUseApi", "mineruApiToken"})
            if unknown:
                return jsonify({"success": False, "error": "unknown_settings_fields", "fields": unknown}), 400
            incoming_configs = data.get("llmConfigs", {})
            if not isinstance(incoming_configs, dict):
                return jsonify({"success": False, "error": "invalid_llm_configs"}), 400
            unknown_scenarios = sorted(set(incoming_configs) - {"translate", "interpret", "dailyArxiv"})
            if unknown_scenarios:
                return jsonify({"success": False, "error": "unknown_llm_scenarios", "fields": unknown_scenarios}), 400

            old_settings = _normalize_agentic_settings(
                SettingsDAO.get_setting('agentic_settings', {}), default_agentic_settings
            )
            merged_settings = _normalize_agentic_settings(old_settings, default_agentic_settings)
            pending_secrets: dict[str, str] = {}
            for scenario in ("translate", "interpret", "dailyArxiv"):
                incoming_cfg = incoming_configs.get(scenario)
                if isinstance(incoming_cfg, dict):
                    unknown_fields = sorted(set(incoming_cfg) - {"llmModel", "llmBaseUrl", "llmApiKey"})
                    if unknown_fields:
                        return jsonify({"success": False, "error": "unknown_llm_fields", "fields": unknown_fields}), 400
                    model = incoming_cfg.get("llmModel", merged_settings["llmConfigs"][scenario]["llmModel"])
                    base_url = incoming_cfg.get("llmBaseUrl", merged_settings["llmConfigs"][scenario]["llmBaseUrl"])
                    if not isinstance(model, str) or not isinstance(base_url, str):
                        return jsonify({"success": False, "error": "invalid_llm_config"}), 400
                    model, base_url = model.strip(), base_url.strip()
                    if base_url:
                        try:
                            outbound_policy.validate(base_url, purpose="ai")
                        except OutboundPolicyError as exc:
                            if exc.reason != "origin_not_allowed" or getattr(g, "user_role", "user") != "admin" or not hasattr(outbound_policy, "approve_public_url"):
                                if exc.reason == "origin_not_allowed":
                                    return jsonify({"success": False, "error": "provider_not_allowed"}), 403
                                raise
                            outbound_policy.approve_public_url(base_url)
                    merged_settings["llmConfigs"][scenario] = {
                        "llmModel": model,
                        "llmBaseUrl": base_url,
                    }
                    secret = incoming_cfg.get("llmApiKey")
                    if secret not in (None, ""):
                        if not isinstance(secret, str) or not secret.strip() or len(secret) > 8192:
                            return jsonify({"success": False, "error": "invalid_api_key"}), 400
                        pending_secrets[scenario] = secret.strip()

            if "mineruServerUrl" in data:
                if not isinstance(data["mineruServerUrl"], str):
                    return jsonify({"success": False, "error": "invalid_mineru_url"}), 400
                mineru_url = data["mineruServerUrl"].strip()
                if mineru_url:
                    try:
                        outbound_policy.validate(mineru_url, purpose="ai")
                    except OutboundPolicyError as exc:
                        if exc.reason != "origin_not_allowed" or getattr(g, "user_role", "user") != "admin" or not hasattr(outbound_policy, "approve_public_url"):
                            if exc.reason == "origin_not_allowed":
                                return jsonify({"success": False, "error": "provider_not_allowed"}), 403
                            raise
                        outbound_policy.approve_public_url(mineru_url)
                merged_settings["mineruServerUrl"] = mineru_url
            if "mineruUseApi" in data:
                if not isinstance(data["mineruUseApi"], bool):
                    return jsonify({"success": False, "error": "invalid_mineru_mode"}), 400
                merged_settings["mineruUseApi"] = data["mineruUseApi"]
            mineru_secret = data.get("mineruApiToken")
            if mineru_secret not in (None, ""):
                if not isinstance(mineru_secret, str) or not mineru_secret.strip() or len(mineru_secret) > 8192:
                    return jsonify({"success": False, "error": "invalid_api_key"}), 400
                pending_secrets["mineru"] = mineru_secret.strip()

            old_daily = old_settings["llmConfigs"]["dailyArxiv"]
            daily_cfg = (merged_settings.get("llmConfigs") or {}).get("dailyArxiv", {})
            daily_changed = old_daily != daily_cfg or "dailyArxiv" in pending_secrets
            for name, value in pending_secrets.items():
                credential_store.set(name, value)
            SettingsDAO.save_setting('agentic_settings', merged_settings)
            is_daily_configured = bool(
                daily_cfg.get("llmModel")
                and daily_cfg.get("llmBaseUrl")
                and credential_store.configured("dailyArxiv")
            )
            if is_daily_configured and daily_changed:
                try:
                    if start_daily_arxiv_callback:
                        start_daily_arxiv_callback()
                except Exception:
                    print("[Settings] failed to start Daily arXiv after configuration update")
            return jsonify({"success": True, "configured": {
                name: credential_store.configured(name)
                for name in ("translate", "interpret", "dailyArxiv", "mineru")
            }})
        except OutboundPolicyError as exc:
            return jsonify({"success": False, "error": exc.reason}), 400
        except CredentialError:
            return jsonify({"success": False, "error": "credential_storage_failed"}), 503
        except Exception:
            return jsonify({"success": False, "error": "settings_save_failed"}), 500

    @app.delete("/api/settings/agentic/secrets/<secret_name>")
    def api_clear_agentic_secret(secret_name: str):
        if credential_store is None:
            return jsonify({"success": False, "error": "agentic_security_unavailable"}), 503
        if secret_name not in {"translate", "interpret", "dailyArxiv", "mineru"}:
            return jsonify({"success": False, "error": "unknown_agentic_secret"}), 404
        try:
            credential_store.clear(secret_name)
            return jsonify({"success": True, "configured": False})
        except CredentialError:
            return jsonify({"success": False, "error": "credential_storage_failed"}), 503

    # keep oldAPIendpoint for compatibility (return redirect hint)
    @app.route("/api/settings/translation", methods=["GET", "POST"])
    def api_translation_settings_deprecated():
        """Deprecated, please use /api/settings/agentic"""
        return (
            jsonify(
                {
                    "error": "This endpoint is deprecated. Use /api/settings/agentic instead"
                }
            ),
            410,
        )

    @app.route("/api/settings/analysis", methods=["GET", "POST"])
    def api_analysis_settings_deprecated():
        """Deprecated, please use /api/settings/agentic"""
        return (
            jsonify(
                {
                    "error": "This endpoint is deprecated. Use /api/settings/agentic instead"
                }
            ),
            410,
        )

    # ========================================
    # API Test Endpoints
    # ========================================
    @app.route("/api/settings/test/llm", methods=["POST"])
    def api_test_llm():
        """test LLM API connect"""
        try:
            data = request.json or {}
            fields = {
                name: data.get(name, "")
                for name in ("llmConfigType", "llmModel", "llmBaseUrl", "llmApiKey")
            }
            if any(not isinstance(value, str) for value in fields.values()):
                return jsonify({"success": False, "error": "invalid_llm_test_payload"}), 400
            llm_config_type = fields["llmConfigType"].strip() or None
            llm_model = fields["llmModel"].strip()
            llm_base_url = fields["llmBaseUrl"].strip()
            llm_api_key = fields["llmApiKey"].strip()

            if not llm_model or not llm_base_url:
                try:
                    stored = SettingsDAO.get_setting("agentic_settings", {}) or {}
                    stored = _normalize_agentic_settings(stored, default_agentic_settings)
                    cfg_type = llm_config_type or "dailyArxiv"
                    cfg = (stored.get("llmConfigs") or {}).get(cfg_type, {}) or {}
                    llm_model = llm_model or (cfg.get("llmModel") or "").strip()
                    llm_base_url = llm_base_url or (cfg.get("llmBaseUrl") or "").strip()
                except Exception:
                    pass

            if not llm_api_key and credential_store is not None:
                llm_api_key = credential_store.get(llm_config_type or "dailyArxiv")

            if not llm_model or not llm_base_url or not llm_api_key:
                return (
                    jsonify(
                        {
                            "success": False,
                            "error": "Please fill in the complete LLM API configure(Model、Base URL、API Key）",
                        }
                    ),
                    400,
                )

            if outbound_policy is None:
                return jsonify({"success": False, "error": "agentic_security_unavailable"}), 503
            try:
                outbound_policy.validate(llm_base_url, purpose="ai")
            except OutboundPolicyError as exc:
                return jsonify({"success": False, "error": exc.reason}), 400

            # import OpenAI client
            try:
                from openai import OpenAI  # noqa: F401
            except ImportError:
                return (
                    jsonify(
                        {
                            "success": False,
                            "error": "OpenAI The library is not installed, please run: pip install openai",
                        }
                    ),
                    500,
                )

            # Create client
            client = create_openai_client(llm_api_key, llm_base_url, outbound_policy)

            # Send test message
            test_message = "Can you see my message, if you can, respond with Yes."
            try:
                response = client.chat.completions.create(
                    model=llm_model,
                    messages=[
                        {"role": "user", "content": test_message},
                    ],
                    max_tokens=50,  # Limit reply length
                )

                # check reply
                if response.choices and len(response.choices) > 0:
                    reply = response.choices[0].message.content.strip()
                    # Check if it contains "Yes"(not case sensitive)
                    if "yes" in reply.lower():
                        if llm_config_type in (None, "", "dailyArxiv"):
                            try:
                                from ipaper.tools.basic_tools.daily_arxiv import (
                                    get_manager,
                                )

                                papers_dir = os.path.dirname(agentic_settings_file)
                                daily_arxiv_settings_file = os.path.join(
                                    papers_dir, "daily_arxiv_settings.json"
                                )
                                temp_papers_dir = os.path.join(
                                    papers_dir, ".daily_arxiv_temp"
                                )
                                manager = get_manager(
                                    temp_papers_dir, daily_arxiv_settings_file
                                )
                                if hasattr(manager, "_llm_api_failed"):
                                    manager._llm_api_failed = False
                                    manager._llm_api_error_message = ""
                                    print(
                                        "[Settings] LLM API Test successful, cleared Daily arXiv failure status"
                                    )

                                if not manager._scheduler_running:
                                    manager.start_scheduler()
                                    print(
                                        "[Settings] LLM API Test successful, started Daily arXiv Scheduler (the scheduler will automatically trigger a crawl)"
                                    )
                                else:
                                    def trigger_fetch():
                                        try:
                                            manager._do_scheduled_fetch()
                                            print(
                                                "[Settings] LLM API Test successful, triggered once Daily arXiv crawl"
                                            )
                                        except Exception as e:
                                            print(
                                                f"[Settings] trigger Daily arXiv Fetch failed: {e}"
                                            )

                                    if daily_task_executor is not None:
                                        try:
                                            daily_task_executor.submit(trigger_fetch)
                                            print(
                                                "[Settings] LLM API test queued a Daily arXiv crawl"
                                            )
                                        except QueueFull:
                                            print(
                                                "[Settings] Daily arXiv crawl skipped because the queue is full"
                                            )
                            except Exception as e:
                                print(
                                    f"[Settings] deal with Daily arXiv An error occurred in the failed state (does not affect testing): {e}"
                                )

                        return jsonify(
                            {
                                "success": True,
                                "message": "LLM API Connection successful!",
                                "reply": reply,
                            }
                        )
                    else:
                        return jsonify(
                            {
                                "success": False,
                                "error": f"LLM API A response was returned, but not as expected. Reply content: {reply}",
                                "reply": reply,
                            }
                        )
                else:
                    return jsonify(
                        {
                            "success": False,
                            "error": "LLM API Returned an empty reply",
                        }
                    )

            except Exception as e:
                error_msg = str(e)
                # Provide friendlier error messages
                if "401" in error_msg or "Unauthorized" in error_msg:
                    return jsonify(
                        {
                            "success": False,
                            "error": "API Key Invalid or unauthorized",
                        }
                    )
                elif "404" in error_msg or "Not Found" in error_msg:
                    return jsonify(
                        {
                            "success": False,
                            "error": "API Endpoint does not exist, please check Base URL Is it correct?",
                        }
                    )
                elif "timeout" in error_msg.lower():
                    return jsonify(
                        {
                            "success": False,
                            "error": "Connection timed out, please check network connection and Base URL",
                        }
                    )
                else:
                    return jsonify({"success": False, "error": "llm_connection_failed"})

        except CredentialError:
            return jsonify({"success": False, "error": "credential_decryption_failed"}), 503
        except Exception:
            return jsonify({"success": False, "error": "llm_test_failed"}), 500

    @app.route("/api/settings/test/mineru", methods=["POST"])
    def api_test_mineru():
        """test MinerU local server connect"""
        try:
            import requests

            data = request.json or {}
            mineru_server_url = data.get("mineruServerUrl", "")
            if not isinstance(mineru_server_url, str):
                return jsonify({"success": False, "error": "invalid_mineru_url"}), 400
            mineru_server_url = mineru_server_url.strip()

            if not mineru_server_url:
                return (
                    jsonify(
                        {
                            "success": False,
                            "error": "Please fill in MinerU Server URL",
                        }
                    ),
                    400,
                )

            if outbound_policy is None:
                return jsonify({"success": False, "error": "agentic_security_unavailable"}), 503
            try:
                outbound_policy.validate(mineru_server_url, purpose="ai")
            except OutboundPolicyError as exc:
                return jsonify({"success": False, "error": exc.reason}), 400

            # Test health endpoint
            test_url = f"{mineru_server_url.rstrip('/')}/health"
            try:
                response = guarded_request(
                    outbound_policy, "GET", test_url, purpose="ai", timeout=10
                )
                if response.status_code == 200:
                    return jsonify(
                        {
                            "success": True,
                            "message": "MinerU server is accessible",
                            "tested_url": test_url,
                        }
                    )
                else:
                    return jsonify(
                        {
                            "success": False,
                            "error": f"Server returned status {response.status_code}",
                        }
                    )
            except requests.exceptions.ConnectionError:
                return jsonify(
                    {
                        "success": False,
                        "error": "mineru_connection_failed",
                    }
                )
            except requests.exceptions.Timeout:
                return jsonify({"success": False, "error": "Connection timeout"})

        except OutboundPolicyError as exc:
            return jsonify({"success": False, "error": exc.reason}), 400
        except Exception:
            return jsonify({"success": False, "error": "mineru_test_failed"}), 500

    @app.route("/api/settings/test/mineru-api", methods=["POST"])
    def api_test_mineru_api_token():
        """test MinerU API token"""
        try:
            from ipaper.tools.api_test_utils import test_mineru_api_token

            data = request.json or {}
            api_token = data.get("apiToken", "")
            if not isinstance(api_token, str):
                return jsonify({"success": False, "error": "invalid_api_key"}), 400
            api_token = api_token.strip()

            if not api_token and credential_store is not None:
                api_token = credential_store.get("mineru")

            if not api_token:
                return (
                    jsonify(
                        {
                            "success": False,
                            "error": "Please enter API token",
                        }
                    ),
                    400,
                )

            success, error_msg = test_mineru_api_token(api_token)

            if success:
                return jsonify(
                    {
                        "success": True,
                        "message": "API token is valid and working",
                    }
                )
            else:
                return jsonify(
                    {
                        "success": False,
                        "error": error_msg,
                    }
                )

        except CredentialError:
            return jsonify({"success": False, "error": "credential_decryption_failed"}), 503
        except Exception:
            return jsonify({"success": False, "error": "mineru_api_test_failed"}), 500
