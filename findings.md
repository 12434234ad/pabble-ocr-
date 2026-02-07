# Findings（项目发现与结论沉淀）

## 2026-02-05：分段 MD 未合并问题

### 现象复述（用户视角）
- 输出目录里 `imgs/` 或 `_parts/imgs/` 下已有 900+ 图片文件。
- 同时存在多个分段 Markdown（如 `part_001_p0001-0070.md`、`part_002_...md`），用户期望这些应被合并成一个完整 Markdown。
- 任务“重新跑一次”后仍觉得未完成合并。

### 已确认的输出结构（当前实现预期）
- 分段文件会保留在 `output_dir/_parts/` 下：`part_*.md`、`part_*.pdf`、`*_images.json`、`*_pruned.json` 等。
- 合并后的总文档写在 `output_dir/merged_result.md`（不在 `_parts` 目录里）。
- 因此：如果用户是在 Windows 资源管理器打开了 `output_dir/_parts/`，会看到很多 `.md`，这属于正常；需要回到上一层目录查看 `merged_result.md`。

### 潜在真实失败点（会导致“真的没生成 merged_result.md”）
- `merge.py` 中使用 `config.page_separator.join(...)` 直接 join；如果配置文件里 `page_separator` 变成 `null/None`（例如旧版配置、手工编辑损坏），会导致合并阶段抛异常，进而不会产出 `merged_result.md`。

### 修复思路（落地）
1. 合并逻辑对 `page_separator` 做容错：None/非字符串时安全降级。
2. 增加离线修复工具：对已有输出目录读取 `task_state.json` + `_parts/*.md`，重建 `merged_result.md`（不重跑 OCR）。
3. README/Windows 指引补充：解释 `_parts` 的意义、`merged_result.md` 的位置、以及“重建合并”的命令。

### 已落地的改动（对应文件）
- 合并容错：`pabble_ocr/md/merge.py`
- WSL/Windows 路径解析修复（支持 `E:\...` → `/mnt/e/...`）：`pabble_ocr/utils/paths.py`
- 离线重建工具：`pabble_ocr/tools/rebuild_merged_md.py`
- Windows 便捷脚本：`rebuild_merged_md.bat`
- 文档补充：`README.md`

## 2026-02-06：图片路径错配导致“合并后/EPUB 丢图”问题

### 真实样本结构（用户路径）
- 任务目录存在：`images/imgs`（939）+ `images/merged`（215）+ `_parts/*.md`
- 但 `_parts/imgs` 在初始时为空，`merged_result.md` 缺失
- 分段 md 内引用混合：`imgs/...` 与 `images/merged/...`

### 根因
- 合并时的路径改写逻辑优先覆盖“根目录”与“_parts”，未覆盖“图片实存于 `images/`”的映射场景；
- 当引用是 `imgs/...` 但文件实际在 `images/imgs/...` 时，若不做兼容改写，后续 md/epub 链路会出现缺图。

### 本次新增修复
1. 扩展 `pabble_ocr/md/merge.py::_rewrite_merged_md_image_paths`：
- 新增 `images/` 目录兼容；
- 当 `imgs/*` 或 `merged/*` 在 `images/` 命中时，自动改写为 `images/imgs/*` 或 `images/merged/*`。
2. 新增 `pabble_ocr/tools/check_markdown_assets.py`：
- 扫描 Markdown 与 HTML 图片引用；
- 检查本地命中并输出缺失列表；
- 返回码：`0`（全部命中）/`2`（存在缺失）。
3. README 新增 EPUB 前置校验与 Pandoc 命令模板。

### 验证结果（真实目录）
- 生成 `merged_result.md` 后执行资产校验：
- `total_refs=396`、`resolved_local_refs=396`、`missing_local_refs=0`。

## 2026-02-06（用户回访）：`md-epub` 侧仍报缺图的边界

### 用户现象
- 用户把图片和 md 文件“手工挪到一个目录”后再跑 `md-epub`；
- 转换日志出现缺图：`_parts/imgs/...`、`_parts/images/merged/...`。

### 结论
- 这是目录结构被破坏导致的路径失配，不是 OCR 主流程再次失败；
- `merged_result.md` 中仍含 `_parts/...` 引用时，必须保留对应 `_parts` 目录层级；
- 若要“单目录分发”，需要额外的打包/路径重写步骤（当前主流程未提供一键打包器）。

### 当前能力边界（截至 2026-02-06）
- 在原始任务目录中：可做到“分割 -> OCR -> 合并 -> 图片可命中”；
- 在用户手工搬运后：可能再次缺图（需保持目录结构或做自动打包）。

