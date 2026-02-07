# Plan D 规格：指定分段重打（输入 `009`）

## 1. 背景与目标
- 背景：现场补打时，用户希望“只重跑某个分段 PDF（如 009）”，而不是手工修改 `task_state.json`。
- 目标：在 EXE 内提供“指定分段重打”能力，输入分段号后仅重跑目标分段，完成后自动合并 `merged_result.md`。

## 2. 语义边界（必须）
- 本功能是“分段级重打”（segment-level rerun）。
- 不等同于 `PDF_IMAGE_OCR_PAGES`（页级图片补漏）。
- 默认走文档 OCR 主流程（PDF 分段 `file_type=0`），不触发页级图片重跑分支。

## 3. In Scope / Out of Scope
- In Scope
  - 新增配置项：`PDF_RERUN_SEGMENTS`（示例：`009`）。
  - 设置页可填写该值并持久化。
  - 处理阶段按分段号命中：仅目标分段重跑，非目标分段跳过并复用历史产物。
  - 日志明确打印“本次目标分段集合”与“跳过原因”。
- Out of Scope
  - 不更改 Serving API。
  - 不改页级补漏参数语义。
  - 不引入新的状态存储文件。

## 4. 输入规范（首版）
- 支持单值：`009`。
- 兼容输入：`9`（内部归一化为 `009`）。
- 匹配依据：`segment_id` 前缀序号（`part_009_...`），不是 UI 中“第几项队列”。
- 非法输入或无命中分段：应中止并提示，不发起 OCR 请求。

## 5. 处理逻辑（推荐）
1. 读取 `PDF_RERUN_SEGMENTS` 并解析为目标分段号集合。
2. 对每个 `seg`：
   - 若命中目标分段：设置为待跑（`done=false`），执行 OCR。
   - 若未命中：
     - 若历史产物完整（`_parts/<seg>.md` + `_images.json` + `_pruned.json`），保持/回收为 `done=true`；
     - 否则按现有失败分支处理（可保留占位逻辑）。
3. 全流程结束后沿用 `merge_and_materialize` 自动合并。

## 6. 与现有参数关系
- `PDF_RERUN_SEGMENTS` 与 `PDF_IMAGE_OCR_PAGES` 同时填写时，优先级建议：
  - 分段级重打优先决定“跑哪些分段”；
  - 页级补漏仅在“命中分段内部”生效（可选，首版可先禁止并给冲突提示）。

## 7. 日志与可观测性
- 启动时：
  - `指定分段重打模式：目标分段=009`
- 未命中分段：
  - `分段重打模式：跳过非目标分段 i/total（start-end）`
- 命中分段：
  - `分段重打模式：重跑目标分段 i/total（start-end）`
- 冲突/错误：
  - `未匹配到目标分段：009`

## 8. 验收标准
1. 目标案例：输入 `009` 时，仅 `part_009_...` 发起 API 调用。
2. 非目标分段不被误重跑，日志可证明跳过原因。
3. 执行后成功合并 `merged_result.md`，图片引用可命中。
4. 清空 `PDF_RERUN_SEGMENTS` 后，行为恢复普通续跑逻辑。

## 9. 建议改动点（代码）
- `pabble_ocr/config.py`
  - 新增 `pdf_rerun_segments: str = ""`
- `pabble_ocr/ui/settings_dialog.py`
  - 新增输入框 `PDF_RERUN_SEGMENTS（分段重打）`
- `pabble_ocr/processing/process_file.py`
  - 新增分段号解析函数（如 `_parse_segment_spec`）
  - 在 PDF 分段循环中引入“目标分段白名单”判定
- `README.md`
  - 新增“指定分段重打（文档 OCR）”说明与示例

## 10. 回归样例
- Case 1：`13` 段任务，输入 `009`，仅第 9 段重跑。
- Case 2：输入不存在分段 `099`，应直接提示并退出。
- Case 3：历史状态 hash 混杂，仍应保持“非目标不跑”。
