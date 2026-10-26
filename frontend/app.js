const { createApp, ref, computed, watch, nextTick, onMounted } = Vue;

const WELCOME_EXAMPLES = [
  "紫金矿业靠什么赚钱？",
  "东鹏饮料毛利率为什么下滑？",
  "今天大盘怎么看？",
];
const STORAGE_KEY = "native_proto_cid";
const MAX_SOURCE_ROWS = 6;

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

createApp({
  setup() {
    const conversations = ref([]);
    const currentId = ref(null);
    const activeName = ref("");
    const activeCode = ref("");
    const messages = ref([]);
    const notice = ref("");
    const search = ref("");
    const input = ref("");
    const busy = ref(false);
    const phase = ref("idle");
    const streamText = ref("");
    const streamEmpty = ref("正在思考…");
    const statusLine = ref("");
    const usage = ref(null);
    const copied = ref(-1);
    const tip = ref(null);
    const scrollEl = ref(null);
    const inputEl = ref(null);
    const tipEl = ref(null);

    let noticeTimer = null;
    let hideTimer = null;
    let lastStreamScroll = 0;
    let tipAnchor = null;

    const titleText = computed(() =>
      activeName.value ? `投资助手 · ${activeName.value}` : "投资助手"
    );

    const filteredConversations = computed(() => {
      const needle = search.value.trim().toLowerCase();
      if (!needle) return conversations.value;
      return conversations.value.filter((item) =>
        [item.title, item.name, item.code]
          .filter(Boolean)
          .some((text) => String(text).toLowerCase().includes(needle))
      );
    });

    const streamHtml = computed(() => renderBody(streamText.value, [], true));

    const meterStyle = computed(() => {
      const current = usage.value || {};
      const ratio = Math.max(0, Math.min(1, Number(current.ratio) || 0));
      const level = current.level === "over" ? "over" : current.level === "warn" ? "warn" : "normal";
      const color = level === "over" ? "#e5655c" : level === "warn" ? "#e8b96b" : "#5b8cff";
      return { "--pct": String(ratio * 100), "--color": color };
    });

    const meterText = computed(() => {
      const percent = Number((usage.value || {}).percent) || 0;
      return percent >= 1000 ? "999+" : `${Math.round(percent)}%`;
    });

    const meterTitle = computed(() => {
      const current = usage.value || {};
      if (!current.tokens) return "上下文占用";
      return `上下文 ${formatTokens(current.tokens)} / ${formatTokens(current.budget_tokens)} tokens · ${Math.round(Number(current.percent) || 0)}%`;
    });

    watch(titleText, (text) => { document.title = text; }, { immediate: true });

    function setNotice(text) {
      notice.value = text || "";
      if (noticeTimer) clearTimeout(noticeTimer);
      if (text) {
        noticeTimer = setTimeout(() => { notice.value = ""; }, 8000);
      }
    }

    function metaLine(item) {
      const parts = [];
      const date = shortDate(item.updated);
      if (date) parts.push(date);
      if (item.count) parts.push(`${item.count} 条`);
      if (item.id === currentId.value) parts.push("当前");
      return parts.join(" · ");
    }

    function sourceRows(message) {
      return (message.sources || []).slice(0, MAX_SOURCE_ROWS).map((source, index) => ({
        n: index + 1,
        label: source.label || source.id || "未知来源",
        detail: source.detail || "",
        asof: source.as_of || "",
        url: source.url || "",
      }));
    }

    function traceOf(message) {
      const steps = Array.isArray(message.trace) ? message.trace : [];
      const count = steps.length || message.trace_count || 0;
      const lines = steps.map((step) => {
        const outcome = step.ok === false ? `失败：${step.error || "未知错误"}` : "成功";
        return `${step.round || "-"} · ${step.name || "未知工具"} · ${step.company || "--"} · ${step.as_of || "无日期"} · ${outcome}`;
      });
      return { count, lines };
    }

    function decorate(message) {
      return { ...message, html: renderBody(message.text || "", message.sources || [], false) };
    }

    function scroll(force = false) {
      nextTick(() => {
        const box = scrollEl.value;
        if (!box) return;
        const nearBottom = box.scrollHeight - box.scrollTop - box.clientHeight < 180;
        if (force || nearBottom) box.scrollTop = box.scrollHeight;
      });
    }

    function streamScroll() {
      const now = Date.now();
      if (now - lastStreamScroll < 120) return;
      lastStreamScroll = now;
      scroll(false);
    }

    // ---------------- 引用悬停卡 ----------------
    function showTip(event) {
      const target = event.target;
      const cite = target && target.closest ? target.closest("sup.cite") : null;
      if (!cite) return;
      if (hideTimer) { clearTimeout(hideTimer); hideTimer = null; }
      tipAnchor = cite.getBoundingClientRect();
      tip.value = {
        n: (cite.textContent || "").replace(/[\[\]]/g, ""),
        label: cite.dataset.label || "来源",
        detail: cite.dataset.detail || "",
        asof: cite.dataset.asof || "",
        url: cite.dataset.url || "",
        left: tipAnchor.left,
        top: tipAnchor.top,
        ready: false,
      };
      nextTick(positionTip);
    }

    function positionTip() {
      const element = tipEl.value;
      if (!element || !tipAnchor) return;
      const width = element.offsetWidth;
      const height = element.offsetHeight;
      let left = tipAnchor.left + tipAnchor.width / 2 - width / 2;
      left = Math.max(10, Math.min(left, window.innerWidth - width - 10));
      let top = tipAnchor.top - height - 10;
      if (top < 10) top = tipAnchor.bottom + 10;
      if (top + height > window.innerHeight - 10) {
        top = Math.max(10, window.innerHeight - height - 10);
      }
      tip.value = { ...tip.value, left, top, ready: true };
    }

    function scheduleHide() {
      if (hideTimer) clearTimeout(hideTimer);
      hideTimer = setTimeout(() => {
        tip.value = null;
        hideTimer = null;
      }, 150);
    }

    function cancelHide() {
      if (hideTimer) { clearTimeout(hideTimer); hideTimer = null; }
    }

    function hideTip() {
      cancelHide();
      tip.value = null;
    }

    function onTipOut(event) {
      const related = event.relatedTarget;
      if (related && related.closest && related.closest(".tip")) return;
      scheduleHide();
    }

    // ---------------- 会话 ----------------
    async function loadRecent() {
      try {
        const response = await fetch("/api/conversations");
        if (!response.ok) return;
        conversations.value = await response.json();
      } catch (error) { /* 列表失败不影响聊天 */ }
    }

    async function openConversation(id) {
      if (busy.value) return;
      try {
        const response = await fetch(`/api/conversations/${id}`);
        if (!response.ok) {
          newConversation();
          return;
        }
        const data = await response.json();
        currentId.value = data.id;
        localStorage.setItem(STORAGE_KEY, String(data.id));
        activeName.value = data.name || "";
        activeCode.value = data.code || "";
        messages.value = (data.messages || []).map(decorate);
        usage.value = null;
        phase.value = "idle";
        streamText.value = "";
        notice.value = "";
        hideTip();
        scroll(true);
      } catch (error) {
        newConversation();
      }
    }

    function newConversation() {
      currentId.value = null;
      localStorage.removeItem(STORAGE_KEY);
      messages.value = [];
      activeName.value = "";
      activeCode.value = "";
      usage.value = null;
      phase.value = "idle";
      streamText.value = "";
      notice.value = "";
      copied.value = -1;
      hideTip();
      scroll(true);
      nextTick(() => inputEl.value && inputEl.value.focus());
    }

    async function deleteCurrent() {
      if (currentId.value === null) return;
      try {
        await fetch(`/api/conversations/${currentId.value}/delete`, { method: "POST" });
      } catch (error) { /* 删不掉也先清页面 */ }
      newConversation();
      loadRecent();
    }

    function useExample(text) {
      input.value = text;
      nextTick(() => {
        const element = inputEl.value;
        if (!element) return;
        element.focus();
        element.style.height = "auto";
        element.style.height = `${Math.min(element.scrollHeight, 168)}px`;
      });
    }

    async function copyMessage(event, index) {
      const messageEl = event.currentTarget.closest(".msg");
      if (!messageEl) return;
      const body = messageEl.querySelector(".body");
      const rows = [...messageEl.querySelectorAll(".source-row, .src-note")].map((row) => row.innerText.trim());
      const parts = [];
      if (body) parts.push(body.innerText.trim());
      if (rows.length) parts.push(`来源：\n${rows.join("\n")}`);
      const text = parts.join("\n\n");
      try {
        await navigator.clipboard.writeText(text);
        copied.value = index;
        setTimeout(() => { if (copied.value === index) copied.value = -1; }, 1500);
      } catch (error) { /* 剪贴板不可用时不动 */ }
    }

    function grow(event) {
      const element = event.target;
      element.style.height = "auto";
      element.style.height = `${Math.min(element.scrollHeight, 168)}px`;
    }

    function onKeydown(event) {
      if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
        event.preventDefault();
        send();
      }
    }

    async function send() {
      const question = input.value.trim();
      if (!question || busy.value) return;

      const userMessage = { role: "user", text: question };
      messages.value.push(userMessage);
      input.value = "";
      busy.value = true;
      phase.value = "streaming";
      streamText.value = "";
      streamEmpty.value = "正在思考…";
      statusLine.value = "";
      notice.value = "";
      copied.value = -1;
      hideTip();
      if (inputEl.value) inputEl.value.style.height = "auto";
      scroll(true);

      const cidAtSend = currentId.value;

      try {
        const response = await fetch("/api/ask", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ conversation_id: cidAtSend, question }),
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
              const previous = currentId.value;
              if (event.conversation_id && event.conversation_id !== currentId.value) {
                currentId.value = event.conversation_id;
                localStorage.setItem(STORAGE_KEY, String(currentId.value));
                if (previous !== null) {
                  // 换公司时服务端开了新会话：旧记录清掉，只留这一问
                  messages.value = [userMessage];
                }
                loadRecent();
              }
              activeName.value = event.name || "";
              activeCode.value = event.code || "";
              if (event.notice) setNotice(event.notice);
            } else if (event.type === "text") {
              streamText.value += event.delta || "";
              streamScroll();
            } else if (event.type === "discard") {
              streamText.value = "";
              streamEmpty.value = "正在取数…";
              statusLine.value = "";
            } else if (event.type === "tool") {
              const outcome = event.cached ? "复用缓存" : event.ok ? "成功" : "失败";
              statusLine.value = `已取数 · ${event.name || "工具"} · ${event.company || "--"} · ${outcome}`;
            } else if (event.type === "meter") {
              usage.value = event.usage || null;
            } else if (event.type === "warning") {
              messages.value.push({ role: "assistant", text: event.text, warning: true });
              phase.value = "idle";
            } else if (event.type === "error") {
              messages.value.push({ role: "error", text: event.text });
              phase.value = "idle";
              if (event.usage) usage.value = event.usage;
            } else if (event.type === "done") {
              messages.value.push(decorate(event.message));
              phase.value = "idle";
              streamText.value = "";
              statusLine.value = "";
              if (event.usage) usage.value = event.usage;
              loadRecent();
              scroll(true);
            }
          }
        }
      } catch (error) {
        messages.value.push({ role: "error", text: `连接失败：${error.message || error}` });
      } finally {
        busy.value = false;
        phase.value = "idle";
        streamText.value = "";
        nextTick(() => inputEl.value && inputEl.value.focus());
      }
    }

    onMounted(async () => {
      await loadRecent();
      const stored = localStorage.getItem(STORAGE_KEY);
      if (stored) {
        await openConversation(Number(stored));
      }
      window.addEventListener("resize", hideTip);
    });

    return {
      examples: WELCOME_EXAMPLES,
      conversations,
      currentId,
      activeName,
      activeCode,
      messages,
      notice,
      search,
      input,
      busy,
      phase,
      streamText,
      streamEmpty,
      statusLine,
      usage,
      copied,
      tip,
      scrollEl,
      inputEl,
      tipEl,
      titleText,
      filteredConversations,
      streamHtml,
      meterStyle,
      meterText,
      meterTitle,
      metaLine,
      sourceRows,
      traceOf,
      showTip,
      onTipOut,
      cancelHide,
      scheduleHide,
      hideTip,
      formatTokens,
      openConversation,
      newConversation,
      deleteCurrent,
      useExample,
      copyMessage,
      grow,
      onKeydown,
      send,
    };
  },
}).mount("#app");
