<template>
  <section class="page" data-module="pipeline">
    <header class="page-head">
      <div>
        <h2>分析管道管理</h2>
        <p class="page-desc">维护分析节点，围绕节点编号、输入数据集、分析算子、输出结果做登记、筛选与状态流转。</p>
      </div>
      <div class="page-actions">
        <button class="btn primary" type="button" @click="openCreate">登记分析节点</button>
        <button class="btn" type="button" @click="openReceiptReview">回执复跑清单</button>
        <button class="btn" type="button" @click="exportRows">导出分析管道清单</button>
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
          <td :colspan="columns.length + 1" class="empty-state">暂无分析管道数据，可先登记分析节点</td>
        </tr>
      </tbody>
    </table>

    <footer class="page-foot">
      <span>共 {{ total }} 条分析管道记录</span>
      <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
    </footer>

    <Teleport to="body">
      <div v-if="showCreateModal" class="modal-overlay" @click.self="showCreateModal = false">
        <div class="modal-card">
          <header class="modal-head">
            <h3>登记分析节点</h3>
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

    <!-- 可疑产物索引与待复跑清单：按实际产物引用生成，指纹去重 -->
    <Teleport to="body">
      <div v-if="showReceiptModal" class="modal-overlay" @click.self="showReceiptModal = false">
        <div class="modal-card modal-card-wide">
          <header class="modal-head">
            <h3>回执复跑清单与可疑产物索引</h3>
            <button class="btn ghost" type="button" @click="showReceiptModal = false">&times;</button>
          </header>
          <div class="modal-body">
            <div v-if="receiptError" class="error-text">{{ receiptError }}</div>

            <div class="trace-cols">
              <div>
                <strong>待复跑节点（{{ rerunPlan.待复跑节点.length }}）</strong>
                <ul class="trace-list">
                  <li v-for="node in rerunPlan.待复跑节点" :key="node.节点编号">
                    {{ node.节点编号 }}
                    <span v-for="code in node.差错类型" :key="code" class="tag tag-warn">{{ issueLabel(code) }}</span>
                    <br />涉及尝试：{{ node.涉及尝试编号.join('、') || '—' }}
                  </li>
                  <li v-if="!rerunPlan.待复跑节点.length">无</li>
                </ul>
              </div>
              <div>
                <strong>未受影响节点</strong>
                <p>{{ rerunPlan.未受影响节点.length ? rerunPlan.未受影响节点.join('、') : '无' }}</p>
              </div>
            </div>

            <div class="trace-panel">
              <h4>受影响结果清单</h4>
              <table class="data-table trace-table">
                <thead>
                  <tr>
                    <th>下游结果编号</th>
                    <th>引用节点</th>
                    <th>实际引用指纹</th>
                    <th>标记</th>
                  </tr>
                </thead>
                <tbody>
                  <tr v-for="item in rerunPlan.受影响结果清单" :key="item.下游结果编号">
                    <td>{{ item.下游结果编号 }}</td>
                    <td>{{ item.引用节点编号 }}</td>
                    <td>{{ item.引用产物指纹.join(' / ') }}</td>
                    <td>
                      <span v-if="item.指纹冲突" class="tag tag-warn">同结果指纹冲突</span>
                      <span v-if="item.产物缺失" class="tag tag-warn">产物缺失待补</span>
                      <span v-if="!item.指纹冲突 && !item.产物缺失" class="tag tag-ok">可追溯</span>
                    </td>
                  </tr>
                  <tr v-if="!rerunPlan.受影响结果清单.length">
                    <td colspan="4" class="empty-state">暂无受影响结果</td>
                  </tr>
                </tbody>
              </table>
            </div>

            <div class="trace-panel">
              <h4>可疑产物索引（同一指纹被多次尝试引用只计一次）</h4>
              <table class="data-table trace-table">
                <thead>
                  <tr>
                    <th>节点编号</th>
                    <th>数据产物指纹</th>
                    <th>引用尝试</th>
                    <th>下游结果</th>
                    <th>消费状态</th>
                  </tr>
                </thead>
                <tbody>
                  <tr v-for="product in suspiciousProducts" :key="`${product.节点编号}-${product.数据产物指纹}`">
                    <td>{{ product.节点编号 }}</td>
                    <td>{{ product.数据产物指纹 }}</td>
                    <td>{{ product.引用尝试编号.join('、') }}</td>
                    <td>{{ product.下游结果编号.join('、') || '—' }}</td>
                    <td>
                      <span :class="['tag', product.仍被下游消费 ? 'tag-warn' : 'tag-ok']">
                        {{ product.仍被下游消费 ? '仍被下游消费，纠错勿清理' : '无下游消费' }}
                      </span>
                    </td>
                  </tr>
                  <tr v-if="!suspiciousProducts.length">
                    <td colspan="5" class="empty-state">暂无可疑产物</td>
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
interface RerunNode {
  节点编号: string
  差错类型: string[]
  涉及尝试编号: string[]
  受影响结果编号: string[]
}
interface AffectedResult {
  下游结果编号: string
  引用节点编号: string
  引用产物指纹: string[]
  指纹冲突: boolean
  产物缺失: boolean
}
interface SuspiciousProduct {
  节点编号: string
  数据产物指纹: string
  引用尝试编号: string[]
  下游结果编号: string[]
  仍被下游消费: boolean
  可疑: boolean
}

const ENDPOINT = '/api/pipeline'
const columns = ["节点编号", "输入数据集", "分析算子", "输出结果", "执行顺序", "依赖节点", "失败策略", "节点状态"]
const actions = ["提交配置", "启动节点", "标记失败"]
const statuses = ["草稿", "已配置", "运行中", "已完成", "失败"]
const stats = [{"label": "运行中节点", "value": 0}, {"label": "已完成节点", "value": 0}, {"label": "失败节点", "value": 0}]

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

const showReceiptModal = ref(false)
const receiptError = ref('')
const rerunPlan = ref<{ 待复跑节点: RerunNode[]; 未受影响节点: string[]; 受影响结果清单: AffectedResult[] }>({
  待复跑节点: [],
  未受影响节点: [],
  受影响结果清单: [],
})
const suspiciousProducts = ref<SuspiciousProduct[]>([])

function issueLabel(code: string): string {
  return ISSUE_LABELS[code] ?? code
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
      throw new Error('分析管道动作未生效，请稍后重试')
    }
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '分析管道操作失败'
  }
}

async function reload() {
  errorMessage.value = ''
  const query = new URLSearchParams(filters.value as Record<string, string>).toString()
  try {
    const response = await request(`${ENDPOINT}?${query}`)
    if (!response.ok) {
      throw new Error('分析节点列表读取失败')
    }
    const payload = await response.json()
    rows.value = payload.items ?? []
    total.value = payload.total ?? rows.value.length
    updateStats()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '分析管道列表读取失败'
  }
}

async function openReceiptReview() {
  receiptError.value = ''
  showReceiptModal.value = true
  try {
    const [planResp, productResp] = await Promise.all([
      request(`${ENDPOINT}/receipts/rerun-plan`),
      request(`${ENDPOINT}/receipts/suspicious-products`),
    ])
    if (!planResp.ok || !productResp.ok) {
      throw new Error('回执核对数据读取失败')
    }
    rerunPlan.value = await planResp.json()
    const productPayload = await productResp.json()
    suspiciousProducts.value = productPayload.items ?? []
  } catch (error) {
    receiptError.value = error instanceof Error ? error.message : '回执核对数据读取失败'
  }
}

onMounted(reload)
</script>
