# Task Plan: 分段 Markdown 未合并修复（Win10）

## Goal
用户在 Windows 10 宿主机运行时，输出目录下存在大量分段 `.md`（如 `_parts/part_*.md`），但未生成（或用户找不到）总 Markdown `merged_result.md`。目标：
1. 明确输出目录结构与“分段文件保留”的预期，避免误判为“未合并”。
2. 修复潜在的合并失败点（容错坏配置/异常输入）。
3. 提供“离线重建合并”的修复工具（不重跑 OCR），并补充 README/Windows 用法。

## Non-Goals
- 不改 Serving API/识别质量策略
- 不重构队列/任务模型

## Phases
- [x] Phase 1: 复现/定位（确认 merged_result.md 缺失的真实原因）
- [x] Phase 2: 修复合并逻辑的健壮性（坏配置不崩）
- [x] Phase 3: 增加离线修复工具（扫描/重建 merged_result.md）
- [x] Phase 4: 文档与 Win10 使用指引更新
- [x] Phase 5: 在现有输出样本上验证（含 900+ 图片场景）

## Key Questions
1. 用户看到的“很多 md 文件”是否位于 `output_dir/_parts/`（正常保留），而 `merged_result.md` 在父目录？
2. 是否存在配置被写成 `null` 导致 `page_separator.join(...)` 报错，从而合并步骤失败？
3. 对于“已生成分段 md 但 merged 缺失”的场景，如何一键重建且不影响断点续跑？

## Decisions Made
- 分段输出（`_parts/*.md`、`_parts/imgs|images/`）保留不删除；总文档统一输出为 `output_dir/merged_result.md`。
- 合并时对 `page_separator` 做容错（None/非字符串 → 当作空分隔符或转字符串），避免合并阶段崩溃。
- 提供 CLI 工具：对单个输出目录或整个 output root 扫描并重建 `merged_result.md`。

## Errors Encountered
| 时间 | 错误 | 影响 | 处理 |
|------|------|------|------|
| 2026-02-05 | （待记录） |  |  |

## Status
**Completed**（已实现容错合并 + 离线重建工具 + 文档说明）

## 2026-02-06 增量（图片路径与 EPUB 不丢图）
- [x] Phase A: 定位真实目录结构差异（`images/imgs` + `images/merged`）
- [x] Phase B: 扩展合并路径改写逻辑（兼容 `imgs/` 与 `merged/` 历史引用）
- [x] Phase C: 新增 EPUB 前置资源校验工具 `check_markdown_assets`
- [x] Phase D: README 增补 Pandoc `--resource-path` 与校验流程
- [x] Phase E: 在真实输出目录验证（396/396 图片引用命中）
- [x] Phase F: 用户侧验证（md-epub 转换成功，但“摊平目录”会触发 `_parts/...` 缺图）

### 2026-02-06 Decisions
- 读取兼容优先：`root` -> `_parts` -> `images`，并对 `imgs/*`、`merged/*` 提供 `images/...` 映射兜底。
- 不强制迁移历史目录，仅在合并写出时改写为可命中路径。
- 新工具返回码约定：`0=全部命中`，`2=存在缺图`（可作为自动化门禁）。
- 使用第三方 `md-epub` 时，不应手工把图片“摊平到单目录”；应保留任务目录结构（至少保留 `_parts` 与 `images`）。

## Next (讨论阶段，不执行代码)
- [ ] 定义“一键导出给 md-epub 的标准包结构/脚本”，消除用户手工搬运目录导致的缺图。
- [ ] 定义主流程收口：OCR 完成后自动触发 `check_markdown_assets`，并给出可点击的导出提示。
- [x] 文档预备：新增 B 方案落地规格文档，供 `/new` 会话直接开工。
- [x] README 补充 B 方案操作指引（面向新手，不手工摊平目录）。

## 2026-02-06 增量（二期落地：`export_epub_pack`）
- [x] Phase B1: 新增 `pabble_ocr.tools.export_epub_pack` CLI（导出 + 前后校验 + 报告）
- [x] Phase B2: 新增 `export_epub_pack.bat`（拖拽目录）
- [x] Phase B3: 两类目录样本验证（`images/*` 主导、`_parts/*` 主导）
- [x] Phase B4: README 对齐（从“计划项”更新为“可执行流程”）

### 2026-02-06（B 方案）Key Questions
1. 导出包是否默认保持 `merged_result.md` 原文，仅在导出后校验失败时做最小改写？
2. 资产复制是“按引用最小集合”还是“整目录镜像”？
3. `--force` 覆盖策略如何定义，才能保证重复导出行为稳定？

### 2026-02-06（B 方案）Decisions
- 默认不改写 Markdown，仅复制命中引用对应的资产文件到 `*_epub_pack`，保持相对层级。
- 导出后若仍有缺图，按 `check_markdown_assets` 给出的 `suggestion` 做最小路径改写并复检。
- 默认不覆盖已有包；指定 `--force` 时清理并重建导出包。

### 2026-02-06（B 方案）Status
**Completed**（CLI + bat + README + 样本验证已闭环）

