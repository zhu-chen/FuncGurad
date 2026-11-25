<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { api, ApiError } from './api'

const health = ref(null)
const pairs = ref([])
const recentRuns = ref([])

const idxInput = ref('194989')
const selectedPair = ref('')
const batchInput = ref('194989\n218785')
const batchSubmitting = ref(false)

const manualFunc = ref('')
const manualUrl = ref('')
const manualVersion = ref('vulnerable')
const manualIdx = ref('')
const manualSubmitting = ref(false)
const uploadName = ref('')
const uploadText = ref('')
const uploadSubmitting = ref(false)

const batch = ref(null)
const currentRun = ref(null)
const events = ref([])
const traceAfter = ref(0)
const report = ref(null)
const resultRecords = ref([])
const reportLoading = ref(false)
const expanded = ref(new Set())
const toast = ref('')
const timelineEl = ref(null)

const kbQuery = ref('')
const kbProject = ref('')
const kbCwe = ref('')
const kbTopK = ref('5')
const kbLoading = ref(false)
const kbResult = ref(null)
const kbError = ref('')

let runTimer = null
let batchTimer = null
let toastTimer = null

const stub = computed(() => Boolean(health.value?.provider?.is_stub))
const running = computed(
  () =>
    currentRun.value &&
    ['queued', 'preparing_snapshot', 'running'].includes(currentRun.value.status)
)

const TYPE_LABELS = {
  run_start: 'RUN',
  llm_step: 'LLM 决策',
  repo_tool: 'TOOL',
  decision: 'DECISION',
  report: 'REPORT',
  run_end: 'END',
  error: 'ERROR',
  knowledge_search: 'KNOWLEDGE',
}

const STATUS_LABELS = {
  queued: '排队',
  preparing_snapshot: '准备快照',
  running: '运行中',
  done: '完成',
  failed: '失败',
  partial_failed: '部分失败',
}

function statusLabel(status) {
  return STATUS_LABELS[status] || status || ''
}

const runLabels = computed(() => {
  const map = {}
  for (const event of events.value) {
    if (event.type === 'run_start' && event.payload?.idx != null) {
      map[event.run_id] = event.payload.idx
    }
  }
  return map
})

function showToast(message) {
  toast.value = message
  clearTimeout(toastTimer)
  toastTimer = setTimeout(() => {
    toast.value = ''
  }, 5000)
}

function fail(error) {
  showToast(error instanceof ApiError ? error.message : String(error))
}

function typeLabel(type) {
  return TYPE_LABELS[type] || type
}

function pretty(value) {
  try {
    return JSON.stringify(value, null, 2)
  } catch (error) {
    return String(value)
  }
}

function sampleLabel(event) {
  const idx = runLabels.value[event.run_id]
  return idx != null ? `idx=${idx}` : ''
}

function eventSummary(event) {
  const payload = event.payload || {}
  if (event.type === 'run_start') {
    return `provider=${payload.provider}${payload.is_stub ? '（stub 占位模型）' : ''} · 快照 ${payload.snapshot_dir} · 上限 ${payload.max_steps} 步 / ${payload.timeout_seconds}s`
  }
  if (event.type === 'llm_step') {
    const calls = (payload.tool_calls || []).map((call) => call.name)
    const decision = calls.length
      ? `模型决定调用：${calls.join(', ')}`
      : '模型准备给出最终结论'
    return `第 ${payload.step} 步 · ${decision}${payload.content ? ` · ${payload.content}` : ''}`
  }
  if (event.type === 'repo_tool') {
    const summary = payload.summary || {}
    const parts = []
    if (payload.arguments?.pattern) parts.push(`pattern=${payload.arguments.pattern}`)
    if (payload.arguments?.path) parts.push(`path=${payload.arguments.path}`)
    if (summary.total_matches != null) parts.push(`命中 ${summary.total_matches}`)
    if (summary.hits?.length) {
      parts.push(summary.hits.map((hit) => `${hit.path}:${hit.line}`).join(', '))
    }
    if (summary.path) {
      parts.push(`${summary.path}:${summary.start_line}-${summary.end_line}`)
    }
    if (summary.error) parts.push(summary.error)
    return parts.join(' · ') || '工具调用完成'
  }
  if (event.type === 'decision') {
    return `${payload.verdict} → prediction=${payload.prediction} · ${payload.reason}`
  }
  if (event.type === 'run_end') {
    return `收尾：${payload.steps} 步 / ${payload.tool_calls} 次工具 · timed_out=${payload.timed_out} · hit_max_steps=${payload.hit_max_steps}`
  }
  if (event.type === 'error') {
    return `${payload.reason || 'error'} ${payload.detail || ''}`.trim()
  }
  return ''
}

