<template>
  <section class="page" data-module="audit">
    <header class="page-head">
      <div>
        <h2>数据审计管理</h2>
        <p class="page-desc">维护审计记录，围绕记录编号、操作类型、操作人、操作对象做登记、筛选与状态流转。</p>
      </div>
      <div class="page-actions">
        <button class="btn primary" type="button" @click="openCreate">登记审计记录</button>
        <button class="btn" type="button" @click="openTrace">回执差错追查</button>
        <button class="btn" type="button" @click="exportRows">导出数据审计清单</button>
      </div>
    </header>

    <div class="stat-row">
      <article v-for="item in stats" :key="item.label" class="stat-card">
        <span class="stat-label">{{ item.label }}</span>
        <strong class="stat-value">{{ item.value }}</strong>
      </article>
    </div>

    <form class="filter-bar" @submit.prevent="reload">
      <label v-for="field in filterFields" :key="field" class="filter-item">
        <span>{{ field }}</span>
        <input v-model="filters[field]" :placeholder="`按${field}检索`" />
      </label>
      <button class="btn" type="submit">查询</button>
      <button class="btn ghost" type="button" @click="resetFilters">重置条件</button>
    </form>

    <table class="data-table">
      <thead>
        <tr>
          <th v-for="column in columns" :key="column">{{ column }}</th>
          <th>可执行动作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in rows" :key="String(row.id)">
          <td v-for="column in columns" :key="column">{{ row[column] ?? '—' }}</td>
          <td class="row-actions">
            <button
              v-for="action in actions"
              :key="action"
              class="link"
              type="button"
              @click="runAction(action, row)"
            >
              {{ action }}
            </button>
          </td>
        </tr>
        <tr v-if="!rows.length">
          <td :colspan="columns.length + 1" class="empty-state">暂无数据审计数据，可先登记审计记录</td>
        </tr>
      </tbody>
    </table>

    <footer class="page-foot">
      <span>共 {{ total }} 条数据审计记录</span>
      <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
    </footer>

    <Teleport to="body">
      <div v-if="showCreateModal" class="modal-overlay" @click.self="showCreateModal = false">
        <div class="modal-card">
          <header class="modal-head">
            <h3>登记审计记录</h3>
            <button class="btn ghost" type="button" @click="showCreateModal = false">&times;</button>
          </header>
          <form class="modal-body" @submit.prevent="submitCreate">
            <label v-for="field in columns.slice(0, 3)" :key="field" class="modal-field">
              <span>{{ field }}</span>
              <input v-model="createFields[field]" :placeholder="`请输入${field}`" required />
            </label>
            <footer class="modal-foot">
              <button class="btn" type="button" @click="showCreateModal = false">取消</button>
              <button class="btn primary" type="submit">提交登记</button>
            </footer>
          </form>
        </div>
      </div>
    </Teleport>

    <!-- 回执差错追查入口 -->
    <Teleport to="body">
      <div v-if="showTraceModal" class="modal-overlay" @click.self="closeTrace">
        <div class="modal-card modal-card-wide">
          <header class="modal-head">
            <h3>回执差错追查</h3>
            <button class="btn ghost" type="button" @click="closeTrace">&times;</button>
          </header>

          <div class="modal-body">
            <form class="modal-body-inner" @submit.prevent="submitTrace">
              <label v-for="field in traceFields" :key="field" class="modal-field">
                <span>{{ field }}<em v-if="field === '审计操作编号'">（必填）</em></span>
                <input v-model="traceFieldsValue[field]" :placeholder="field === '审计操作编号' ? '请输入审计操作编号' : `可选，留空表示不按${field}收窄`" />
              </label>
              <footer class="modal-foot">
                <button class="btn" type="button" @click="loadTraceList">查看历史追查</button>
                <button class="btn primary" type="submit">发起追查</button>
              </footer>
            </form>

            <div v-if="traceErrorMessage" class="error-text">{{ traceErrorMessage }}</div>

            <!-- 追查结论 -->
            <div v-if="traceReport" class="trace-panel">
              <h4>核对结论（{{ traceReport.追查任务.追查编号 }} · {{ traceReport.追查任务.status }}）</h4>
              <div class="trace-tags">
                <span v-for="code in traceReport.差错类型" :key="code" class="tag tag-warn">{{ issueLabel(code) }}</span>
                <span v-if="!traceReport.差错类型.length" class="tag">未发现差错</span>
              </div>
              <div class="trace-cols">
                <div>
                  <strong>待复跑节点</strong>
                  <p>{{ traceReport.待复跑节点.length ? traceReport.待复跑节点.join('、') : '无' }}</p>
                </div>
                <div>
                  <strong>未受影响节点</strong>
                  <p>{{ traceReport.未受影响节点.length ? traceReport.未受影响节点.join('、') : '无' }}</p>
                </div>
              </div>
              <div>
                <strong>受影响结果（按实际产物引用，指纹去重）</strong>
                <ul class="trace-list">
                  <li v-for="item in traceReport.受影响结果清单" :key="item.下游结果编号">
                    {{ item.下游结果编号 }} · 指纹 {{ item.引用产物指纹.join(' / ') }}
                    <span v-if="item.指纹冲突" class="tag tag-warn">同结果指纹冲突</span>
                    <span v-if="item.产物缺失" class="tag tag-warn">产物缺失待补</span>
                  </li>
                  <li v-if="!traceReport.受影响结果清单.length">无</li>
                </ul>
              </div>

              <h4>节点明细与可疑产物索引</h4>
              <table class="data-table trace-table">
                <thead>
                  <tr>
                    <th>节点编号</th>
                    <th>差错</th>
                    <th>实际产物指纹（去重）</th>
                    <th>下游消费</th>
                    <th>处理</th>
                  </tr>
                </thead>
                <tbody>
                  <tr v-for="node in traceReport.节点核对" :key="node.节点编号">
                    <td>{{ node.节点编号 }}</td>
                    <td>
                      <span v-for="issue in node.差错清单" :key="issue.差错类型 + (issue.关联回执编号 || []).join()" class="tag tag-warn">
                        {{ issueLabel(issue.差错类型) }}
                        <template v-if="issue.涉及尝试编号 && issue.涉及尝试编号.length">（尝试 {{ issue.涉及尝试编号.join('、') }}）</template>
                      </span>
                      <span v-if="!node.差错清单.length" class="tag">正常</span>
                    </td>
                    <td>
                      <span v-for="product in node.产物索引" :key="product.数据产物指纹" class="tag">
                        {{ product.数据产物指纹 }}
                        <template v-if="product.引用尝试编号.length > 1">（{{ product.引用尝试编号.length }} 次引用计 1 次）</template>
                      </span>
                      <span v-if="!node.产物索引.length">—</span>
                    </td>
                    <td>{{ node.产物索引.some((p: TraceProduct) => p.仍被下游消费) ? '仍在消费' : '无' }}</td>
                    <td>{{ node.待复跑 ? '需复跑' : '无需处理' }}</td>
                  </tr>
                </tbody>
              </table>

              <h4>留存证据（核销只标记状态，回执与产物均不删除）</h4>
              <table class="data-table trace-table">
                <thead>
                  <tr>
                    <th>回执编号</th>
                    <th>节点/尝试</th>
                    <th>回执结果</th>
                    <th>节点终态</th>
                    <th>产物指纹</th>
                    <th>迟到</th>
                    <th>核销状态</th>
                    <th>操作</th>
                  </tr>
                </thead>
                <tbody>
                  <tr v-for="evidence in traceReport.留存证据" :key="evidence.回执编号">
                    <td>{{ evidence.回执编号 }}</td>
                    <td>{{ evidence.节点编号 }} / {{ evidence.尝试编号 }}</td>
                    <td>{{ evidence.回执结果 }}</td>
                    <td>{{ evidence.节点终态 }}</td>
                    <td>{{ evidence.数据产物指纹 }}</td>
                    <td>{{ evidence.迟到 ? '是' : '否' }}</td>
                    <td>
                      <span :class="['tag', evidence.核销状态 === '已核销' ? 'tag-ok' : 'tag-warn']">{{ evidence.核销状态 }}</span>
                    </td>
                    <td>
                      <button
                        v-if="evidence.核销状态 !== '已核销'"
                        class="link"
                        type="button"
                        @click="writeOff(evidence.回执编号)"
                      >
                        核销
                      </button>
                      <span v-else>证据已留存</span>
                    </td>
                  </tr>
                </tbody>
              </table>
            </div>

            <!-- 历史追查任务 -->
            <div v-if="traceList.length" class="trace-panel">
              <h4>历史追查任务</h4>
              <table class="data-table trace-table">
                <thead>
                  <tr>
                    <th>追查编号</th>
                    <th>审计操作编号</th>
                    <th>范围</th>
                    <th>待复跑节点</th>
                    <th>状态</th>
                    <th>操作</th>
                  </tr>
                </thead>
                <tbody>
                  <tr v-for="item in traceList" :key="item.id">
                    <td>{{ item.追查编号 }}</td>
                    <td>{{ item.审计操作编号 }}</td>
                    <td>{{ traceScopeText(item) }}</td>
                    <td>{{ item.待复跑节点.join('、') || '无' }}</td>
                    <td>{{ item.status }}</td>
                    <td><button class="link" type="button" @click="openTraceDetail(item.id)">查看报告</button></td>
                  </tr>
                </tbody>
              </table>
            </div>
          </div>
        </div>
      </div>
    </Teleport>
  </section>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'