## 2026-02-06（文档同步）：B 方案规格已固化

### 新增文档
- `docs/PLAN_B_EPUB_EXPORT.md`

### 目的
- 给下一轮 `/new` 会话提供“决策完整”的实现起点，避免重复讨论。

### README 增补要点
- 新增“B 方案（导出包后交给 md-epub）”章节；
- 明确禁止“手工摊平目录”；
- 提供当前稳定流程与后续“一键导出包”计划入口。

## 2026-02-06（B 方案实现前）设计结论

### 已确认约束
- 目标规格来自 `docs/PLAN_B_EPUB_EXPORT.md`，执行顺序固定为：`CLI -> bat -> README -> 样本验证`。
- 现有 `check_markdown_assets` 已具备引用提取、路径归一化和命中判定能力，可复用为导出前后统计基线。
- 现有 bat 脚本约定：支持拖拽参数、校验 `.venv\Scripts\python.exe`、执行后 `pause`。

### 实现策略（本轮）
1. 新增 `pabble_ocr/tools/export_epub_pack.py`：
- 输入任务目录（含 `merged_result.md`），输出 `<task_dir>_epub_pack`；
- 导出前后执行资源校验，写出 `export_report.json`；
- 默认不覆盖已有输出，`--force` 强制重建。
2. 为降低重复逻辑，给 `check_markdown_assets` 增加可复用函数（CLI 保持兼容）。
3. 若导出后仍有缺图，按 `suggestion` 执行最小路径改写并复检，把改写明细写入报告。

## 2026-02-06（B 方案实现完成）新增能力

### 新增文件
- `pabble_ocr/tools/export_epub_pack.py`
- `export_epub_pack.bat`

### 变更文件
- `pabble_ocr/tools/check_markdown_assets.py`：新增可复用函数 `check_markdown_assets(md_path)`，CLI 行为保持兼容。
- `README.md`：B 方案改为“可执行流程”，包含 CLI + bat + 报告排错入口。

### 导出逻辑（最终）
1. 校验输入目录存在 `merged_result.md`。
2. 执行导出前校验（复用 `check_markdown_assets`）。
3. 复制 `merged_result.md` 到 `*_epub_pack`。
4. 按“已命中引用”复制资产文件，保持相对目录层级（不摊平）。
5. 对导出包再次校验；若仍缺图，按 `suggestion` 做最小路径改写并复检。
6. 生成 `export_report.json`（前后统计、复制数量、告警、改写明细、时间戳）。

### 行为约定
- 默认不覆盖已有导出目录；`--force` 时重建。
- 返回码：`0`（导出后无缺图）/`2`（失败或导出后仍缺图）。

## 2026-02-06（补漏页重跑）“391-455 不生效且从头跑”根因与修复

### 根因 1：补漏参数被纳入 OCR 变更哈希
- `process_file.py::_ocr_options_hash` 把 `pdf_image_ocr_pages/pdf_image_ocr_dpi/pdf_image_ocr_max_side_px` 纳入了 hash。
- 结果：用户只想改补漏页范围，也会触发“已完成分段参数变化”，从而把 done 分段全部打回重跑。

### 根因 2：分卷文件页码语义不一致
- 对分卷输入（如 `part_007_p0391-0455.pdf`），任务内部页码是 `1-65`。
- 用户按原文绝对页码填写 `391-455` 时，旧逻辑只按 `1-65` 比较，导致补漏页不命中。

### 本次修复
1. `pdf_image_ocr_*` 从 `ocr_options_hash` 剥离，避免补漏参数变更触发全量重跑。
2. 新增“补漏命中分段重跑”判定：仅命中页所在分段会重跑，其余 done 分段继续跳过。
3. 新增分卷文件绝对页码映射：若文件名含 `_pXXXX-YYYY`，补漏时支持 `391-455` 这类绝对页码。
4. 保持 `merge_and_materialize` 收口不变，确保补跑后自动重合并，图片走原有下载/复用逻辑不丢失。

### 验证结论
- 语法编译通过：`process_file.py`、`settings_dialog.py`、`config.py`。
- 依赖受限：运行动态脚本验证时环境缺少 `pypdf`，未执行到完整运行态自测。

## 2026-02-06（追加）为何升级后仍从 1/13 开始

### 新发现根因
- 新版把 `PDF_IMAGE_OCR_*` 从 `ocr_options_hash` 中移除后，哈希算法发生变化。
- 老任务 `task_state.json` 里保存的是“旧算法哈希”，升级后会被误判为“OCR 参数变化”，从而触发全量分段重跑。