function toggle(index) {
  const next = new Set(expanded.value)
  if (next.has(index)) {
    next.delete(index)
  } else {
    next.add(index)
  }
  expanded.value = next
}

function toolSummary(call) {
  const summary = call.summary || {}
  const parts = []
  if (summary.total_matches != null) parts.push(`命中 ${summary.total_matches}`)
  if (summary.paths?.length) parts.push(`文件 ${summary.paths.join(', ')}`)
  if (summary.hits?.length) {
    parts.push(summary.hits.map((hit) => `${hit.path}:${hit.line}`).join(', '))
  }
  if (summary.path) {
    parts.push(`${summary.path}:${summary.start_line}-${summary.end_line} / 共 ${summary.total_lines} 行`)
  }
  if (summary.scanned_files != null) parts.push(`扫描 ${summary.scanned_files} 个文件`)
  if (summary.truncated) parts.push('结果被截断')
  if (summary.error) parts.push(`错误：${summary.error}`)
  return parts.join(' · ')
}

async function scrollTimeline() {
  await nextTick()
  const element = timelineEl.value
  if (element) {
    element.scrollTop = element.scrollHeight
  }
}

watch(
  () => events.value.length,
  () => {
    scrollTimeline()
  }
)

async function loadHealth() {
  try {
    health.value = await api.health()
  } catch (error) {
    fail(error)
  }
}

async function loadPairs() {
  try {
    pairs.value = (await api.pairs(30)).pairs
  } catch (error) {
    fail(error)
  }
}

async function loadRecent() {
  try {
    recentRuns.value = (await api.runs(12)).runs
  } catch (error) {
    fail(error)
  }
}

function stopRunPolling() {
  if (runTimer) {
    clearInterval(runTimer)
    runTimer = null
  }
}

async function loadReport(runId) {
  reportLoading.value = true
  try {
    const data = await api.getReport(runId)
    report.value = data.report
    resultRecords.value = data.result_records || []
  } catch (error) {
    report.value = null
    resultRecords.value = []
    fail(error)
  } finally {
    reportLoading.value = false
  }
}

async function pollRun() {
  const run = currentRun.value
  if (!run) {
    stopRunPolling()
    return
  }
  try {
    const trace = await api.getTrace(run.run_id, traceAfter.value)
    if (trace.events.length) {
      events.value.push(...trace.events)
      traceAfter.value = trace.next
    }

    const latest = await api.getRun(run.run_id)
    currentRun.value = latest

    if (latest.status === 'done') {
      stopRunPolling()
      await loadReport(run.run_id)
      const category = latest.pair_category ? ` · ${latest.pair_category}` : ''
      showToast(`运行完成${category}`)
      loadRecent()
    } else if (latest.status === 'failed') {
      stopRunPolling()
      showToast(`运行失败：${latest.error || '未知错误'}`)
      loadRecent()
    }
  } catch (error) {
    stopRunPolling()
    fail(error)
  }
}

function startRunPolling() {
  stopRunPolling()
  runTimer = setInterval(pollRun, 700)
}

async function startRun(idx) {
  if (!Number.isInteger(idx) || idx < 0) {
    showToast('请输入合法的样本 idx')
    return
  }
  stopRunPolling()
  try {
    const run = await api.createRun(idx, true)
    currentRun.value = run
    events.value = []
    traceAfter.value = 0
    report.value = null
    resultRecords.value = []
    expanded.value = new Set()
    startRunPolling()
    loadRecent()
  } catch (error) {
    fail(error)
  }
}

function runSingle() {
  startRun(Number(idxInput.value))
}

function runPair() {
  const idx = Number(selectedPair.value)
  if (!Number.isInteger(idx)) {
    showToast('请先选择一对样本')
    return
  }
  idxInput.value = String(idx)
  startRun(idx)
}

function applyRun(run) {
  currentRun.value = run
  events.value = []
  traceAfter.value = 0
  report.value = null
  resultRecords.value = []
  expanded.value = new Set()
}

