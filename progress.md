# Progress（过程日志）

## 2026-02-05

### 目标
解决“分段 `.md` 未合并成 `merged_result.md`”的疑问/故障，并确保 Win10 宿主机可自助修复。

### 已做
- 读取并理解合并逻辑：`pabble_ocr/md/merge.py`。
- 确认 PDF 处理流程在分段全部完成后会调用 `merge_and_materialize()`；失败分段时会调用 `merge_best_effort()` 并仍应生成 `merged_result.md`（带失败占位）。
- 在现有样本输出目录验证：`/mnt/e/output/_-_美国国家运动医学学会纠正性训练指南.../merged_result.md` 存在且包含 452 页 marker。
- 识别潜在合并崩溃点：`config.page_separator` 若为 `None` 会导致 `.join()` 报错。
- 已修复合并逻辑的 `page_separator` 容错：即使配置为 `None/非字符串` 也不会在合并阶段崩溃。
- 新增离线工具：`python -m pabble_ocr.tools.rebuild_merged_md <path>` 可重建 `merged_result.md`（不重跑 OCR）。
- 更新 `README.md`：补充 `_parts` 目录含义 + `merged_result.md` 位置 + 修复命令。
- 本地构造最小样例验证工具输出：能正确改写图片引用为 `_parts/imgs/...` 并生成 `merged_result.md`。
- 在真实输出目录验证（939 张图片场景）：`--force` 重建可正常完成并生成 `merged_result.md`。

### 收尾
- 已用 `--recursive --dry-run` 验证扫描行为；按需可加 `--stale/--force` 做批量重建。
- 本次改动文件清单：`pabble_ocr/md/merge.py`、`pabble_ocr/utils/paths.py`、`pabble_ocr/tools/rebuild_merged_md.py`、`README.md`、`rebuild_merged_md.bat`，以及规划文件 `task_plan.md/findings.md/progress.md`。

## 2026-02-06

### 目标
实现“分段合并后图片路径可自动对齐”，并在 Pandoc 转 EPUB 前可一键检测是否缺图。

### 已做
- 只读排查用户真实目录：`/mnt/e/output/_-_美国国家运动医学学会纠正性训练指南.../`
  - 初始状态：`merged_result.md` 缺失，`images/imgs` 与 `images/merged` 存在，`_parts/imgs` 为空。
  - `_parts/*.md` 引用混合 `imgs/...` 与 `images/merged/...`。
- 修改 `pabble_ocr/md/merge.py`：
  - `_rewrite_merged_md_image_paths` 新增 `images` 目录兼容与 `imgs/merged` 映射改写。
- 新增 `pabble_ocr/tools/check_markdown_assets.py`：
  - 支持 Markdown + HTML 图片引用扫描；
  - 输出统计与缺失样例；
  - 缺失时返回码 `2`。
- 更新 `README.md`：
  - 增加图片目录兼容说明；
  - 增加 EPUB 前置校验命令与 Pandoc `--resource-path` 模板。

### 验证
- 语法/入口：
  - `python -m compileall pabble_ocr/md/merge.py pabble_ocr/tools/check_markdown_assets.py`
  - `python -m pabble_ocr.tools.check_markdown_assets --help`
- 真实目录重建：
  - 使用 `_manual_merge_from_parts` 生成 `merged_result.md`。
- 资源命中检查：
  - `python -m pabble_ocr.tools.check_markdown_assets <merged_result.md>`
  - 结果：`total_refs=396`、`resolved_local_refs=396`、`missing_local_refs=0`。

### 用户回访（同日）
- 用户使用第三方项目 `md-epub` 执行一键转换可跑通，但在“手工汇总 md+图片到单目录”后出现缺图告警；
- 告警路径集中在 `_parts/imgs/*` 与 `_parts/images/merged/*`；
- 已确认根因是目录层级被摊平，非 OCR 产物本身缺失；
- 已给出临时修复（补回 `_parts` 结构）可恢复显示。

### 当前沟通状态
- 用户希望后续做到真正“全流程自动，无需手工修目录”；
- 当前进入讨论阶段：先定最终自动化收口方案，再决定是否继续代码实现。