import { request } from '@/api/client'

type Row = Record<string, string | number | null>
interface TraceProduct {
  数据产物指纹: string
  引用尝试编号: string[]
  仍被下游消费: boolean
}
interface TraceTask {
  id: number
  追查编号: string
  审计操作编号: string
  节点编号: string
  尝试编号: string
  输入批号: string
  数据产物指纹: string
  status: string
  待复跑节点: string[]
}
interface TraceReport {
  追查任务: { 追查编号: string; status: string }
  差错类型: string[]
  待复跑节点: string[]
  未受影响节点: string[]
  受影响结果清单: Array<{ 下游结果编号: string; 引用产物指纹: string[]; 指纹冲突: boolean; 产物缺失: boolean }>
  节点核对: Array<{
    节点编号: string
    待复跑: boolean
    差错清单: Array<{ 差错类型: string; 涉及尝试编号: string[]; 关联回执编号: string[] }>
    产物索引: TraceProduct[]
  }>
  留存证据: Array<{ 回执编号: string; 节点编号: string; 尝试编号: string; 回执结果: string; 节点终态: string; 数据产物指纹: string; 迟到: boolean; 核销状态: string }>
}

const ENDPOINT = '/api/audit'
const TRACE_ENDPOINT = `${ENDPOINT}/receipt-traces`
const columns = ["记录编号", "操作类型", "操作人", "操作对象", "操作时间", "变更内容", "审计结果", "记录状态"]
const actions = ["提交审计", "标记异常"]
const statuses = ["已记录", "已审计", "异常"]
const stats = [{"label": "审计记录数", "value": 0}, {"label": "异常记录数", "value": 0}, {"label": "已审计记录数", "value": 0}]