---

## Archive
### 2026-02-01：PRD 审核任务（已完成）
- 输出：`PRD_review.md`

## 2026-02-06 增量（三期：补漏页重跑只命中指定分段）
- [x] Phase C1: 定位 `PDF_IMAGE_OCR_PAGES` 导致全量重跑的根因
- [x] Phase C2: 调整重跑判定，仅命中分段重跑
- [x] Phase C3: 增强分卷文件绝对页码命中（`part_007_p0391-0455.pdf`）
- [x] Phase C4: 文档/设置文案同步
- [x] Phase C5: 语法检查与最小逻辑验证

### 2026-02-06（C 方案）Key Questions
1. 为什么用户一填 `391-455` 就从头重跑？
2. 分卷文件内页码是 `1-65`，如何兼容用户输入绝对页码 `391-455`？
3. 只重跑补漏段后，能否保持自动重合并且不丢图？

### 2026-02-06（C 方案）Decisions
- `pdf_image_ocr_pages/dpi/max_side` 从 `ocr_options_hash` 剥离，避免补漏参数变化触发已完成分段全量重跑。
- 对已完成分段增加“补漏页命中”判定：命中则仅重跑该分段，未命中分段继续跳过。
- 若输入文件名含页段（`*_p0391-0455.pdf`），补漏页支持同时按本文件页码和绝对页码命中。
- 保持结尾 `merge_and_materialize` 流程不变，沿用已下载图片与分段图片索引，避免丢图。

### 2026-02-06（C 方案）Status
**Completed**（代码 + 文案 + 编译验证完成）

## 2026-02-07 增量（D 方案：指定分段重打）
- [x] Phase D1: 需求与边界固化（“分段重打”与“页级补漏”语义隔离）
- [x] Phase D2: 交互设计（EXE 内输入分段号，如 `009`）
- [x] Phase D3: 任务状态收敛策略（只重打命中分段，其他分段稳定跳过）
- [x] Phase D4: 日志与可观测性（明确打印“本次仅重打 X/总段数”）
- [x] Phase D5: README/设置文案对齐与回归验证

### 2026-02-07（D 方案）Goal
在 EXE 中提供“补打指定分段”能力，用户只输入分段号（如 `009`）即可触发该分段的文档 OCR 重跑，并在完成后自动合并，避免再手工编辑 `task_state.json`。

### 2026-02-07（D 方案）Non-Goals
- 不做“页级图片补漏”增强（`PDF_IMAGE_OCR_PAGES` 语义保持不变）。
- 不改 Serving API 协议与 OCR 识别策略。
- 不引入新的任务持久化格式（优先兼容现有 `task_state.json`）。

### 2026-02-07（D 方案）Key Questions
1. 输入语法只支持单值（`009`）还是支持集合（`009,011`）与范围（`009-011`）？
2. 分段号匹配依据是 `segment_id` 序号（`part_009_...`）还是队列序号（`9/13`）？
3. 当历史状态出现 `done=false` 但产物完整时，是否自动回收为已完成，避免被误重跑？
4. 若用户输入分段号不存在，提示策略是“直接失败”还是“仅警告并不执行”？

### 2026-02-07（D 方案）Proposed Behavior
- 推荐输入语法：支持 `009`（首版必做），可兼容 `9` 并归一化到 3 位。
- 仅匹配 `segment_id` 前缀序号（如 `part_009_...`），不依赖队列显示序号。
- 启用“指定分段重打”后：
  - 命中分段：强制重跑（文档 OCR，`file_type=0`）。
  - 未命中分段：若已有可复用产物则跳过并保持完成。
  - 自动合并：沿用现有 `merge_and_materialize` 收口。
- 与 `PDF_IMAGE_OCR_PAGES` 解耦：该功能默认不触发图片 OCR 分支。

### 2026-02-07（D 方案）Acceptance
1. 输入 `009` 后启动任务，日志应明确出现“仅重打分段 9/13（part_009_...）”。
2. 其他分段不发生 API 调用，运行耗时接近单分段。
3. 任务完成后自动更新 `merged_result.md`，图片引用保持可命中。
4. 输入不存在分段号时，给出可读错误提示且不发起 OCR 请求。

### 2026-02-07（D 方案）Status
**Completed**（配置/UI/处理逻辑/README 已落地，编译验证通过）

### 2026-02-07（D 方案）Implementation Notes
- 配置新增：`AppConfig.pdf_rerun_segments`，设置页新增 `PDF_RERUN_SEGMENTS（分段重打）` 输入框并持久化。
- 分段选择：新增 `_parse_segment_spec`（支持 `009` 与 `9` 归一化）和 `_segment_code_from_segment_id`（匹配 `part_009_...`）。
- 处理策略：启用该模式后仅命中分段允许进入 OCR，非目标分段统一跳过并优先复用历史产物。
- 保护策略：
  - 非法输入立即失败（不发起 OCR 请求）；
  - 目标分段无命中立即失败；
  - 与 `PDF_IMAGE_OCR_PAGES` 同时填写时直接报冲突错误，避免语义混用。