## 2026-02-06（文档更新，供 `/new` 续接）

### 已做
- 更新 `README.md`：
  - 新增“B 方案（导出包后交给 md-epub）”章节；
  - 明确“不要手工摊平目录”与当前稳定操作步骤。
- 新增 `docs/PLAN_B_EPUB_EXPORT.md`：
  - 固化导出包目标结构、CLI 入口建议、执行流程、验收标准与风险边界。
- 同步 `task_plan.md` / `findings.md` 当前状态，标记文档预备完成。

### 结果
- 现在可直接 `/new`，并以 `docs/PLAN_B_EPUB_EXPORT.md` 作为下一轮实现任务输入。

## 2026-02-06（B 方案正式实现）

### 目标
按 `docs/PLAN_B_EPUB_EXPORT.md` 落地一键导出包：`export_epub_pack` CLI + Windows bat，并完成样本验证与 README 对齐。

### 已做（进行中）
- 读取并确认 B 方案规格、现有 README 章节和可复用 CLI 代码风格。
- 同步计划文件，拆分为 B1~B4 四个阶段并记录实现决策：
  - 默认不改写 Markdown；
  - `--force` 覆盖重建；
  - 导出后缺图时才做最小路径改写。

### 代码实现
- `pabble_ocr/tools/check_markdown_assets.py`
  - 新增 `check_markdown_assets(md_path)`，返回统计结果 + 已命中引用明细，供其他工具复用。
  - 原 CLI 文本输出与返回码语义保持不变。
- `pabble_ocr/tools/export_epub_pack.py`
  - 新增 CLI：`python -m pabble_ocr.tools.export_epub_pack <task_dir> [--force] [--out ...]`。
  - 执行导出前后校验，复制命中资源，生成 `export_report.json`。
  - 已存在导出目录且未传 `--force` 时返回 `2` 并输出 `[fail]`。
- 新增 `export_epub_pack.bat`
  - 支持拖拽目录与透传命令参数（如 `--force`）。

### 样本验证
- 构造样本目录：`/tmp/epub_pack_samples`
  - Case 1：`images/imgs + images/merged` 主导；
  - Case 2：`_parts/imgs + _parts/images/merged` 主导。
- 验证命令：
  - `python -m pabble_ocr.tools.export_epub_pack /tmp/epub_pack_samples/case_images`
  - `python -m pabble_ocr.tools.export_epub_pack /tmp/epub_pack_samples/case_parts`
  - `python -m pabble_ocr.tools.check_markdown_assets /tmp/epub_pack_samples/case_images_epub_pack/merged_result.md`
  - `python -m pabble_ocr.tools.check_markdown_assets /tmp/epub_pack_samples/case_parts_epub_pack/merged_result.md`
  - 重复导出校验：不加 `--force` 返回 `2`；加 `--force` 成功重建。
- 结果：
  - 两类样本导出后均 `missing_local_refs=0`；
  - `export_report.json` 正常生成并包含 pre/post 统计。

### 文档对齐
- 更新 `README.md` 的 B 方案章节：
  - 从“计划项”改为已实现的一键导出流程；
  - 增加 CLI、bat、`export_report.json` 排障说明。

## 2026-02-06（补漏页重跑修复）

### 目标
解决“`PDF_IMAGE_OCR_PAGES=391-455` 不生效且一开始就从头重跑”的问题，保证只补跑命中页段并自动重合并 `merged_result.md`。

### 已做
- 修改 `pabble_ocr/processing/process_file.py`：
  - `pdf_image_ocr_*` 不再参与 `ocr_options_hash`。
  - 增加 `_infer_pdf_page_range_from_filename`、`_map_local_page_to_inferred_absolute_page`、`_segment_matches_rerun_pages`。
  - `seg.done` 分支增加“补漏页命中则仅重跑该分段”逻辑。
  - 补漏页匹配支持本文件页码与分卷绝对页码双语义。
- 修改 `pabble_ocr/ui/settings_dialog.py`：补漏输入框占位文案增加分卷绝对页码示例。
- 修改 `pabble_ocr/config.py` 与 `README.md`：同步行为说明。

