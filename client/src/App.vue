<script setup>
import { computed, nextTick, onMounted, reactive, ref, watch } from "vue";
import { clearToken, getToken, request, setToken } from "./api";

const route = ref(window.location.pathname);
const isAdminPage = computed(() => route.value.startsWith("/admin"));

const codes = ref("");
const redeeming = ref(false);
const redeemError = ref("");
const redeemResults = ref([]);
const resultListRef = ref(null);
const successfulResults = computed(() =>
  redeemResults.value.filter((item) => item.content || item.files?.length)
);
const copyNotice = ref("");
let copyNoticeTimer = null;

const adminToken = ref(getToken());
const password = ref("");
const adminMessage = ref("");
const adminLoading = ref(false);
const showSettings = ref(false);
const updateStatus = ref(null);
const updateLoading = ref(false);
const updateMessage = ref("");

const admin = reactive({
  summary: {
    products: 0,
    cards: 0,
    unused_cards: 0,
    used_cards: 0,
    stock: 0
  },
  products: [],
  cards: []
});

const productForm = reactive({
  name: "",
  description: ""
});

const stockForm = reactive({
  productId: "",
  items: ""
});
const stockFiles = ref([]);
const stockFileInput = ref(null);

const cardForm = reactive({
  productId: "",
  prefix: "CARD",
  count: 10,
  itemCount: 1,
  length: 16
});

const passwordForm = reactive({
  currentPassword: "",
  newPassword: "",
  confirmPassword: ""
});

const generatedCodes = ref("");
const selectedProductId = ref("");
const productDetail = ref(null);
const productLoading = ref(false);
const selectedInventoryIds = ref([]);

const availableItems = computed(() =>
  (productDetail.value?.inventory || []).filter((item) => item.status === "available")
);
const deliveredItems = computed(() =>
  (productDetail.value?.inventory || []).filter((item) => item.status === "delivered")
);
const allInventoryItems = computed(() => productDetail.value?.inventory || []);
const allInventorySelected = computed(
  () =>
    allInventoryItems.value.length > 0 &&
    selectedInventoryIds.value.length === allInventoryItems.value.length
);

function syncRoute() {
  route.value = window.location.pathname;
}

function go(path) {
  window.history.pushState({}, "", path);
  syncRoute();
}

async function openSettings() {
  showSettings.value = true;
  await checkUpdate();
}

function setDefaultProducts() {
  const first = admin.products[0];
  if (!first) return;
  if (!stockForm.productId) stockForm.productId = String(first.id);
  if (!cardForm.productId) cardForm.productId = String(first.id);
}

function statusText(status) {
  return status === "delivered" ? "已兑换" : "未发货";
}

function cardStatusText(card) {
  return card.used_at ? "已使用" : "未使用";
}

async function redeem() {
  redeemError.value = "";
  redeemResults.value = [];

  if (!codes.value.trim()) {
    redeemError.value = "请输入卡密";
    return;
  }

  redeeming.value = true;
  try {
    const data = await request("/api/redeem", {
      method: "POST",
      body: JSON.stringify({ codes: codes.value })
    });
    redeemResults.value = data.results || [];
    if (redeemResults.value.length) {
      await nextTick();
      resultListRef.value?.scrollIntoView({ behavior: "smooth", block: "start" });
    }
  } catch (error) {
    redeemError.value = error.message;
  } finally {
    redeeming.value = false;
  }
}

async function copyText(text, message = "复制成功") {
  if (!text) return;
  await navigator.clipboard.writeText(text);
  copyNotice.value = message;
  window.clearTimeout(copyNoticeTimer);
  copyNoticeTimer = window.setTimeout(() => {
    copyNotice.value = "";
  }, 1800);
}

