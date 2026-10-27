# frontend

投资助手的原生页面：Vue 3（global build，不需要打包）+ marked + DOMPurify，由
`assistant/api.py`（Starlette）提供服务和数据接口。

- `index.html`：页面结构
- `styles.css`：样式
- `app.js`：`<chat-pane>` 对话栏（会话、SSE 流式回答、引用角标与悬停卡）+ 页面外壳
  （侧栏、拖动/按钮开分屏、分隔条调宽）。左右两栏各自持有状态和滚动区，
  互不撑高、互不影响；点引用角标或来源行会打开页面最右侧的来源详情侧栏，
  里面是该来源的元数据、当时工具返回的完整数据依据（字段行 / 表格 / 可折叠的
  嵌套块，另有原始 JSON）和取数记录，和左侧对话列表同一套样式，Esc 或 ✕ 关闭。
  拖动两侧栏的边缘都可以调宽（宽度记在 localStorage）；表格不截列、不截行，
  放不下时在表格内部横向/纵向滚动，表头固定在顶部。
  依据数据由 `GET /api/conversations/{id}/evidence?source_id=...` 从已保存的
  工具消息里取回
- 研究档案页只读，入口在左侧栏和对话栏标题；数据来自 `/api/research/companies`，
  不会从页面写档案。点公司进入详情，五个标签分别是当前结论、已验证事实（按类型
  分组，含指标表与来源链接）、待验证假设、结论变化时间线、未解决问题（按重要性
  分组）；顶部显示最后复核/最后写入时间，可返回公司列表或对话，对话状态保留
- `assets/`：第三方库，本地自带，不走 CDN

第三方库版本：Vue 3.5.44（MIT）、marked 18.1.1（MIT）、DOMPurify 3.4.17（Apache-2.0 OR MPL-2.0）。
升级时从 npm 包里拷 `vue/dist/vue.global.prod.js`、`marked/lib/marked.umd.js`、
`dompurify/dist/purify.min.js` 覆盖同名文件即可。

启动：`python -m assistant.api`，默认 http://127.0.0.1:8502 。