async function runManual() {
  const func = manualFunc.value.trim()
  const commitUrl = manualUrl.value.trim()
  if (!func) {
    showToast('请粘贴待检测的函数代码')
    return
  }
  if (!commitUrl) {
    showToast('请填写修复提交链接')
    return
  }
  const idxText = String(manualIdx.value ?? '').trim()
  const idx = idxText === '' ? null : Number(idxText)
  if (idx !== null && (!Number.isInteger(idx) || idx < 0)) {
    showToast('idx 需为 >= 0 的整数，或留空')
    return
  }
  manualSubmitting.value = true
  try {
    const run = await api.manualRun({
      func,
      commitUrl,
      version: manualVersion.value,
      idx,
    })
    applyRun(run)
    showToast('已提交手工样本：正在准备快照…')
    startRunPolling()
    loadRecent()
  } catch (error) {
    fail(error)
  } finally {
    manualSubmitting.value = false
  }
}

async function handleUpload(event) {
  const file = event.target.files?.[0]
  uploadName.value = file ? file.name : ''
  uploadText.value = ''
  if (!file) return
  try {
    uploadText.value = await file.text()
    showToast(`已读取 ${file.name}`)
  } catch (error) {
    fail(error)
  }
}

async function submitUpload() {
  if (!uploadText.value) {
    showToast('请先选择 .jsonl 文件')
    return
  }
  uploadSubmitting.value = true
  try {
    batch.value = await api.uploadBatch(uploadText.value)
    showToast(`已提交 ${batch.value.total} 条上传样本`)
    startBatchPolling()
  } catch (error) {
    fail(error)
  } finally {
    uploadSubmitting.value = false
  }
}

async function openRun(runId) {
  stopRunPolling()
  try {
    currentRun.value = await api.getRun(runId)
    events.value = []
    traceAfter.value = 0
    report.value = null
    resultRecords.value = []
    expanded.value = new Set()

    if (currentRun.value.status === 'done') {
      await pollRun()
    } else {
      await pollRun()
      if (
        currentRun.value &&
        ['queued', 'preparing_snapshot', 'running'].includes(currentRun.value.status)
      ) {
        startRunPolling()
      }
    }
  } catch (error) {
    fail(error)
  }
}

function parseIdxList(text) {
  return [
    ...new Set(
      text
        .split(/[\s,;，；]+/)
        .map((item) => item.trim())
        .filter(Boolean)
        .map(Number)
        .filter((value) => Number.isInteger(value) && value >= 0)
    ),
  ]
}

function stopBatchPolling() {
  if (batchTimer) {
    clearInterval(batchTimer)
    batchTimer = null
  }
}

async function pollBatch() {
  if (!batch.value) {
    stopBatchPolling()
    return
  }
  try {
    batch.value = await api.getBatch(batch.value.batch_id)
    if (!['queued', 'running'].includes(batch.value.status)) {
      stopBatchPolling()
      showToast(
        `批量完成：${batch.value.done}/${batch.value.total} 成功，${batch.value.failed} 失败`
      )
      loadRecent()
    }
  } catch (error) {
    stopBatchPolling()
    fail(error)
  }
}

function startBatchPolling() {
  stopBatchPolling()
  batchTimer = setInterval(pollBatch, 1000)
}

async function submitBatch() {
  const idxList = parseIdxList(batchInput.value)
  if (!idxList.length) {
    showToast('请输入至少一个 idx')
    return
  }
  batchSubmitting.value = true
  try {
    batch.value = await api.createBatch(idxList, true)
    showToast(`批量已提交：${batch.value.total} 条`)
    startBatchPolling()
  } catch (error) {
    fail(error)
  } finally {
    batchSubmitting.value = false
  }
}

async function searchKnowledge() {
  const query = kbQuery.value.trim()
  if (!query) {
    showToast('请输入知识库检索词')
    return
  }
  kbLoading.value = true
  kbError.value = ''
  try {
    kbResult.value = await api.knowledgeSearch(query, {
      topK: Number(kbTopK.value) || 5,
      project: kbProject.value.trim(),
      cwe: kbCwe.value.trim(),
    })
  } catch (error) {
    kbResult.value = null
    kbError.value = error instanceof ApiError ? error.message : String(error)
    fail(error)
  } finally {
    kbLoading.value = false
  }
}

onMounted(() => {
  loadHealth()
  loadPairs()
  loadRecent()
})

