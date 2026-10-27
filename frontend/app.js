const { createApp } = Vue;

const WELCOME_EXAMPLES = [
  "紫金矿业靠什么赚钱？",
  "东鹏饮料毛利率为什么下滑？",
  "今天大盘怎么看？",
];
const STORAGE_KEY = "front_conversation";
const SPLIT_KEY = "front_split_conversation";
const RATIO_KEY = "front_split_ratio";
const MAX_SOURCE_ROWS = 6;
const MIN_RATIO = 22;
const MAX_RATIO = 78;

function escapeAttr(value) {
  return String(value ?? "")
    .replace(/&/g, "&amp;")
    .replace(/"/g, "&quot;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
}

function renderBody(text, sources, streaming) {
  const map = new Map();
  (sources || []).forEach((source) => {
    const id = String(source.id || "");
    if (id && !map.has(id)) map.set(id, { n: map.size + 1, source });
  });

  let src = String(text || "");
  if (streaming) {
    src = src.replace(/\[\[[^\]]*\]\]/g, "").replace(/\[\[[^\]]*$/, "");
  } else {
    src = src.replace(/\[\[([^\[\]]+)\]\]/g, (_, rawId) => {
      const hit = map.get(rawId.trim());
      const source = (hit && hit.source) || {};
      const number = hit ? hit.n : "?";
      const label = source.label || source.id || (hit ? "未知来源" : "来源未登记");
      const attrs = [
        `data-label="${escapeAttr(label)}"`,
        `data-detail="${escapeAttr(source.detail || "")}"`,
        `data-asof="${escapeAttr(source.as_of || "")}"`,
      ];
      if (source.url) attrs.push(`data-url="${escapeAttr(source.url)}"`);
      return `<sup class="cite" ${attrs.join(" ")}>[${number}]</sup>`;
    });
  }
  const html = marked.parse(src, { gfm: true, breaks: true });
  return DOMPurify.sanitize(html, {
    ADD_ATTR: ["data-label", "data-detail", "data-asof", "data-url"],
  });
}

function decorate(message) {
  return { ...message, html: renderBody(message.text || "", message.sources || [], false) };
}

// 工具耗时：一秒以内按毫秒，超过按秒；旧记录没有这个字段，按 0 显示
function formatDuration(value) {
  const ms = Math.max(0, Math.round(Number(value) || 0));
  return ms >= 1000 ? `${(ms / 1000).toFixed(1)}s` : `${ms}ms`;
}

function formatTokens(value) {
  const n = Number(value) || 0;
  if (n >= 10000) return `${(n / 10000).toFixed(1)}万`;
  if (n >= 1000) return `${(n / 1000).toFixed(1)}k`;
  return String(Math.round(n));
}

function shortDate(value) {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  const now = new Date();
  const hh = String(date.getHours()).padStart(2, "0");
  const mm = String(date.getMinutes()).padStart(2, "0");
  if (date.toDateString() === now.toDateString()) return `今天 ${hh}:${mm}`;
  const sameYear = date.getFullYear() === now.getFullYear();
  const md = `${date.getMonth() + 1}-${date.getDate()}`;
  return sameYear ? md : `${date.getFullYear()}-${md}`;
}

// ---------------------------------------------------------------------------
// 一个对话栏：会话、消息、流式回答、输入框全部自己持有。
// 两栏各自是独立的滚动容器和请求，互不等待，也互不撑高。
// ---------------------------------------------------------------------------
const ChatPane = {
  name: "ChatPane",
  template: "#pane-template",
  props: {
    secondary: { type: Boolean, default: false },
  },
  emits: ["state", "changed", "close"],
  data() {
    return {
      cid: null,
      activeName: "",
      activeCode: "",
      messages: [],
      examples: WELCOME_EXAMPLES,
      notice: "",
      input: "",
      busy: false,
      phase: "idle",
      streamText: "",
      streamEmpty: "正在思考…",
      statusLine: "",
      usage: null,
      copied: -1,
      tip: null,
    };
  },
  computed: {
    streamHtml() {
      return renderBody(this.streamText, [], true);
    },
    meterStyle() {
      const current = this.usage || {};
      const ratio = Math.max(0, Math.min(1, Number(current.ratio) || 0));
      const color = current.level === "over" ? "#e5655c" : current.level === "warn" ? "#e8b96b" : "#5b8cff";
      return { "--pct": String(ratio * 100), "--color": color };
    },
    meterText() {
      const percent = Number((this.usage || {}).percent) || 0;
      return percent >= 1000 ? "999+" : `${Math.round(percent)}%`;
    },
    meterTitle() {
      const current = this.usage || {};
      if (!current.tokens) return "上下文占用";
      return `上下文 ${formatTokens(current.tokens)} / ${formatTokens(current.budget_tokens)} tokens · ${Math.round(Number(current.percent) || 0)}%`;
    },
  },
  created() {
    this.noticeTimer = null;
    this.hideTimer = null;
    this.lastStreamScroll = 0;
    this.tipAnchor = null;
  },
  beforeUnmount() {
    if (this.noticeTimer) clearTimeout(this.noticeTimer);
    if (this.hideTimer) clearTimeout(this.hideTimer);
  },
  methods: {
    formatTokens,
    notify() {
      this.$emit("state", { id: this.cid, name: this.activeName, code: this.activeCode });
    },

    // ---------------- 会话 ----------------
    async open(id) {
      if (this.busy) return;
      if (!id) {
        this.reset();
        return;
      }
      if (this.cid === id) return;
      try {
        const response = await fetch(`/api/conversations/${id}`);
        if (!response.ok) {
          this.reset();
          return;
        }
        const data = await response.json();
        this.cid = data.id;
        this.activeName = data.name || "";
        this.activeCode = data.code || "";
        this.messages = (data.messages || []).map(decorate);
        this.usage = data.usage || null;
        this.phase = "idle";
        this.streamText = "";
        this.notice = "";
        this.copied = -1;
        this.hideTip();
        this.scroll(true);
        this.notify();
      } catch (error) {
        this.reset();
      }
    },
    reset() {
      this.cid = null;
      this.activeName = "";
      this.activeCode = "";
      this.messages = [];
      this.usage = null;
      this.phase = "idle";
      this.streamText = "";
      this.notice = "";
      this.copied = -1;
      this.hideTip();
      this.scroll(true);
      this.notify();
    },
    newChat() {
      this.reset();
      this.$nextTick(() => this.focusInput());
    },
    async deleteConversation() {
      const id = this.cid;
      if (id === null) return;
      try {
        await fetch(`/api/conversations/${id}/delete`, { method: "POST" });
      } catch (error) { /* 删不掉也先清页面 */ }
      this.reset();
      this.$emit("changed");
      if (this.secondary) this.$emit("close");
    },

    // ---------------- 提问与流式 ----------------
    async send() {
      const question = this.input.trim();
      if (!question || this.busy) return;

      const userMessage = { role: "user", text: question };
      this.messages.push(userMessage);
      this.input = "";
      this.busy = true;
      this.phase = "streaming";
      this.streamText = "";
      this.streamEmpty = "正在思考…";
      this.statusLine = "";
      this.notice = "";
      this.copied = -1;
      this.hideTip();
      if (this.$refs.inputEl) this.$refs.inputEl.style.height = "auto";
      this.scroll(true);

      try {
        const response = await fetch("/api/ask", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ conversation_id: this.cid, question }),
        });
        if (!response.ok || !response.body) throw new Error(`HTTP ${response.status}`);

        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        let buffer = "";

        while (true) {
          const chunk = await reader.read();
          if (chunk.done) break;
          buffer += decoder.decode(chunk.value, { stream: true });

          let split;
          while ((split = buffer.indexOf("\n\n")) >= 0) {
            const frame = buffer.slice(0, split);
            buffer = buffer.slice(split + 2);
            const line = frame.split("\n").find((item) => item.startsWith("data:"));
            if (!line) continue;
            let event;
            try {
              event = JSON.parse(line.slice(5).trim());
            } catch (error) {
              continue;
            }

            if (event.type === "state") {
              const previous = this.cid;
              if (event.conversation_id && event.conversation_id !== this.cid) {
                this.cid = event.conversation_id;
                // 换公司时服务端开了新会话：旧记录清掉，只留这一问
                if (previous !== null) this.messages = [userMessage];
                this.$emit("changed");
              }
              this.activeName = event.name || "";
              this.activeCode = event.code || "";
              this.notify();
              if (event.notice) this.setNotice(event.notice);
            } else if (event.type === "text") {
              this.streamText += event.delta || "";
              this.streamScroll();
            } else if (event.type === "discard") {
              this.streamText = "";
              this.streamEmpty = "正在取数…";
              this.statusLine = "";
            } else if (event.type === "tool") {
              const outcome = event.cached ? "复用缓存" : event.ok ? "成功" : "失败";
              this.statusLine = `已取数 · ${event.name || "工具"} · ${event.company || "--"} · ${outcome} · ${formatDuration(event.duration_ms)}`;
            } else if (event.type === "meter") {
              this.usage = event.usage || null;
            } else if (event.type === "warning") {
              this.messages.push({ role: "assistant", text: event.text, warning: true });
              this.phase = "idle";
            } else if (event.type === "error") {
              this.messages.push({ role: "error", text: event.text });
              this.phase = "idle";
              if (event.usage) this.usage = event.usage;
            } else if (event.type === "done") {
              this.messages.push(decorate(event.message));
              this.phase = "idle";
              this.streamText = "";
              this.statusLine = "";
              if (event.usage) this.usage = event.usage;
              this.$emit("changed");
              this.notify();
              this.scroll(true);
            }
          }
        }
      } catch (error) {
        this.messages.push({ role: "error", text: `连接失败：${error.message || error}` });
      } finally {
        this.busy = false;
        this.phase = "idle";
        this.streamText = "";
        this.$nextTick(() => this.focusInput());
      }
    },

    // ---------------- 输入框 ----------------
    useExample(text) {
      this.input = text;
      this.$nextTick(() => {
        this.focusInput();
        const element = this.$refs.inputEl;
        if (!element) return;
        element.style.height = "auto";
        element.style.height = `${Math.min(element.scrollHeight, 168)}px`;
      });
    },
    focusInput() {
      if (this.$refs.inputEl) this.$refs.inputEl.focus();
    },
    grow(event) {
      const element = event.target;
      element.style.height = "auto";
      element.style.height = `${Math.min(element.scrollHeight, 168)}px`;
    },
    onKeydown(event) {
      if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
        event.preventDefault();
        this.send();
      }
    },

    // ---------------- 展示 ----------------
    setNotice(text) {
      this.notice = text || "";
      if (this.noticeTimer) clearTimeout(this.noticeTimer);
      if (text) {
        this.noticeTimer = setTimeout(() => { this.notice = ""; }, 8000);
      }
    },
    scroll(force = false) {
      this.$nextTick(() => {
        const box = this.$refs.scrollEl;
        if (!box) return;
        const nearBottom = box.scrollHeight - box.scrollTop - box.clientHeight < 180;
        if (force || nearBottom) box.scrollTop = box.scrollHeight;
      });
    },
    streamScroll() {
      const now = Date.now();
      if (now - this.lastStreamScroll < 120) return;
      this.lastStreamScroll = now;
      this.scroll(false);
    },
    sourceRows(message) {
      return (message.sources || []).slice(0, MAX_SOURCE_ROWS).map((source, index) => ({
        n: index + 1,
        label: source.label || source.id || "未知来源",
        detail: source.detail || "",
        asof: source.as_of || "",
        url: source.url || "",
      }));
    },
    traceOf(message) {
      const steps = Array.isArray(message.trace) ? message.trace : [];
      const count = steps.length || message.trace_count || 0;
      const lines = steps.map((step) => {
        const outcome = step.ok === false ? `失败：${step.error || "未知错误"}` : "成功";
        return `${step.round || "-"} · ${step.name || "未知工具"} · ${step.company || "--"} · ${step.as_of || "无日期"} · ${outcome} · 耗时 ${formatDuration(step.duration_ms)}`;
      });
      return { count, lines };
    },
    async copyMessage(event, index) {
      const messageEl = event.currentTarget.closest(".msg");
      if (!messageEl) return;
      const body = messageEl.querySelector(".body");
      const rows = [...messageEl.querySelectorAll(".source-row, .src-note")].map((row) => row.innerText.trim());
      const parts = [];
      if (body) parts.push(body.innerText.trim());
      if (rows.length) parts.push(`来源：\n${rows.join("\n")}`);
      try {
        await navigator.clipboard.writeText(parts.join("\n\n"));
        this.copied = index;
        setTimeout(() => { if (this.copied === index) this.copied = -1; }, 1500);
      } catch (error) { /* 剪贴板不可用时不动 */ }
    },

    // ---------------- 引用悬停卡 ----------------
    showTip(event) {
      const target = event.target;
      const cite = target && target.closest ? target.closest("sup.cite") : null;
      if (!cite) return;
      if (this.hideTimer) { clearTimeout(this.hideTimer); this.hideTimer = null; }
      this.tipAnchor = cite.getBoundingClientRect();
      this.tip = {
        n: (cite.textContent || "").replace(/[\[\]]/g, ""),
        label: cite.dataset.label || "来源",
        detail: cite.dataset.detail || "",
        asof: cite.dataset.asof || "",
        url: cite.dataset.url || "",
        left: this.tipAnchor.left,
        top: this.tipAnchor.top,
        ready: false,
      };
      this.$nextTick(() => this.positionTip());
    },
    positionTip() {
      const element = this.$refs.tipEl;
      if (!element || !this.tipAnchor) return;
      const width = element.offsetWidth;
      const height = element.offsetHeight;
      let left = this.tipAnchor.left + this.tipAnchor.width / 2 - width / 2;
      left = Math.max(10, Math.min(left, window.innerWidth - width - 10));
      let top = this.tipAnchor.top - height - 10;
      if (top < 10) top = this.tipAnchor.bottom + 10;
      if (top + height > window.innerHeight - 10) {
        top = Math.max(10, window.innerHeight - height - 10);
      }
      this.tip = { ...this.tip, left, top, ready: true };
    },
    scheduleHide() {
      if (this.hideTimer) clearTimeout(this.hideTimer);
      this.hideTimer = setTimeout(() => {
        this.tip = null;
        this.hideTimer = null;
      }, 150);
    },
    cancelHide() {
      if (this.hideTimer) { clearTimeout(this.hideTimer); this.hideTimer = null; }
    },
    hideTip() {
      this.cancelHide();
      this.tip = null;
    },
    onTipOut(event) {
      const related = event.relatedTarget;
      if (related && related.closest && related.closest(".tip")) return;
      this.scheduleHide();
    },
  },
};

