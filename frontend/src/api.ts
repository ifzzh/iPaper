export class ApiError extends Error {
  constructor(
    public status: number,
    public code: string,
  ) {
    super(code);
  }
}
const processingErrors: Record<string, string> = {
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
    throw new ApiError(
      response.status,
      typeof obj?.error === "string" ? obj.error : "request_failed",
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
  const data = await request("/api/papers/all", signal);
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