onBeforeUnmount(() => {
  stopRunPolling()
  stopBatchPolling()
  clearTimeout(toastTimer)
})
</script>

<template>
  <div class="page">
    <header class="topbar">
      <div>
        <h1>FuncGurad <span>Agent 检测台</span></h1>
        <p class="sub">只读仓库快照 · 自主工具循环 · 实时轨迹</p>
      </div>
      <div class="model-banner" :class="{ stub }">
        <span class="dot" :class="{ stub }"></span>
        <div>
          <strong v-if="health">
            {{ health.provider.name }}
            <template v-if="health.provider.is_stub">（占位模型 stub）</template>
          </strong>
          <strong v-else>连接中……</strong>
          <small v-if="stub">结果不代表真实检测能力</small>
          <small v-else-if="health">{{ health.provider.model || '真实模型' }}</small>
        </div>
      </div>
    </header>

    <div v-if="health?.status === 'degraded'" class="data-warning">
      <strong>数据未就绪</strong>
      <span>{{ health.message }}</span>
      <code v-if="health.missing?.length">缺失：{{ health.missing.join('、') }}</code>
    </div>

    <transition name="toast">
      <div v-if="toast" class="toast">{{ toast }}</div>
    </transition>

    <main class="grid">
      <section class="panel input-panel">
        <h2>1. 输入</h2>

        <label>样本 idx</label>
        <div class="row">
          <input
            v-model="idxInput"
            type="number"
            min="0"
            placeholder="例如 194989"
            @keyup.enter="runSingle"
          />
          <button class="primary" :disabled="running" @click="runSingle">
            {{ running ? '运行中…' : '运行单条' }}
          </button>
        </div>
        <p class="hint">会自动补齐相邻配对样本，用于计算 P-x 类别。</p>

        <label>选择一对样本</label>
        <select v-model="selectedPair">
          <option value="">-- 选择配对 --</option>
          <option v-for="pair in pairs" :key="pair.pair_no" :value="pair.vulnerable_idx">
            #{{ pair.pair_no }} · idx {{ pair.vulnerable_idx }} / {{ pair.fixed_idx }}
          </option>
        </select>
        <button class="ghost" :disabled="!selectedPair || running" @click="runPair">
          运行所选配对
        </button>

        <hr />

        <h2>手工样本</h2>
        <p class="hint">
          粘贴一个版本的函数与修复提交链接。每次只分析一个版本，版本角色由你选择。
        </p>
        <label>函数代码</label>
        <textarea
          v-model="manualFunc"
          rows="6"
          class="code-input"
          placeholder="int demo(char *dst, int len) { ... }"
        ></textarea>
        <label>修复提交链接</label>
        <input
          v-model="manualUrl"
          placeholder="https://github.com/owner/repo/commit/<sha>"
        />
        <div class="row manual-meta">
          <select v-model="manualVersion">
            <option value="vulnerable">修复前（漏洞版）</option>
            <option value="fixed">修复后（修复版）</option>
          </select>
          <input
            v-model="manualIdx"
            type="number"
            min="0"
            placeholder="idx（可选，报告用）"
          />
        </div>
        <button
          class="primary"
          :disabled="manualSubmitting || running"
          @click="runManual"
        >
          {{ manualSubmitting ? '提交中…' : '运行手工样本' }}
        </button>
        <p class="hint">
          首次运行会按提交链接准备仓库快照，大仓库可能需要数分钟；状态显示「准备快照」时属正常等待。
        </p>

        <hr />

        <h2>批量任务</h2>
        <textarea
          v-model="batchInput"
          rows="4"
          placeholder="每行或逗号分隔多个 idx，例如 194989, 218785"
        ></textarea>
        <button class="ghost" :disabled="batchSubmitting" @click="submitBatch">
          {{ batchSubmitting ? '提交中…' : '提交批量' }}
        </button>

        <hr />

        <h2>上传 .jsonl</h2>
        <p class="hint">
          每行一条样本，字段为 idx / target / commit_url / func。与按 idx 的批量不同，这里不要求按对出现。
        </p>
        <input
          type="file"
          accept=".jsonl,application/json"
          @change="handleUpload"
        />
        <p v-if="uploadName" class="small mono">已选择：{{ uploadName }}</p>
        <button
          class="ghost"
          :disabled="uploadSubmitting || !uploadText"
          @click="submitUpload"
        >
          {{ uploadSubmitting ? '提交中…' : '上传并检测' }}
        </button>

        <div v-if="batch" class="batch">
          <div class="batch-head">
            <strong>批量 {{ batch.batch_id }}</strong>
            <span class="badge" :class="batch.status">{{ statusLabel(batch.status) }}</span>
          </div>
          <p class="small">
            {{ batch.done }}/{{ batch.total }} 完成
            <span v-if="batch.failed"> · {{ batch.failed }} 失败</span>
          </p>
          <ul class="batch-list">
            <li v-for="item in batch.items" :key="item.run_id">
              <span class="badge" :class="item.status">{{ statusLabel(item.status) }}</span>
              <span class="mono small">idx {{ item.requested_idx }}</span>
              <span v-if="item.pair_category" class="cat">{{ item.pair_category }}</span>
              <button
                class="link"
                :disabled="item.status === 'queued'"
                @click="openRun(item.run_id)"
              >
                查看轨迹
              </button>
            </li>
          </ul>
        </div>

        <div v-if="recentRuns.length" class="recent">
          <h3>最近运行</h3>
          <button
            v-for="run in recentRuns"
            :key="run.run_id"
            class="link recent-item"
            @click="openRun(run.run_id)"
          >
            <span class="badge" :class="run.status">{{ statusLabel(run.status) }}</span>
            <span class="mono small">{{ run.run_id }}</span>
            <span v-if="run.pair_category" class="cat">{{ run.pair_category }}</span>
          </button>
        </div>
      </section>

      <section class="panel trace-panel">
        <div class="panel-head">
          <h2>2. 实时轨迹</h2>
          <span v-if="currentRun" class="mono small">{{ currentRun.run_id }}</span>
        </div>

        <div v-if="!currentRun" class="empty">
          运行一条样本后，这里会按时间顺序流出 Agent 的每一步：
          模型决定、工具调用、命中位置、耗时与最终判定。
        </div>

        <div v-else ref="timelineEl" class="timeline">
          <template v-for="(event, index) in events" :key="`${event.run_id}-${event.seq}-${index}`">
            <div
              v-if="index === 0 || events[index - 1].run_id !== event.run_id"
              class="sample-sep"
            >
              样本 {{ sampleLabel(event) }} · {{ event.run_id }}
            </div>

            <article
              class="event"
              :class="[event.type, event.status]"
              @click="toggle(index)"
            >
              <div class="event-head">
                <span class="seq">#{{ event.seq }}</span>
                <span class="type">{{ typeLabel(event.type) }}</span>
                <span v-if="event.name" class="name">{{ event.name }}</span>
                <span v-if="event.duration_ms != null" class="dur">
                  {{ event.duration_ms }}ms
                </span>
                <span class="status-dot" :class="event.status"></span>
              </div>
              <div class="event-body">{{ eventSummary(event) }}</div>
              <pre v-if="expanded.has(index)" class="event-raw">{{ pretty(event) }}</pre>
            </article>
          </template>

          <div v-if="running" class="waiting">
            <span class="spinner"></span>
            Agent 正在运行，轨迹实时追加……
          </div>
        </div>
      </section>

      <section class="panel report-panel">
        <div class="panel-head">
          <h2>3. 报告</h2>
          <span v-if="currentRun" class="badge" :class="currentRun.status">
            {{ statusLabel(currentRun.status) }}
          </span>
        </div>

        <div v-if="reportLoading" class="empty">加载报告中……</div>
        <div v-else-if="!report" class="empty">
          运行完成后展示结果、P-x 类别、理由与证据文件路径。
        </div>

        <template v-else>
          <div v-if="report.pair" class="report-top">
            <div class="category" :class="report.pair.category">
              {{ report.pair.category }}
            </div>
            <div>
              <strong>{{ report.pair.meaning }}</strong>
              <p class="small">
                漏洞版 {{ report.pair.vulnerable_idx }} · 修复版
                {{ report.pair.fixed_idx }}
              </p>
              <p class="small mono">{{ JSON.stringify(report.pair.predictions) }}</p>
            </div>
          </div>

          <div v-if="report.provider?.is_stub" class="stub-note">
            {{ report.stub_notice || '当前为 stub 占位模型，结果不代表真实检测能力。' }}
          </div>

          <div class="result-lines">
            <p class="small">result.jsonl（严格两字段）</p>
            <code v-for="record in resultRecords" :key="record.idx">
              {"idx": {{ record.idx }}, "prediction": {{ record.prediction }}}
            </code>
          </div>

          <article v-for="run in report.runs" :key="run.idx" class="sample-report">
            <div class="sample-head">
              <strong>idx {{ run.idx }}</strong>
              <span class="badge" :class="run.verdict">{{ run.verdict }}</span>
              <span class="mono small">prediction={{ run.prediction }}</span>
              <span class="small">{{ run.steps }} 步 / {{ run.tool_calls.length }} 次工具</span>
            </div>

            <p class="reason">{{ run.reason }}</p>

            <div class="gate" :class="{ on: run.evidence_gate?.applied }">
              证据门控：
              {{ run.evidence_gate?.applied ? '已触发' : '未触发' }} ·
              {{ run.evidence_gate?.note }}
            </div>

            <div v-if="run.evidence?.length" class="evidence">
              <p class="small">证据</p>
              <ul>
                <li v-for="(item, index) in run.evidence" :key="index">
                  <code>{{ item.path }}:{{ item.line }}</code> {{ item.note }}
                </li>
              </ul>
            </div>

            <details>
              <summary>工具调用轨迹（{{ run.tool_calls.length }}）</summary>
              <div
                v-for="(call, index) in run.tool_calls"
                :key="index"
                class="tool-call"
              >
                <div class="tool-head">
                  <strong>{{ call.name }}</strong>
                  <span class="badge" :class="call.ok ? 'ok' : 'failed'">
                    {{ call.ok ? 'ok' : 'error' }}
                  </span>
                  <span class="small">{{ call.elapsed_ms }}ms</span>
                </div>
                <pre>{{ pretty(call.arguments) }}</pre>
                <p class="small">{{ toolSummary(call) }}</p>
              </div>
            </details>
          </article>

          <details class="raw-report">
            <summary>report.json 原始内容</summary>
            <pre>{{ pretty(report) }}</pre>
          </details>
        </template>
      </section>

      <section class="panel knowledge-panel">
        <div class="panel-head">
          <h2>4. 知识库检索</h2>
          <span v-if="kbResult" class="mono small">
            {{ kbResult.retriever }} · {{ kbResult.elapsed_ms }}ms ·
            {{ kbResult.candidates }} 候选
          </span>
        </div>

        <div class="row">
          <input
            v-model="kbQuery"
            placeholder="漏洞现象或代码关键词，例如 整数溢出 长度校验缺失"
            @keyup.enter="searchKnowledge"
          />
          <button class="primary" :disabled="kbLoading" @click="searchKnowledge">
            {{ kbLoading ? '检索中…' : '检索' }}
          </button>
        </div>

        <div class="kb-filters">
          <input v-model="kbProject" placeholder="项目过滤（可选），如 openssl" />
          <input v-model="kbCwe" placeholder="CWE 过滤（可选），如 CWE-787" />
          <input v-model="kbTopK" type="number" min="1" max="20" title="返回条数" />
        </div>

        <div v-if="kbError" class="kb-error">{{ kbError }}</div>
        <div v-else-if="!kbResult" class="empty">
          检索历史 CVE 卡片，查看项目、漏洞类型与相似度；Agent 也可通过
          search_knowledge 工具自主调用。
        </div>
        <ul v-else-if="kbResult.hits.length" class="kb-list">
          <li v-for="hit in kbResult.hits" :key="hit.ref">
            <div class="kb-head">
              <strong>{{ hit.cve || '无 CVE 编号' }}</strong>
              <span class="badge">{{ hit.project }}</span>
              <span class="kb-score">{{ hit.score.toFixed(2) }}</span>
            </div>
            <p class="small mono">{{ hit.func_signature || '（无函数签名）' }}</p>
            <p class="kb-summary">{{ hit.summary }}</p>
            <p class="small">
              {{ hit.cwe.join(', ') || '未标注 CWE' }} · {{ hit.ref }} ·
              {{ hit.retriever }}
            </p>
          </li>
        </ul>
        <div v-else class="empty">没有命中，换个关键词或放宽过滤条件。</div>
      </section>
    </main>

    <footer class="footer">
      只读工具限定在快照目录内，前端不参与任何业务判断。当前模型：
      {{ health?.provider?.name || '未知' }}
      <span v-if="stub">（stub 占位模型，结果不代表真实检测能力）</span>
    </footer>
  </div>
</template>