// ---------------------------------------------------------------------------
// 页面：左边常驻一个对话栏，右边按需再开一个。
// ---------------------------------------------------------------------------
createApp({
  components: { ChatPane },
  data() {
    return {
      conversations: [],
      search: "",
      leftCid: null,
      rightCid: null,
      splitOpen: false,
      splitRatio: 52,
      dragging: false,
      resizing: false,
    };
  },
  computed: {
    filteredConversations() {
      const needle = this.search.trim().toLowerCase();
      if (!needle) return this.conversations;
      return this.conversations.filter((item) =>
        [item.title, item.name, item.code]
          .filter(Boolean)
          .some((text) => String(text).toLowerCase().includes(needle))
      );
    },
    leftStyle() {
      if (!this.splitOpen) return { flex: "1 1 auto", minWidth: 0 };
      return { flex: `0 0 ${this.splitRatio}%`, minWidth: 0 };
    },
    rightStyle() {
      return { flex: "1 1 auto", minWidth: 0 };
    },
  },
  methods: {
    metaLine(item) {
      const parts = [];
      const date = shortDate(item.updated);
      if (date) parts.push(date);
      if (item.count) parts.push(`${item.count} 条`);
      if (item.id === this.leftCid) parts.push("当前");
      if (item.id === this.rightCid) parts.push("分屏");
      return parts.join(" · ");
    },

    async refresh() {
      try {
        const response = await fetch("/api/conversations");
        if (!response.ok) return;
        this.conversations = await response.json();
      } catch (error) { /* 列表失败不影响聊天 */ }
    },

    // ---------------- 打开对话 ----------------
    openLeft(id) {
      const pane = this.$refs.leftPane;
      if (pane) pane.open(id);
    },
    async openInRight(id) {
      if (id === this.leftCid) return;
      if (!this.splitOpen) {
        this.splitOpen = true;
        await this.$nextTick();
      }
      const pane = this.$refs.rightPane;
      if (pane) pane.open(id);
    },
    closeSplit() {
      this.splitOpen = false;
      this.rightCid = null;
      localStorage.removeItem(SPLIT_KEY);
    },
    newChat() {
      const pane = this.$refs.leftPane;
      if (pane) pane.newChat();
    },

    // ---------------- 拖动分屏 ----------------
    onDragStart(event, item) {
      try {
        event.dataTransfer.setData("text/plain", String(item.id));
        event.dataTransfer.effectAllowed = "copy";
      } catch (error) { /* 老浏览器不支持就算了 */ }
      this.dragging = true;
    },
    onDragEnd() {
      this.dragging = false;
    },
    onDrop(event) {
      this.dragging = false;
      let raw = "";
      try {
        raw = event.dataTransfer.getData("text/plain");
      } catch (error) { /* 没有数据 */ }
      const id = Number(raw);
      if (id) this.openInRight(id);
    },

    // ---------------- 分栏宽度 ----------------
    startResize(event) {
      event.preventDefault();
      const panes = this.$refs.panes;
      if (!panes) return;
      const total = panes.clientWidth;
      if (!total) return;
      const startX = event.clientX;
      const startRatio = this.splitRatio;
      this.resizing = true;
      document.body.style.userSelect = "none";
      document.body.style.cursor = "col-resize";
      const onMove = (moveEvent) => {
        const delta = moveEvent.clientX - startX;
        this.splitRatio = Math.min(MAX_RATIO, Math.max(MIN_RATIO, startRatio + (delta / total) * 100));
      };
      const onUp = () => {
        window.removeEventListener("mousemove", onMove);
        window.removeEventListener("mouseup", onUp);
        this.resizing = false;
        document.body.style.userSelect = "";
        document.body.style.cursor = "";
        localStorage.setItem(RATIO_KEY, String(this.splitRatio));
      };
      window.addEventListener("mousemove", onMove);
      window.addEventListener("mouseup", onUp);
    },

    // ---------------- 两栏状态 ----------------
    onLeftState(state) {
      this.leftCid = state.id;
      if (state.id) localStorage.setItem(STORAGE_KEY, String(state.id));
      else localStorage.removeItem(STORAGE_KEY);
      document.title = state.name ? `投资助手 · ${state.name}` : "投资助手";
    },
    onRightState(state) {
      this.rightCid = state.id;
      if (state.id) localStorage.setItem(SPLIT_KEY, String(state.id));
      else localStorage.removeItem(SPLIT_KEY);
    },
  },
  async mounted() {
    this.splitRatio = Number(localStorage.getItem(RATIO_KEY)) || 52;
    await this.refresh();
    const left = Number(localStorage.getItem(STORAGE_KEY));
    if (left) this.$refs.leftPane.open(left);
    const right = Number(localStorage.getItem(SPLIT_KEY));
    if (right) this.openInRight(right);
  },
}).mount("#app");