const ISSUE_LABELS: Record<string, string> = {
  terminal_mismatch: '终态对不上',
  product_misplaced: '产物引用错位',
  success_missing: '成功回执丢失',
}

const rows = ref<Row[]>([])
const total = ref(0)
const errorMessage = ref('')
const filters = ref<Record<string, string>>({})
const filterFields = columns.slice(0, 3)
const showCreateModal = ref(false)
const createFields = ref<Record<string, string>>({})

const showTraceModal = ref(false)
const traceFields = ["审计操作编号", "节点编号", "尝试编号", "输入批号", "数据产物指纹"] as const
const traceFieldsValue = ref<Record<string, string>>({})
const traceReport = ref<TraceReport | null>(null)
const traceList = ref<TraceTask[]>([])
const traceErrorMessage = ref('')
const activeTraceId = ref<number | null>(null)

function issueLabel(code: string): string {
  return ISSUE_LABELS[code] ?? code
}

function traceScopeText(item: TraceTask): string {
  return [item.节点编号, item.尝试编号, item.输入批号, item.数据产物指纹]
    .map((value) => String(value ?? ''))
    .filter(Boolean)
    .join(' / ') || '全局'
}

function updateStats() {
  const data = rows.value
  stats.length > 0 && (stats[0].value = data.filter((r: Row) => r.status === statuses[1] || r.pending).length)
  stats.length > 1 && (stats[1].value = data.filter((r: Row) => r.status && !r.pending && !r.abnormal).length)
  stats.length > 2 && (stats[2].value = data.filter((r: Row) => r.abnormal).length)
}

