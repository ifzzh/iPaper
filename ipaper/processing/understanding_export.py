"""Offline analysis exports: only already-authorized local assets enter a ZIP."""

from __future__ import annotations
import json
import re
import zipfile
from pathlib import Path

from .common import ProcessingError, fingerprint
from .pipeline import file_digest
from ipaper.security.paths import safe_join


def safe_offline_markdown(text, images):
    """Only canonical, copied images can be embedded by an offline renderer.

    Preserve Markdown/math instead of round-tripping through HTML. Other image
    syntax is made literal, including reference images and malformed targets.
    Raw HTML is escaped; it cannot bypass the image allowlist.
    """
    import html

    text = re.sub(
        r"</?[A-Za-z][^>]*>|<!--.*?-->", lambda m: html.escape(m[0]), text, flags=re.S
    )
    text = text.replace("![", r"\![")
    for name in images:
        pattern = r"\\!\[([^\]\n]*)\]\(" + re.escape(name) + r"\)"
        text = re.sub(pattern, lambda m: "![" + m[1] + "](" + name + ")", text)
    return text


def analysis_content(understanding, paper_id, result_id):
    files = understanding.files
    if result_id == "legacy":
        path, text = files.legacy(paper_id, analysis=True)
        if not path:
            raise ProcessingError("understanding_not_found", 404)
        images, missing = files.legacy_images(path, text)
        canonical = {}
        for raw, image in images.items():
            name = "images/" + file_digest(image) + image.suffix.lower()
            text = text.replace("](" + raw + ")", "](" + name + ")")
            canonical[name] = image
        return {
            "markdown": safe_offline_markdown(text, canonical),
            "sources": {},
            "kind": "interpretation",
            "legacy": True,
            "missingImages": missing,
        }, canonical
    row = files.row(result_id)
    if row["paper_id"] != paper_id or row["kind"] not in {"overview", "interpretation"}:
        raise ProcessingError("understanding_not_found", 404)
    body = files.body(result_id)
    snapshot = files.row(row["source_id"])
    images = {
        e["path"]: safe_join(
            files.store.artifact_directory(snapshot["id"]),
            e["path"],
            must_exist=True,
            require_file=True,
        )
        for e in json.loads(snapshot["manifest_json"])["entries"]
        if e["path"].startswith("images/")
    }
    referenced = set(
        re.findall(
            r"!\[[^\]]*\]\((images/[a-f0-9]+\.(?:png|jpe?g))\)", body["markdown"]
        )
    )
    selected = {name: path for name, path in images.items() if name in referenced}
    return {
        **body,
        "markdown": safe_offline_markdown(body["markdown"], selected),
        "kind": row["kind"],
        "config": json.loads(row["config_json"]),
        "createdAt": row["created_at"],
        "snapshotId": row["source_id"],
    }, selected


def create_export(understanding, paper_id, data, *, preview=False):
    result_id = data.get("analysisResultId")
    if not isinstance(result_id, str) or data.get("format") not in {"markdown", "zip"}:
        raise ProcessingError("invalid_export_request")
    body, images = analysis_content(understanding, paper_id, result_id)
    size = sum(p.stat().st_size for p in images.values()) + len(
        body["markdown"].encode()
    )
    if size > 32 * 1024**2:
        raise ProcessingError("analysis_export_size_limit", 413)
    if preview:
        return {
            "bytes": size,
            "imageCount": len(images),
            "missingImages": len(body.get("missingImages", [])),
            "requests": 0,
        }
    request = {
        "analysisResultId": result_id,
        "format": data["format"],
        "contentHash": fingerprint(body),
        "assets": {name: file_digest(p) for name, p in images.items()},
    }
    return understanding.jobs.create(
        paper_id, "analysis_export", request, reservation=max(1024**2, size * 2 + 65536)
    )


def run_export(understanding, job, request):
    body, images = analysis_content(
        understanding, job["paper_id"], request["analysisResultId"]
    )
    if (
        fingerprint(body) != request["contentHash"]
        or {name: file_digest(p) for name, p in images.items()} != request["assets"]
    ):
        raise ProcessingError("analysis_version_changed", 409)
    with understanding.store.connection() as db:
        title = (
            db.execute(
                "SELECT title FROM papers WHERE id=? AND owner_id=?",
                (job["paper_id"], understanding.store.owner),
            ).fetchone()[0]
            or "论文"
        )
    title = re.sub(r'[\x00-\x1f<>:"/\\|?*]', "_", title)[:100]
    name = (
        "iPaper-"
        + title
        + "-"
        + ("AI概览" if body["kind"] == "overview" else "深度解读")
    )
    markdown = (
        "# "
        + title
        + "\n\n> iPaper · "
        + ("AI 概览" if body["kind"] == "overview" else "深度解读")
        + "；与作者原始摘要不同。\n\n"
    )
    if body.get("legacy"):
        markdown += "> 历史分析：生成配置和来源精度未知。\n\n"
    else:
        config = body["config"]
        markdown += (
            "> 生成时间："
            + body["createdAt"]
            + "；语言："
            + config["language"]
            + "；模型："
            + config["model"]
            + "。\n\n"
        )
    markdown += body["markdown"]
    appendix = []
    for label, value in body.get("sources", {}).items():
        source = understanding.files.resolve(value["sourceId"])
        locator = (
            f"原始 PDF 第 {source['page']} 页"
            if source.get("page")
            else "旧正文摘录，无可确认的 PDF 页码"
        )
        appendix.append(
            f"### [{label}] {locator}\n\n> "
            + value["quote"].replace("\n", "\n> ")
            + f"\n\n来源版本：{source['snapshotId']}；来源编号：{source['id']}。"
        )
    if appendix:
        markdown += "\n\n## 来源摘录\n\n" + "\n\n".join(appendix)
    if body.get("missingImages"):
        markdown += (
            "\n\n## 图片缺失说明\n\n有 "
            + str(len(body["missingImages"]))
            + " 个历史图片引用无法核实，未包含在导出中。"
        )
    root = understanding.store.artifact_directory(job["id"])
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    target = root / ("analysis.zip" if request["format"] == "zip" else "analysis.md")
    understanding.jobs.check(job["id"])
    if request["format"] == "zip":
        with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr(name + ".md", markdown)
            archive.writestr(
                "阅读说明.txt",
                "使用支持 Markdown 数学公式和表格的本地阅读器打开 .md 文件。images 目录与正文保持同级。来源摘录可离线阅读；未包含论文 PDF。此文件包不是服务器生成的排版 PDF。\n",
            )
            for relative, path in images.items():
                understanding.jobs.check(job["id"])
                archive.write(path, relative)
    else:
        if images:
            markdown = (
                "> 此 Markdown 含图片引用。离线查看图片请同时导出“Markdown＋图片包”。\n\n"
                + markdown
            )
        target.write_text(markdown, encoding="utf-8")
    rid = understanding.files.publish(
        job["paper_id"],
        "analysis_export",
        {"filename": name + target.suffix, "file": target.name},
        assets={target.name: target},
        key=fingerprint([job["id"], request]),
        job_id=job["id"],
    )
    understanding.jobs.checkpoint(
        job["id"], "completed", {"understandingResultId": rid}, completed=1, total=1
    )
    understanding.jobs.finish(job["id"], "completed")
