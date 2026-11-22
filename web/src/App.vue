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

let runTimer = null
let batchTimer = null
let toastTimer = null

const stub = computed(() => Boolean(health.value?.provider?.is_stub))
const running = computed(
  () => currentRun.value && ['queued', 'running'].includes(currentRun.value.status)
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
      if (currentRun.value && ['queued', 'running'].includes(currentRun.value.status)) {
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

        <h2>批量任务</h2>
        <textarea
          v-model="batchInput"
          rows="4"
          placeholder="每行或逗号分隔多个 idx，例如 194989, 218785"
        ></textarea>
        <button class="ghost" :disabled="batchSubmitting" @click="submitBatch">
          {{ batchSubmitting ? '提交中…' : '提交批量' }}
        </button>

        <div v-if="batch" class="batch">
          <div class="batch-head">
            <strong>批量 {{ batch.batch_id }}</strong>
            <span class="badge" :class="batch.status">{{ batch.status }}</span>
          </div>
          <p class="small">
            {{ batch.done }}/{{ batch.total }} 完成
            <span v-if="batch.failed"> · {{ batch.failed }} 失败</span>
          </p>
          <ul class="batch-list">
            <li v-for="item in batch.items" :key="item.run_id">
              <span class="badge" :class="item.status">{{ item.status }}</span>
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
            <span class="badge" :class="run.status">{{ run.status }}</span>
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
            {{ currentRun.status }}
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
    </main>

    <footer class="footer">
      只读工具限定在快照目录内，前端不参与任何业务判断。当前模型：
      {{ health?.provider?.name || '未知' }}
      <span v-if="stub">（stub 占位模型，结果不代表真实检测能力）</span>
    </footer>
  </div>
</template>