### 处理
- 增加新旧哈希兼容判断：`saved_hash` 命中当前哈希或旧版哈希均视为可续跑。
- 这样升级后不会因哈希迁移导致误重跑。

## 2026-02-06（追加）补漏模式优先跳过未命中分段
- 现场反馈仍从 `1/13` 开始，说明在 `seg.done` 分支里仍可能被 hash 判定触发重跑。
- 已调整策略：当 `PDF_IMAGE_OCR_PAGES` 非空时，先按页段命中判定；未命中分段直接跳过，不再受 hash 差异影响。
- 增加日志：启动 PDF 处理时打印“已完成分段数量”，用于快速判断是否命中续跑状态。

## 2026-02-07（新需求）指定分段重打（输入 `009`）任务沉淀

### 用户诉求（明确）
- 需要“按分段号重打”，例如输入 `009` 即重跑 `part_009_...pdf`。
- 目标是分段级文档 OCR 重跑 + 自动合并，不希望触发页级图片补漏语义。
- 期望在 EXE 内直接完成，不再手工改 `task_state.json`。

### 关键认知（必须写进实现约束）
1. `PDF_IMAGE_OCR_PAGES` 是“页级补漏（图片模式）”开关，不等同于“分段重打”。
2. 分段重打应走现有 PDF 分段主流程（`file_type=0`），而不是 per-page 图片重跑（`file_type=1`）。
3. 实战中出现过状态漂移：历史 `task_state.json` 可能有多个 `ocr_options_hash`，会导致非目标分段被打回重跑。

### 实现前置约束
- 指定分段重打模式下，需要“目标分段白名单”优先级高于 hash 变化判定。
- 对非目标分段应尽量复用历史产物（md/images/pruned）并保持 `done=true`，避免无关 API 调用。
- 运行日志需显式打印“目标分段集合”和“跳过原因”，方便现场定位。

### 方案取舍（建议）
- 方案 A（推荐）：新增独立输入项 `PDF_RERUN_SEGMENTS`（如 `009`），仅控制分段级重打。
  - 优点：语义清晰，不与 `PDF_IMAGE_OCR_PAGES` 混淆。
  - 成本：需新增配置字段、设置 UI 与处理分支。
- 方案 B（不推荐）：复用 `PDF_IMAGE_OCR_PAGES` 解析分段号。
  - 问题：同名参数承载两种语义，极易误操作与误解。

### 验收关注点（从故障复盘提炼）
- 输入 `009` 时，首个 API 调用必须落在 `part_009_...`，而非 `3/13`、`5/13` 等非目标段。
- 完成后 `merged_result.md` 可用，且图片引用不退化。
- 清空 `PDF_RERUN_SEGMENTS` 后行为恢复为普通断点续跑。

## 2026-02-07（D 方案实现）指定分段重打已落地

### 代码改动
1. `pabble_ocr/config.py`
- 新增 `pdf_rerun_segments: str = ""`（配置持久化自动兼容）。

2. `pabble_ocr/ui/settings_dialog.py`
- 新增输入框：`PDF_RERUN_SEGMENTS（分段重打）`；
- `get_config()` 写回 `pdf_rerun_segments`。

3. `pabble_ocr/processing/process_file.py`
- 新增 `_parse_segment_spec`：支持 `009` 与 `9`（归一化 3 位）。
- 新增 `_segment_code_from_segment_id`：按 `part_XXX_...` 提取分段号。
- PDF 主流程新增“指定分段重打模式”：
  - 启动日志打印目标分段集合与命中分段；
  - 非目标分段跳过（优先复用历史产物）；
  - 目标分段强制重跑文档 OCR（`file_type=0`）。
- 错误保护：
  - `PDF_RERUN_SEGMENTS` 非法输入 -> 直接失败；
  - 目标分段无命中 -> 直接失败；
  - 与 `PDF_IMAGE_OCR_PAGES` 同时填写 -> 冲突失败（首版禁止混用）。

4. `README.md`
- 在“大 PDF 处理建议”新增 `PDF_RERUN_SEGMENTS=002` 用法；
- 明确其与 `PDF_IMAGE_OCR_PAGES` 的语义差异与互斥限制。

### 验证与限制
- 通过：`python -m compileall pabble_ocr/config.py pabble_ocr/ui/settings_dialog.py pabble_ocr/processing/process_file.py`
- 限制：运行态最小脚本验证受环境依赖限制（缺少 `pypdf`），未做端到端 OCR 调用验证。