function resetFilters() {
  filters.value = {}
  void reload()
}

function exportRows() {
  window.open(`${ENDPOINT}/export`, '_blank')
}

function openCreate() {
  errorMessage.value = ''
  createFields.value = {}
  showCreateModal.value = true
}

async function submitCreate() {
  errorMessage.value = ''
  try {
    const response = await request(ENDPOINT, {
      method: 'POST',
      body: JSON.stringify({ values: createFields.value }),
    })
    if (!response.ok) {
      const data = await response.json()
      throw new Error(data.message || '登记失败')
    }
    showCreateModal.value = false
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '登记失败'
  }
}

async function runAction(action: string, row: Row) {
  errorMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/${row.id}/actions`, {
      method: 'POST',
      body: JSON.stringify({ values: { action } }),
    })
    if (!response.ok) {
      throw new Error('数据审计动作未生效，请稍后重试')
    }
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '数据审计操作失败'
  }
}

async function reload() {
  errorMessage.value = ''
  const query = new URLSearchParams(filters.value as Record<string, string>).toString()
  try {
    const response = await request(`${ENDPOINT}?${query}`)
    if (!response.ok) {
      throw new Error('审计记录列表读取失败')
    }
    const payload = await response.json()
    rows.value = payload.items ?? []
    total.value = payload.total ?? rows.value.length
    updateStats()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '数据审计列表读取失败'
  }
}

function openTrace() {
  traceErrorMessage.value = ''
  traceReport.value = null
  activeTraceId.value = null
  traceFieldsValue.value = {}
  showTraceModal.value = true
  void loadTraceList()
}

function closeTrace() {
  showTraceModal.value = false
}

async function submitTrace() {
  traceErrorMessage.value = ''
  try {
    const response = await request(TRACE_ENDPOINT, {
      method: 'POST',
      body: JSON.stringify({ values: { ...traceFieldsValue.value } }),
    })
    const data = await response.json()
    if (!response.ok || !data.ok) {
      throw new Error(data.message || '追查发起失败')
    }
    traceReport.value = data.entry.核对报告 as TraceReport
    activeTraceId.value = data.entry.追查任务.id as number
    await loadTraceList()
  } catch (error) {
    traceReport.value = null
    traceErrorMessage.value = error instanceof Error ? error.message : '回执差错追查失败'
  }
}

async function loadTraceList() {
  try {
    const response = await request(TRACE_ENDPOINT)
    if (!response.ok) {
      throw new Error('历史追查读取失败')
    }
    const payload = await response.json()
    traceList.value = (payload.items ?? []) as TraceTask[]
  } catch (error) {
    traceErrorMessage.value = error instanceof Error ? error.message : '历史追查读取失败'
  }
}

async function openTraceDetail(traceId: number) {
  traceErrorMessage.value = ''
  try {
    const response = await request(`${TRACE_ENDPOINT}/${traceId}`)
    if (!response.ok) {
      throw new Error('追查报告读取失败')
    }
    traceReport.value = (await response.json()) as TraceReport
    activeTraceId.value = traceId
  } catch (error) {
    traceErrorMessage.value = error instanceof Error ? error.message : '追查报告读取失败'
  }
}

async function writeOff(receiptNo: string) {
  if (activeTraceId.value === null) {
    return
  }
  traceErrorMessage.value = ''
  try {
    const response = await request(`${TRACE_ENDPOINT}/${activeTraceId.value}/write-off`, {
      method: 'POST',
      body: JSON.stringify({ values: { 回执编号: receiptNo }, remark: '终态矛盾回执核销，证据留存' }),
    })
    const data = await response.json()
    if (!response.ok || !data.ok) {
      throw new Error(data.message || '核销失败')
    }
    await openTraceDetail(activeTraceId.value)
    await loadTraceList()
  } catch (error) {
    traceErrorMessage.value = error instanceof Error ? error.message : '核销失败'
  }
}

onMounted(reload)
</script>
