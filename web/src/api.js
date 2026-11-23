/**
 * 统一 API 模块：所有请求只走这里，负责解包统一响应与错误提示。
 */

export class ApiError extends Error {
  constructor(message, status, code) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.code = code
  }
}

const STATUS_MESSAGE = {
  400: '请求参数不合法',
  404: '资源不存在',
  409: '任务尚未完成，请稍后再试',
  422: '请求参数校验失败',
  500: '服务器内部错误',
  503: '服务暂不可用',
}

async function request(
  path,
  { method = 'GET', body, timeout = 30000, allowNotOk = false } = {}
) {
  const controller = new AbortController()
  const timer = setTimeout(() => controller.abort(), timeout)
  try {
    const response = await fetch(`/api${path}`, {
      method,
      headers: body ? { 'Content-Type': 'application/json' } : undefined,
      body: body ? JSON.stringify(body) : undefined,
      signal: controller.signal,
    })

    let payload = null
    try {
      payload = await response.json()
    } catch (error) {
      payload = null
    }

    if (!response.ok || (payload?.ok === false && !allowNotOk)) {
      const message =
        payload?.error?.message ||
        STATUS_MESSAGE[response.status] ||
        `请求失败（HTTP ${response.status}）`
      throw new ApiError(message, response.status, payload?.error?.code)
    }
    return payload.data
  } catch (error) {
    if (error.name === 'AbortError') {
      throw new ApiError('请求超时，请稍后重试', 0, 'timeout')
    }
    if (error instanceof ApiError) {
      throw error
    }
    throw new ApiError(
      '无法连接后端服务，请确认 python -m backend.server 正在运行',
      0,
      'network'
    )
  } finally {
    clearTimeout(timer)
  }
}

export const api = {
  health: () => request('/health', { allowNotOk: true }),
  pairs: (limit = 30) => request(`/pairs?limit=${limit}`),
  runs: (limit = 20) => request(`/runs?limit=${limit}`),
  createRun: (idx, pair = true) =>
    request('/runs', { method: 'POST', body: { idx, pair } }),
  getRun: (runId) => request(`/runs/${encodeURIComponent(runId)}`),
  getReport: (runId) =>
    request(`/runs/${encodeURIComponent(runId)}/report`),
  getTrace: (runId, after = 0) =>
    request(`/runs/${encodeURIComponent(runId)}/trace?after=${after}`),
  createBatch: (idxList, pair = true) =>
    request('/batches', { method: 'POST', body: { idx_list: idxList, pair } }),
  getBatch: (batchId) =>
    request(`/batches/${encodeURIComponent(batchId)}`),
  knowledgeSearch: (q, { topK = 5, project = '', cwe = '' } = {}) => {
    const params = new URLSearchParams({ q, top_k: String(topK) })
    if (project) params.set('project', project)
    if (cwe) params.set('cwe', cwe)
    return request(`/knowledge/search?${params.toString()}`)
  },
}