function safeFilename(text) {
  return String(text || "card")
    .replace(/[\\/:*?"<>|]/g, "_")
    .slice(0, 80);
}

function downloadText(filename, text) {
  const blob = new Blob([text], { type: "text/plain;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}

function base64ToBlob(base64, mimeType) {
  const binary = atob(base64);
  const bytes = new Uint8Array(binary.length);
  for (let index = 0; index < binary.length; index += 1) {
    bytes[index] = binary.charCodeAt(index);
  }
  return new Blob([bytes], { type: mimeType || "application/octet-stream" });
}

function downloadBlob(filename, blob) {
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}

function downloadRedeemFile(file) {
  downloadBlob(file.filename || "download", base64ToBlob(file.base64, file.mimeType));
}

function downloadAllRedeemFiles(item) {
  (item.files || []).forEach((file, index) => {
    window.setTimeout(() => downloadRedeemFile(file), index * 150);
  });
}

function downloadRedeemResult(item) {
  downloadText(`${safeFilename(item.code)}.txt`, item.content || "");
}

function downloadAllRedeemResults() {
  const text = successfulResults.value
    .map((item) => {
      const files = (item.files || []).map((file) => `文件：${file.filename}`).join("\n");
      return `卡密：${item.code}\n商品：${item.productName || "-"}\n\n${item.content || files}`;
    })
    .join("\n\n==============================\n\n");
  downloadText(`card-results-${new Date().toISOString().slice(0, 10)}.txt`, text);
}

async function login() {
  adminMessage.value = "";
  if (!password.value.trim()) {
    adminMessage.value = "请输入后台密码";
    return;
  }

  adminLoading.value = true;
  try {
    const data = await request("/api/admin/login", {
      method: "POST",
      body: JSON.stringify({ password: password.value })
    });
    setToken(data.token);
    adminToken.value = data.token;
    password.value = "";
    await loadAdmin();
  } catch (error) {
    adminMessage.value = error.message;
  } finally {
    adminLoading.value = false;
  }
}

function logout() {
  clearToken();
  adminToken.value = "";
  admin.products = [];
  admin.cards = [];
  productDetail.value = null;
  selectedProductId.value = "";
  selectedInventoryIds.value = [];
  showSettings.value = false;
  updateStatus.value = null;
}

async function loadAdmin(options = {}) {
  if (!adminToken.value) return;

  const clearMessage = options.clearMessage !== false;
  adminLoading.value = true;
  if (clearMessage) {
    adminMessage.value = "";
  }
  try {
    const [summaryData, productsData, cardsData] = await Promise.all([
      request("/api/admin/summary"),
      request("/api/admin/products"),
      request("/api/admin/cards?limit=120")
    ]);
    admin.summary = summaryData.summary;
    admin.products = productsData.products || [];
    admin.cards = cardsData.cards || [];
    setDefaultProducts();
    if (selectedProductId.value) {
      const exists = admin.products.some((product) => String(product.id) === selectedProductId.value);
      if (exists) {
        await loadProductDetail(selectedProductId.value, false);
      } else {
        selectedProductId.value = "";
        productDetail.value = null;
      }
    }
  } catch (error) {
    adminMessage.value = error.message;
    if (error.message.includes("登录")) {
      logout();
    }
  } finally {
    adminLoading.value = false;
  }
}

async function refreshAdmin() {
  generatedCodes.value = "";
  selectedInventoryIds.value = [];
  await loadAdmin();
}

async function createProduct() {
  adminMessage.value = "";
  if (!productForm.name.trim()) {
    adminMessage.value = "请输入商品名称";
    return;
  }

  const data = await request("/api/admin/products", {
    method: "POST",
    body: JSON.stringify(productForm)
  });
  productForm.name = "";
  productForm.description = "";
  await loadAdmin({ clearMessage: false });
  await openProduct(data.id);
}

async function addStock() {
  adminMessage.value = "";
  if (!stockForm.productId) {
    adminMessage.value = "请先创建并选择商品";
    return;
  }
  if (!stockForm.items.trim()) {
    adminMessage.value = "请填写库存内容";
    return;
  }

  const data = await request(`/api/admin/products/${stockForm.productId}/stock`, {
    method: "POST",
    body: JSON.stringify({ items: stockForm.items })
  });
  stockForm.items = "";
  await loadAdmin({ clearMessage: false });
  adminMessage.value = `已上架 ${data.count} 条库存`;
  await openProduct(stockForm.productId);
}

function onStockFilesChange(event) {
  stockFiles.value = Array.from(event.target.files || []);
}

async function uploadStockFiles() {
  adminMessage.value = "";
  if (!stockForm.productId) {
    adminMessage.value = "请先创建并选择商品";
    return;
  }
  if (!stockFiles.value.length) {
    adminMessage.value = "请选择要上传的文件";
    return;
  }

  const formData = new FormData();
  stockFiles.value.forEach((file) => {
    formData.append("files", file, file.name);
  });

  const data = await request(`/api/admin/products/${stockForm.productId}/files`, {
    method: "POST",
    body: formData
  });
  stockFiles.value = [];
  if (stockFileInput.value) {
    stockFileInput.value.value = "";
  }
  await loadAdmin({ clearMessage: false });
  adminMessage.value = `已上传 ${data.count} 个文件库存`;
  await openProduct(stockForm.productId);
}

async function generateCards() {
  adminMessage.value = "";
  generatedCodes.value = "";
  if (!cardForm.productId) {
    adminMessage.value = "请先创建并选择商品";
    return;
  }

  const data = await request("/api/admin/cards/generate", {
    method: "POST",
    body: JSON.stringify({
      productId: Number(cardForm.productId),
      prefix: cardForm.prefix,
      count: Number(cardForm.count),
      itemCount: Number(cardForm.itemCount),
      length: Number(cardForm.length)
    })
  });
  generatedCodes.value = (data.codes || []).join("\n");
  await loadAdmin({ clearMessage: false });
  adminMessage.value = `已生成 ${data.codes.length} 张卡密，每张兑换 ${cardForm.itemCount} 条商品`;
  await openProduct(cardForm.productId);
}

async function loadProductDetail(productId, showLoading = true) {
  if (!productId) return;
  if (showLoading) productLoading.value = true;
  try {
    const data = await request(`/api/admin/products/${productId}`);
    productDetail.value = {
      product: data.product,
      inventory: data.inventory || [],
      cards: data.cards || []
    };
    selectedProductId.value = String(productId);
    const existingIds = new Set(productDetail.value.inventory.map((item) => item.id));
    selectedInventoryIds.value = selectedInventoryIds.value.filter((id) => existingIds.has(id));
  } catch (error) {
    adminMessage.value = error.message;
  } finally {
    productLoading.value = false;
  }
}

async function openProduct(productId) {
  selectedProductId.value = String(productId);
  await loadProductDetail(productId);
}

async function deleteProduct(product) {
  const confirmed = window.confirm(
    `确定删除商品「${product.name}」吗？它下面的库存、已兑换记录和卡密都会一起删除。`
  );
  if (!confirmed) return;

  await request(`/api/admin/products/${product.id}`, { method: "DELETE" });
  adminMessage.value = `已删除商品：${product.name}`;
  if (selectedProductId.value === String(product.id)) {
    selectedProductId.value = "";
    productDetail.value = null;
  }
  await loadAdmin({ clearMessage: false });
  adminMessage.value = `已删除商品：${product.name}`;
}

async function deleteInventoryItem(item) {
  const label = item.status === "delivered" ? "已兑换内容" : "未发库存";
  const confirmed = window.confirm(`确定删除这条${label}吗？删除后无法从后台恢复。`);
  if (!confirmed) return;

  await request(`/api/admin/inventory/${item.id}`, { method: "DELETE" });
  await loadAdmin({ clearMessage: false });
  adminMessage.value = `已删除一条${label}`;
  if (selectedProductId.value) {
    await loadProductDetail(selectedProductId.value, false);
  }
}

function toggleSelectAllInventory() {
  if (allInventorySelected.value) {
    selectedInventoryIds.value = [];
    return;
  }
  selectedInventoryIds.value = allInventoryItems.value.map((item) => item.id);
}

async function deleteSelectedInventoryItems() {
  if (!selectedInventoryIds.value.length) {
    adminMessage.value = "请先勾选要删除的商品";
    return;
  }

  const confirmed = window.confirm(
    `确定删除选中的 ${selectedInventoryIds.value.length} 条商品吗？未发和已兑换都会直接删除。`
  );
  if (!confirmed) return;

  const data = await request("/api/admin/inventory/bulk-delete", {
    method: "POST",
    body: JSON.stringify({ ids: selectedInventoryIds.value })
  });
  selectedInventoryIds.value = [];
  await loadAdmin({ clearMessage: false });
  adminMessage.value = `已批量删除 ${data.count} 条商品`;
  if (selectedProductId.value) {
    await loadProductDetail(selectedProductId.value, false);
  }
}

async function restoreInventoryItem(item) {
  const confirmed = window.confirm("确定恢复这条已兑换商品吗？恢复后它会回到未发库存。");
  if (!confirmed) return;

  await request(`/api/admin/inventory/${item.id}/restore`, { method: "POST" });
  await loadAdmin({ clearMessage: false });
  adminMessage.value = "已恢复一条已兑换商品";
  if (selectedProductId.value) {
    await loadProductDetail(selectedProductId.value, false);
  }
}

async function restoreCard(card) {
  const confirmed = window.confirm(
    `确定恢复卡密「${card.code}」吗？它发出的商品会回到未发库存，这张卡会变回未使用。`
  );
  if (!confirmed) return;

  const data = await request(`/api/admin/cards/${card.id}/restore`, { method: "POST" });
  await loadAdmin({ clearMessage: false });
  adminMessage.value = `已恢复卡密：${card.code}`;
  await loadProductDetail(data.productId, false);
}

async function deleteCard(card) {
  const suffix = card.used_at
    ? "它发出的商品记录也会一起删除，不会回到库存。"
    : "删除后这张卡密不能再使用。";
  const confirmed = window.confirm(`确定删除卡密「${card.code}」吗？${suffix}`);
  if (!confirmed) return;

  const data = await request(`/api/admin/cards/${card.id}`, { method: "DELETE" });
  await loadAdmin({ clearMessage: false });
  adminMessage.value = `已删除卡密：${card.code}`;
  if (selectedProductId.value === String(data.productId)) {
    await loadProductDetail(data.productId, false);
  }
}

async function changePassword() {
  adminMessage.value = "";
  if (!passwordForm.currentPassword || !passwordForm.newPassword) {
    adminMessage.value = "请填写当前密码和新密码";
    return;
  }
  if (passwordForm.newPassword !== passwordForm.confirmPassword) {
    adminMessage.value = "两次输入的新密码不一致";
    return;
  }

  await request("/api/admin/password", {
    method: "POST",
    body: JSON.stringify({
      currentPassword: passwordForm.currentPassword,
      newPassword: passwordForm.newPassword
    })
  });

  passwordForm.currentPassword = "";
  passwordForm.newPassword = "";
  passwordForm.confirmPassword = "";
  showSettings.value = false;
  adminMessage.value = "后台密码已修改，下次登录请使用新密码";
}

async function checkUpdate() {
  updateLoading.value = true;
  updateMessage.value = "";
  try {
    const data = await request("/api/admin/update/status");
    updateStatus.value = data.status;
    if (data.status.error) {
      updateMessage.value = `检测失败：${data.status.error}`;
    } else if (data.status.updateAvailable) {
      updateMessage.value = "检测到新版本，可以一键更新。";
    } else {
      updateMessage.value = "当前已是最新版本。";
    }
  } catch (error) {
    updateMessage.value = error.message;
  } finally {
    updateLoading.value = false;
  }
}

async function runUpdate() {
  const confirmed = window.confirm(
    "确定开始更新吗？更新时会重新构建前端并重启服务，页面可能短暂断开。"
  );
  if (!confirmed) return;

  updateLoading.value = true;
  updateMessage.value = "";
  try {
    const data = await request("/api/admin/update/run", { method: "POST" });
    updateMessage.value = data.message || "更新任务已开始，稍后服务会自动重启。";
  } catch (error) {
    updateMessage.value = error.message;
  } finally {
    updateLoading.value = false;
  }
}

watch(isAdminPage, (value) => {
  if (value && adminToken.value) {
    loadAdmin();
  }
});

onMounted(() => {
  window.addEventListener("popstate", syncRoute);
  if (isAdminPage.value && adminToken.value) {
    loadAdmin();
  }
});
</script>

<template>
  <main v-if="!isAdminPage" class="redeem-shell">
    <div v-if="copyNotice" class="copy-toast">{{ copyNotice }}</div>
    <section class="redeem-card">
      <p class="eyebrow">文件池卡密平台</p>
      <h1>卡密提取</h1>
      <p class="subtitle">输入一张或多张卡密提取文件</p>

      <div class="notice">提取后的内容请及时保存；卡密成功兑换后会自动标记为已使用。</div>

      <form class="redeem-form" @submit.prevent="redeem">
        <label for="codes">卡密</label>
        <textarea
          id="codes"
          v-model="codes"
          placeholder="例如：CARD-20260526-ABCDEFGH12345678"
          rows="6"
        />
        <button class="primary-button" type="submit" :disabled="redeeming">
          {{ redeeming ? "提取中..." : "提取卡密" }}
        </button>
      </form>

      <p v-if="redeemError" class="error-text">{{ redeemError }}</p>

      <section v-if="redeemResults.length" ref="resultListRef" class="result-list">
        <div v-if="successfulResults.length" class="result-toolbar">
          <span>已提取 {{ successfulResults.length }} 张卡密</span>
          <button type="button" @click="downloadAllRedeemResults">下载全部 TXT</button>
        </div>
        <article
          v-for="item in redeemResults"
          :key="item.code"
          class="result-item"
          :class="item.status"
        >
          <div class="result-head">
            <strong>{{ item.code }}</strong>
            <span>{{ item.message }}</span>
          </div>
          <p v-if="item.productName" class="product-name">{{ item.productName }}</p>
          <div v-if="item.content" class="result-actions">
            <button type="button" @click="copyText(item.content, '内容已复制')">复制内容</button>
            <button type="button" @click="downloadRedeemResult(item)">下载 TXT</button>
          </div>
          <div v-if="item.files?.length" class="result-actions file-actions">
            <button
              v-for="file in item.files"
              :key="file.filename"
              type="button"
              @click="downloadRedeemFile(file)"
            >
              下载 {{ file.filename }}
            </button>
            <button v-if="item.files.length > 1" type="button" @click="downloadAllRedeemFiles(item)">
              下载全部文件
            </button>
          </div>
          <pre v-if="item.content">{{ item.content }}</pre>
          <p v-if="!item.content && item.files?.length" class="file-note">
            该卡密提取到 {{ item.files.length }} 个文件，请点击上方按钮下载。
          </p>
        </article>
      </section>
    </section>
  </main>

  <main v-else class="admin-shell">
    <div v-if="copyNotice" class="copy-toast">{{ copyNotice }}</div>
    <section v-if="!adminToken" class="login-card">
      <button class="ghost-link" type="button" @click="go('/')">返回前台</button>
      <p class="eyebrow">Admin Console</p>
      <h1>后台面板</h1>
      <p class="subtitle">管理商品、库存和卡密生成。</p>
      <form class="login-form" @submit.prevent="login">
        <input v-model="password" type="password" placeholder="后台密码" autocomplete="current-password" />
        <button class="primary-button" type="submit" :disabled="adminLoading">
          {{ adminLoading ? "登录中..." : "登录后台" }}
        </button>
      </form>
      <p v-if="adminMessage" class="error-text">{{ adminMessage }}</p>
    </section>

    <section v-else class="admin-board">
      <header class="admin-header">
        <div>
          <p class="eyebrow">Card Redeem</p>
          <h1>后台面板</h1>
          <p class="subtitle">手动上货、生成卡密、查看兑换状态。</p>
        </div>
        <div class="header-actions">
          <button type="button" class="secondary-button" @click="go('/')">前台页面</button>
          <button type="button" class="secondary-button" @click="refreshAdmin">刷新</button>
          <button type="button" class="secondary-button" @click="openSettings">设置</button>
          <button type="button" class="danger-button" @click="logout">退出</button>
        </div>
      </header>

      <p v-if="adminMessage" class="admin-message">{{ adminMessage }}</p>

      <section class="stats-grid">
        <article>
          <span>商品</span>
          <strong>{{ admin.summary.products }}</strong>
        </article>
        <article>
          <span>总卡密</span>
          <strong>{{ admin.summary.cards }}</strong>
        </article>
        <article>
          <span>未使用</span>
          <strong>{{ admin.summary.unused_cards }}</strong>
        </article>
        <article>
          <span>可发库存</span>
          <strong>{{ admin.summary.stock }}</strong>
        </article>
      </section>

      <section class="panel-grid">
        <form class="panel" @submit.prevent="createProduct">
          <h2>创建商品</h2>
          <input v-model="productForm.name" placeholder="商品名称，例如：网盘文件 A" />
          <textarea v-model="productForm.description" rows="4" placeholder="商品说明，可选" />
          <button class="primary-button" type="submit">保存商品</button>
        </form>

        <form class="panel" @submit.prevent="addStock">
          <h2>手动上货</h2>
          <select v-model="stockForm.productId">
            <option value="">请选择商品</option>
            <option v-for="product in admin.products" :key="product.id" :value="String(product.id)">
              {{ product.name }}（库存 {{ product.stock }}）
            </option>
          </select>
          <textarea
            v-model="stockForm.items"
            rows="7"
            placeholder="一行一个发货内容，可以是下载链接、账号密码、兑换文本等"
          />
          <button class="primary-button" type="submit">上架库存</button>
          <div class="upload-box">
            <label class="field-label">
              上传文件库存
              <input ref="stockFileInput" type="file" multiple @change="onStockFilesChange" />
              <span>支持 JSON、ZIP、图片、文档等任意格式；每个文件会作为一条库存。</span>
            </label>
            <button class="secondary-button" type="button" @click="uploadStockFiles">
              上传选中文件
            </button>
          </div>
        </form>

        <form class="panel" @submit.prevent="generateCards">
          <h2>生成卡密</h2>
          <label class="field-label">
            绑定商品
            <select v-model="cardForm.productId">
              <option value="">请选择商品</option>
              <option v-for="product in admin.products" :key="product.id" :value="String(product.id)">
                {{ product.name }}
              </option>
            </select>
          </label>
          <p class="help-text">
            卡密格式是：卡密开头 + 今天日期 + 随机码。比如生成张数填 10、每张发货数量填 100，
            就是生成 10 张卡，每张卡兑换后会一次发出 100 条该商品库存。
          </p>
          <div class="inline-fields labeled-fields">
            <label class="field-label">
              卡密开头
              <input v-model="cardForm.prefix" placeholder="例如 CARD / OUTLOOK" />
              <span>方便区分来源或商品，可随便填英文/数字。</span>
            </label>
            <label class="field-label">
              生成张数
              <input v-model.number="cardForm.count" min="1" max="1000" type="number" placeholder="例如 100" />
              <span>一次生成多少张卡密。</span>
            </label>
            <label class="field-label">
              每张发货数量
              <input v-model.number="cardForm.itemCount" min="1" max="1000" type="number" placeholder="例如 1 / 100" />
              <span>一张卡兑换后发几条库存。</span>
            </label>
            <label class="field-label">
              随机码长度
              <input v-model.number="cardForm.length" min="8" max="32" type="number" placeholder="例如 16" />
              <span>越长越难猜，建议 16。</span>
            </label>
          </div>
          <button class="primary-button" type="submit">生成卡密</button>
          <textarea v-if="generatedCodes" v-model="generatedCodes" rows="8" readonly />
          <button
            v-if="generatedCodes"
            class="secondary-button"
            type="button"
            @click="copyText(generatedCodes, '卡密已复制')"
          >
            复制全部卡密
          </button>
        </form>

      </section>

      <section class="tables-grid">
        <article class="table-panel product-table-panel">
          <h2>商品列表</h2>
          <div class="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>ID</th>
                  <th>商品信息</th>
                  <th>库存</th>
                  <th>已兑换</th>
                  <th>卡密</th>
                  <th>操作</th>
                </tr>
              </thead>
              <tbody>
                <tr
                  v-for="product in admin.products"
                  :key="product.id"
                  :class="{ selected: selectedProductId === String(product.id) }"
                >
                  <td>{{ product.id }}</td>
                  <td>
                    <strong>{{ product.name }}</strong>
                    <small>{{ product.description || "暂无说明" }}</small>
                  </td>
                  <td>{{ product.stock }} / {{ product.total_stock }}</td>
                  <td>{{ product.delivered_stock }}</td>
                  <td>{{ product.unused_cards }} 未用 / {{ product.used_cards }} 已用</td>
                  <td class="row-actions">
                    <button type="button" class="mini-button" @click="openProduct(product.id)">管理</button>
                    <button type="button" class="mini-button danger" @click="deleteProduct(product)">删除</button>
                  </td>
                </tr>
              </tbody>
            </table>
          </div>
        </article>

        <article class="table-panel">
          <h2>最近卡密</h2>
          <div class="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>卡密</th>
                  <th>商品</th>
                  <th>每张发货</th>
                  <th>状态</th>
                  <th>操作</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="card in admin.cards" :key="card.id">
                  <td class="mono">{{ card.code }}</td>
                  <td>{{ card.product_name }}</td>
                  <td>{{ card.item_count }} 条</td>
                  <td>
                    <span class="status-pill" :class="{ used: card.used_at }">
                      {{ cardStatusText(card) }}
                    </span>
                  </td>
                  <td class="row-actions">
                    <button
                      v-if="card.used_at"
                      type="button"
                      class="mini-button"
                      @click="restoreCard(card)"
                    >
                      恢复
                    </button>
                    <button type="button" class="mini-button danger" @click="deleteCard(card)">删除</button>
                  </td>
                </tr>
              </tbody>
            </table>
          </div>
        </article>
      </section>

      <section class="detail-panel">
        <div class="detail-header">
          <div>
            <h2>单个商品管理</h2>
            <p v-if="!productDetail">在商品列表点击“管理”，这里会显示该商品的库存、已兑换内容和卡密。</p>
            <p v-else>
              {{ productDetail.product.name }}：
              可发 {{ productDetail.product.stock }} 条，已兑换 {{ productDetail.product.delivered_stock }} 条，
              卡密 {{ productDetail.product.cards }} 张。
            </p>
          </div>
          <button
            v-if="selectedProductId"
            type="button"
            class="secondary-button"
            :disabled="productLoading"
            @click="loadProductDetail(selectedProductId)"
          >
            {{ productLoading ? "刷新中..." : "刷新商品" }}
          </button>
        </div>

        <template v-if="productDetail">
          <div class="selection-toolbar">
            <label class="check-row">
              <input
                type="checkbox"
                :checked="allInventorySelected"
                @change="toggleSelectAllInventory"
              />
              全选当前商品
            </label>
            <span>已选 {{ selectedInventoryIds.length }} 条</span>
            <button type="button" class="mini-button danger" @click="deleteSelectedInventoryItems">
              删除选中
            </button>
          </div>

          <section class="detail-columns">
            <article>
              <h3>未发库存</h3>
              <div v-if="!availableItems.length" class="empty-state">这个商品暂无可发库存。</div>
              <div v-for="item in availableItems" :key="item.id" class="stock-card">
                <div class="stock-meta">
                  <label class="check-row">
                    <input v-model="selectedInventoryIds" type="checkbox" :value="item.id" />
                    <span class="status-pill">{{ statusText(item.status) }}</span>
                  </label>
                  <button type="button" class="mini-button danger" @click="deleteInventoryItem(item)">删除</button>
                </div>
                <pre>{{ item.item_type === "file" ? `文件：${item.filename}` : item.content }}</pre>
              </div>
            </article>

            <article>
              <h3>已兑换商品</h3>
              <div v-if="!deliveredItems.length" class="empty-state">还没有用户兑换这个商品。</div>
              <div v-for="item in deliveredItems" :key="item.id" class="stock-card delivered">
                <div class="stock-meta">
                  <label class="check-row">
                    <input v-model="selectedInventoryIds" type="checkbox" :value="item.id" />
                    <span class="status-pill used">{{ statusText(item.status) }}</span>
                  </label>
                  <div class="row-actions">
                    <button type="button" class="mini-button" @click="restoreInventoryItem(item)">恢复</button>
                    <button type="button" class="mini-button danger" @click="deleteInventoryItem(item)">删除</button>
                  </div>
                </div>
                <p class="linked-code">兑换卡密：{{ item.card_code || "已删除卡密" }}</p>
                <pre>{{ item.item_type === "file" ? `文件：${item.filename}` : item.content }}</pre>
              </div>
            </article>
          </section>

          <article class="table-panel full-width-panel">
            <h3>该商品的卡密</h3>
            <div class="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>卡密</th>
                    <th>每张发货</th>
                    <th>状态</th>
                    <th>兑换内容</th>
                    <th>操作</th>
                  </tr>
                </thead>
                <tbody>
                  <tr v-for="card in productDetail.cards" :key="card.id">
                    <td class="mono">{{ card.code }}</td>
                    <td>{{ card.item_count }} 条</td>
                    <td>
                      <span class="status-pill" :class="{ used: card.used_at }">
                        {{ cardStatusText(card) }}
                      </span>
                    </td>
                    <td class="content-cell">{{ card.delivered_content || "-" }}</td>
                    <td class="row-actions">
                      <button
                        v-if="card.used_at"
                        type="button"
                        class="mini-button"
                        @click="restoreCard(card)"
                      >
                        恢复
                      </button>
                      <button type="button" class="mini-button danger" @click="deleteCard(card)">删除</button>
                    </td>
                  </tr>
                </tbody>
              </table>
            </div>
          </article>
        </template>
      </section>

      <div v-if="showSettings" class="modal-mask" @click.self="showSettings = false">
        <form class="settings-modal" @submit.prevent="changePassword">
          <div class="modal-head">
            <div>
              <h2>设置</h2>
              <p>修改后台登录密码。</p>
            </div>
            <button type="button" class="mini-button" @click="showSettings = false">关闭</button>
          </div>
          <label class="field-label">
            当前密码
            <input v-model="passwordForm.currentPassword" type="password" autocomplete="current-password" />
          </label>
          <label class="field-label">
            新密码
            <input v-model="passwordForm.newPassword" type="password" autocomplete="new-password" />
            <span>至少 8 位，建议包含字母和数字。</span>
          </label>
          <label class="field-label">
            再输一次新密码
            <input v-model="passwordForm.confirmPassword" type="password" autocomplete="new-password" />
          </label>
          <button class="primary-button" type="submit">保存新密码</button>

          <section class="update-card">
            <div>
              <h3>系统更新</h3>
              <p>检测 GitHub 仓库是否有新版本，部署环境可直接一键更新。</p>
              <p class="help-text">更新只替换程序代码并重启服务，不会删除数据库、库存、卡密、已兑换记录和后台配置。</p>
            </div>

            <dl v-if="updateStatus" class="version-list">
              <div>
                <dt>当前版本</dt>
                <dd>{{ updateStatus.currentShort || "未知" }}</dd>
              </div>
              <div>
                <dt>远程版本</dt>
                <dd>{{ updateStatus.latestShort || "未知" }}</dd>
              </div>
              <div>
                <dt>仓库</dt>
                <dd>{{ updateStatus.repo }} / {{ updateStatus.branch }}</dd>
              </div>
            </dl>

            <p v-if="updateMessage" class="update-message">{{ updateMessage }}</p>

            <div class="row-actions">
              <button type="button" class="mini-button" :disabled="updateLoading" @click="checkUpdate">
                {{ updateLoading ? "检测中..." : "检测更新" }}
              </button>
              <button
                type="button"
                class="mini-button danger"
                :disabled="updateLoading || !updateStatus?.supported || !updateStatus?.updateAvailable"
                @click="runUpdate"
              >
                一键更新
              </button>
            </div>
            <p v-if="updateStatus && !updateStatus.supported" class="help-text">
              当前环境不支持后台更新；Linux 一键部署后的服务器环境会自动支持。
            </p>
          </section>
        </form>
      </div>
    </section>
  </main>
</template>
