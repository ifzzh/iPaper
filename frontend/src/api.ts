export class ApiError extends Error {
  constructor(
    public status: number,
    public code: string,
    /** Seconds the server asked us to wait (Retry-After), when it said so. */
    public retryAfter: number | null = null,
  ) {
    super(code);
  }
}
const processingErrors: Record<string, string> = {
  revision_conflict: "内容已在另一处更新，草稿保留。请重新核对后提交。",
  topic_catalog_changed: "主题体系已变化，请重新检查扩展方向。",
  topic_definition_in_use: "该自动整理方向已绑定其他主题，请先核对或合并。",
  topic_name_conflict: "同一层级已有同名主题，请更名或明确合并。",
  topic_deleted: "主题已删除，请在主题管理中核对近期操作。",
  topic_not_found: "主题不存在或已无法访问。",
  topic_cycle: "不能把主题调整到自己的后代中。",
  topic_merge_cycle: "不能直接合并祖先与后代主题。",
  undo_conflict: "相关内容有较新修改，本次撤销不会覆盖它们。",
  request_id_conflict: "此操作内容已变化，请重新核对后提交。",
  topic_bootstrap_budget: "本次体系检查超过本地处理预算，未保存不完整结果。",
  topic_queue_full: "主题整理队列已满，请稍后重试。",
  topic_storage_failed: "主题保存失败，原有信息保留，请重试。",

  tag_revision_conflict: "标签已在另一处更新。草稿已保留，请重新载入后核对。",
  tag_name_conflict: "此名称或别名已属于其他标签，请明确合并或改用其他名称。",
  tag_deleted: "这个标签已删除，可以在标签管理中恢复。",
  tag_name_retired:
    "此别名已移除，不会由自动任务重新创建。请在标签管理中核对。",
  tag_undo_conflict:
    "相关内容在此操作后已被修改，不能直接撤销覆盖。请重新核对。",
  tag_undo_unavailable: "此操作已撤销或超过 30 天恢复期限。",
  invalid_tag_name: "请输入 1–64 个字符的有效标签名称。",
  keyword_queue_full: "整理队列已满，请等待现有任务结束后重试。",
  keyword_storage_failed: "标签保存失败，原有标签保留，请重试。",
  keyword_failed: "整理未完成，原有标签保留，请查看任务并重试。",
  keyword_source_changed: "论文内容已变化，请使用当前内容重新整理。",
  keyword_preview_changed: "输入内容或模型配置已变化，请重新打开预检。",
  keyword_input_limit:
    "所选正文超过单次增强预算，请使用本地整理或选择更短的论文。",
  keyword_model_batch_limit: "一次模型增强最多选择 20 篇论文。",
  keyword_evidence_invalid: "模型关键词缺少有效原文依据，本次未应用。",
  tag_catalog_limit: "标签库已达到 5,000 个有效标签，请先整理现有标签。",
  paper_tag_limit: "这篇论文已达到 128 个标签，请先移除不需要的标签。",
  library_selection_expired: "全部匹配的选择已过期，请重新选择。",
  keyword_nothing_to_retry: "没有需要重试的任务。",
  keyword_cancelled: "任务已取消，尚未开始的请求不会继续。",
  keyword_interrupted: "本地整理被中断，可重新整理。",

  invalid_bookmark_name: "书签名称需要 1–120 个字符。",
  bookmark_changed: "书签已在另一处更新，请重新打开书签列表后再操作。",
  bookmark_limit: "书签已达上限：每篇 200 条、每账号 5,000 条。",
  reading_search_expired:
    "内容或译文修订已变化，请重新搜索，避免混合不同版本。",
  selection_length_limit:
    "一次最多翻译 2,000 个字符；请缩短选区，不会自动截断。",
  selection_input_limit: "选区超过本次输入预算，请缩短后明确提交。",
  selection_cache_quota: "划词缓存已达账号上限，请等待到期清理或联系管理员。",
  selection_source_mismatch: "选区与来源版本不匹配，请重新选择。",
  selection_explicit_retry_required:
    "请回到原选区翻译窗口明确重试；不会自动追加模型请求。",
  paper_content_missing:
    "尚无可用正文。请先明确创建解析任务，系统不会自动付费解析。",
  content_version_changed: "解析正文版本已变化，请刷新并重新核对范围。",
  content_coverage_confirmation_required:
    "正文覆盖不完整或未知，请先确认本次使用的范围。",
  content_size_limit: "正文超过当前安全处理上限，未截取开头冒充全文。",
  interpret_settings_not_configured: "请先配置“论文问答与分析”模型及密钥。",
  analysis_configuration_changed:
    "解读配置已变化，原任务已停止；请按新配置创建任务。",
  analysis_version_changed: "分析版本已变化，请刷新后重新操作。",
  understanding_failed: "分析未完成，已有可读结果保留，请查看任务日志。",
  local_source_required: "局部问答需要有效选区或当前结构段落。",
  chat_request_already_submitted:
    "此问题已提交，不会重复调用模型；请核对请求状态和历史。",
  chat_request_in_progress: "你已有正在处理的问答，请等待结果或核对历史。",
  model_queue_full: "模型请求已达到并发上限，请稍后再发送。",
  analysis_export_size_limit:
    "此分析图片包超过 32 MiB 导出上限，未生成不完整文件包。",
  analysis_export_failed: "导出未完成，请在任务日志中核对原因。",
  invalid_understanding_settings:
    "请填写有效提示词（最多 12,000 字符）并选择输出语言。",
  empty_source_context:
    "当前内容没有可引用的文字，请选择正文或表格文字后提问。",
  document_worker_unavailable: "文档处理服务暂时不可用，请稍后重试。",
  document_preflight_busy:
    "文档正在预检，请稍后重试；不会创建云解析或模型请求。",
  processing_dispatch_interrupted: "本地调度中断，已保存结果保留，请明确继续。",
  model_request_rejected: "模型服务拒绝了请求，请检查对应模型配置后继续。",
  model_rate_limited: "模型服务限流，已停止继续请求；请稍后明确继续。",
  pdf_selection_unverified: "未能在 PDF 页面中核实这段文字，请重新选择正文。",
  processing_scope_exceeds_budget:
    "所选范围超过本次处理预算，请调整范围或预算后再提交。",
  structured_settings_not_configured: "请先在设置中配置独立的结构化翻译模型。",
  mineru_cloud_not_configured: "请先在设置中启用 MinerU 云解析并配置密钥。",
  user_processing_busy: "你已有一个文献处理任务，请在任务中心查看或停止。",
  processing_queue_full: "处理队列已满，请稍后再提交。",
  owner_quota_exceeded: "处理产物已达到账号存储配额，请联系管理员调整。",
  result_quota_exceeded: "此次结果超过存储上限，请缩小处理范围。",
  source_changed: "PDF 已发生变化，请重新检查处理范围。",
  source_expired: "原始文件已变化，此来源不能指向当前 PDF。",
  parse_version_mismatch: "解析版本已变化，请重新选择结果。",
  translation_config_changed: "翻译配置已改变，请按新配置创建任务。",
  processing_budget_exceeded:
    "已达到处理预算，成功结果已保留。请缩小范围后继续。",
  processing_time_budget: "已达到执行时限，成功结果已保留。",
  model_result_unknown:
    "模型请求的最终结果不确定。不会自动重发，请核对后决定是否继续。",
  mineru_response_unknown:
    "解析任务提交结果不确定，请核对任务编号；不会重复创建。",
  mineru_upload_unknown:
    "文件上传结果不确定，只能查询已创建任务，不会重复上传。",
  cloud_submission_unknown_no_resubmit:
    "此前云任务的创建结果不确定，不能自动重复创建。请先核对供应商记录。",
  mineru_parse_failed: "云解析失败，已有结果保留，请查看任务日志。",
  mineru_poll_timeout:
    "云解析仍未获得确定结果，可稍后明确继续查询；不会重新创建任务。",
  structure_worker_rejected: "解析产物未通过安全或结构校验，已停止处理。",
  model_output_id_mismatch: "模型返回的块编号不匹配，此批未发布。",
  model_output_invalid: "模型返回格式不正确，此批未发布。",
  model_output_incomplete: "模型输出未完整结束，此批未发布。",
  translation_protected_content_changed:
    "模型改动了受保护的公式或数字，此批未发布。",
  processing_storage_failed: "处理状态保存失败，请稍后重试。",
  processing_failed: "处理未完成，请查看任务日志；已保存结果仍可阅读。",
  service_restarted: "服务已重启，请确认后继续；不确定的请求不会自动重发。",
  some_blocks_failed:
    "部分块翻译失败，已完成内容仍可阅读，可以重试未完成部分。",
};
export function errorText(error: unknown): string {
  const code =
    error && typeof error === "object" && "code" in error
      ? String(error.code)
      : "";
  if (processingErrors[code]) return processingErrors[code];
  if (error instanceof ApiError) {
    if (error.status === 404) return "这篇论文不存在或已无法访问。";
    if (error.status === 429) return "请求较多，请稍后重试。";
    if (error.code === "csrf_failed") return "安全令牌失效，请重新登录。";
  }
  return "暂时无法加载，请检查连接后重试。";
}
export function isSessionError(error: unknown) {
  return (
    error instanceof ApiError &&
    (error.status === 401 || error.code === "password_change_required")
  );
}
export async function request(
  path: string,
  signal?: AbortSignal,
  method = "GET",
  body?: unknown,
  keepalive = false,
): Promise<unknown> {
  const headers = new Headers({ Accept: "application/json" });
  if (method !== "GET") {
    for (const [key, value] of Object.entries(csrfHeaders()))
      headers.set(key, value);
  }
  if (body !== undefined) headers.set("Content-Type", "application/json");
  const response = await fetch(path, {
    method,
    headers,
    signal,
    body: body === undefined ? undefined : JSON.stringify(body),
    credentials: "same-origin",
    cache: "no-store",
    keepalive,
  });
  if (
    response.status === 401 &&
    !path.startsWith("/api/auth/") &&
    typeof window !== "undefined"
  )
    window.dispatchEvent(new Event("ipaper-session-expired"));
  let data: unknown;
  try {
    data = await response.json();
  } catch {
    throw new ApiError(response.status, "invalid_response");
  }
  const obj = data as Record<string, unknown> | null;
  if (
    obj?.error === "password_change_required" &&
    typeof window !== "undefined"
  )
    window.dispatchEvent(new Event("ipaper-session-expired"));
  if (!response.ok || obj?.success === false || obj?.error) {
    // A 429 carries Retry-After; callers must wait that long instead of
    // hammering the endpoint with a fixed short backoff.
    const header = response.headers.get("Retry-After");
    const parsed = header !== null && /^\d+$/.test(header.trim()) ? Number(header) : null;
    throw new ApiError(
      response.status,
      typeof obj?.error === "string" ? obj.error : "request_failed",
      response.status === 429 ? parsed : null,
    );
  }
  return data;
}
export type User = {
  id: string;
  username: string;
  role?: string;
  must_change_password?: boolean;
};
export type Paper = {
  tags?: { id: string; name: string }[];
  id: string;
  title: string;
  authors: string;
  year: string;
  abstract: string;
  starred: boolean;
  translated: boolean;
  translation: string;
  analysis: string;
  published: string;
  arxiv_url?: string;
  github?: string;
  homepage?: string;
  notes?: string;
  affiliation?: string;
  journal?: string;
  has_analysis_result?: boolean;
  category_id?: string;
};
function text(v: unknown): string {
  return typeof v === "string" ? v : "";
}
export function paperFrom(data: unknown): Paper {
  if (
    !data ||
    typeof data !== "object" ||
    typeof (data as Record<string, unknown>).id !== "string"
  )
    throw new ApiError(200, "invalid_paper");
  const p = data as Record<string, unknown>;
  return {
    id: text(p.id),
    tags: Array.isArray(p.tags)
      ? p.tags
          .filter(
            (t: any) =>
              t && typeof t.id === "string" && typeof t.name === "string",
          )
          .map((t: any) => ({ id: t.id, name: t.name }))
      : [],
    title:
      text(p.title) ||
      text(p.original_filename) ||
      text(p.filename) ||
      "未命名论文",
    authors: text(p.authors),
    year: text(p.year),
    abstract: text(p.abstract),
    starred: p.starred === true,
    translated: p.has_chinese_version === true,
    translation: text(p.translation_status),
    analysis: text(p.analysis_status),
    published: text(p.arxiv_published_date),
    arxiv_url: text(p.arxiv_url),
    github: text(p.github),
    homepage: text(p.homepage),
    notes: text(p.notes),
    affiliation: text(p.affiliation),
    journal: text(p.journal),
    has_analysis_result: p.has_analysis_result === true,
    category_id: text(p.category_id),
  };
}
export async function session(signal: AbortSignal): Promise<User | null> {
  const data = (await request("/api/auth/session", signal)) as {
    authenticated?: boolean;
    user?: User & { must_change_password?: boolean };
  };
  if (
    !data?.authenticated ||
    data.user?.must_change_password ||
    typeof data.user?.id !== "string" ||
    typeof data.user.username !== "string"
  )
    return null;
  return { id: data.user.id, username: data.user.username };
}
export async function papers(signal: AbortSignal) {
  const data = await request("/api/library/index", signal);
  if (!Array.isArray(data)) throw new ApiError(200, "invalid_list");
  return data.map(paperFrom);
}
export function publicationDate(value: string) {
  const match = /^(\d{4})-(\d{2})-(\d{2})(?:T|$)/.exec(value);
  return match ? `${match[1]}-${match[2]}-${match[3]}` : "";
}

export function csrfHeaders(): Record<string, string> {
  const cookie = document.cookie
    .split(";")
    .map((v) => v.trim())
    .find((v) => v.startsWith("paperpilot_csrf="));
  if (!cookie) return {};
  try {
    return {
      "X-CSRF-Token": decodeURIComponent(
        cookie.slice("paperpilot_csrf=".length),
      ),
    };
  } catch {
    return {};
  }
}