### 验证
- `python -m compileall pabble_ocr/processing/process_file.py pabble_ocr/ui/settings_dialog.py pabble_ocr/config.py`：通过。
- 动态脚本验证（页码映射函数）尝试失败：`ModuleNotFoundError: No module named 'pypdf'`。

### 影响评估
- 用户修改补漏页参数时，不会再触发所有已完成分段全量重跑。
- `part_007_p0391-0455.pdf` 场景可直接填写 `391-455` 命中补漏页。
- 完成后仍走原合并逻辑，自动重写 `merged_result.md` 并复用既有图片资源。

### 2026-02-06（追加修复）
- 发现升级副作用：新旧 `ocr_options_hash` 算法不一致会导致旧任务状态被误判为参数变化，从而从 `1/13` 开始重跑。
- 已在 `process_file.py` 增加新旧哈希兼容判断（`_is_ocr_hash_compatible`），并保留“补漏页仅重跑命中分段”逻辑。
- 验证：编译通过；脚本验证 `compat_new=True`、`compat_old=True`。

### 2026-02-06（追加修复 2）
- 针对“仍从 1/13 开始”追加保护：`PDF_IMAGE_OCR_PAGES` 非空时，未命中页段的 done 分段直接跳过（补漏模式优先）。
- 新增运行前日志：`检测到可续跑状态：X/Y 分段已完成` 或 `未检测到已完成分段`，便于定位是否命中原 task_state。
- 验证：编译通过；最小脚本验证 `391-455` 仅命中 `391-455` 分段，不命中 `1-65`。

## 2026-02-07（文档交接：指定分段重打）

### 本次仅做文档，不改功能代码
- 新增下一阶段任务定义：在 EXE 内支持“输入分段号（如 `009`）重打指定分段”。
- 明确语义边界：该能力属于“分段级文档 OCR 重跑”，不等于 `PDF_IMAGE_OCR_PAGES` 的页级图片补漏。
- 同步了 `task_plan.md` 与 `findings.md` 的实现要点、验收标准和方案取舍。
- 新增规格文档 `docs/PLAN_D_SEGMENT_RERUN.md`，供下个会话直接按规格开发。

### 下个会话建议启动语句
- “按 `docs/PLAN_D_SEGMENT_RERUN.md` 实现 D 方案：EXE 增加 `PDF_RERUN_SEGMENTS`，支持输入 `009` 只重打 `part_009`，并补充日志与 README。”

## 2026-02-07（D 方案正式实现）

### 目标
按 `docs/PLAN_D_SEGMENT_RERUN.md` 落地 EXE 分段重打：输入分段号（如 `002`）仅重跑 `part_002_pxxxx-yyyy`，并补充日志与 README。

### 已做
- 新增配置项 `pdf_rerun_segments`（`pabble_ocr/config.py`）。
- 设置页新增输入框 `PDF_RERUN_SEGMENTS（分段重打）` 并持久化（`pabble_ocr/ui/settings_dialog.py`）。
- `process_file.py` 新增：
  - `_parse_segment_spec`（`9 -> 009` 归一化）；
  - `_segment_code_from_segment_id`（按 `part_XXX_` 匹配）。
- PDF 主流程新增“指定分段重打模式”：
  - 启动打印目标分段与命中分段日志；
  - 非目标分段跳过并优先复用历史产物；
  - 目标分段强制重跑文档 OCR；
  - 非法输入/无命中/与 `PDF_IMAGE_OCR_PAGES` 冲突时立即报错退出（不发 OCR 请求）。
- README 新增 `PDF_RERUN_SEGMENTS` 使用说明与互斥提示。

### 验证
- 编译通过：
  - `python -m compileall pabble_ocr/config.py pabble_ocr/ui/settings_dialog.py pabble_ocr/processing/process_file.py`
- 限制：
  - 尝试运行导入级动态脚本时触发 `ModuleNotFoundError: No module named 'pypdf'`，因此本轮未做端到端 OCR 实跑验证。
