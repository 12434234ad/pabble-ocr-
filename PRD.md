# 产品需求文档 (PRD): PaddleOCR-VL 智能识别客户端

> [!IMPORTANT]
> **状态**: 可开工 (v0.1) - API 契约已明确，待实现
> **作者**: Antigravity
> **日期**: 2026-01-31

## 1. 项目背景
用户需要通过已部署的 PaddleOCR-VL-1.5 Serving API（使用 `Authorization: token ...` 鉴权），将本地的 PDF 或图片文件批量识别并转换为带图片的 Markdown 文档。软件主要供个人独立使用，强调操作便捷性和稳定性。

## 1.1 目标与非目标
### 目标
- 支持批量导入 PDF/图片，按队列顺序处理。
- 输出 1 个总 Markdown：`merged_result.md`，且图片可在本地正常显示（Markdown 引用图片路径有效）。
- 默认“稳定优先”：串行请求、可重试、不中断队列、可断点续跑。
- Windows 免安装 `.exe`。

### 非目标（v0.1 不做）
- 不做账号系统/云同步/多人协作。
- 不做复杂编辑器（仅提供打开输出/预览能力）。
- 不做人工校对工作流。

## 2. 核心功能需求

### 2.1 文件处理
- **支持格式**: 图片（JPG/PNG/BMP 等）与 PDF。
- **批量导入**: 支持多选文件或文件夹拖拽导入。
- **队列顺序**: 默认按导入顺序串行处理（并发=1），避免并发过高导致失败。
- **列表管理**: 支持移除单项/清空列表（拖拽排序可选，若成本高可 v0.2）。

### 2.2 识别与转换
- **API 对接**: 对接 PaddleOCR-VL-1.5 Serving API（`POST /layout-parsing`）。
- **识别粒度**:
  - 图片：单次请求（`fileType=1`）。
  - PDF：默认按“分段 PDF”处理（见 2.4），每段仍用 `fileType=0`。
- **MD 生成**: 将识别到的文本、表格、公式等结构化内容保存为 `.md` 文件。

### 2.3 输出管理（必须带图片）
- **默认输出目录**: `E:\\output\\`（可在设置中修改）。
- **输出结构**: 每个输入文件创建同名子目录：
  - 主文档：`E:\\output\\<input_stem>\\merged_result.md`
  - 图片资源：按接口返回 `markdown.images` 的相对路径落盘（例如 `E:\\output\\<input_stem>\\images\\...`）
- **图片来源**:
  - `markdown.images` 为 `dict`：`{ "relative/path.png": "<image-url>" }`
  - 客户端需下载 `image-url` 内容并保存到 `relative/path.png` 对应位置

### 2.4 大 PDF 分段策略（默认启用，稳定优先）
背景：Serving 端通常对“单次输入图片数/页数”有上限（常见为 100 页），且超大 PDF 单次请求容易超时。

- **默认分段大小**: 80 页/段（可配置）。
- **处理流程**:
  1. 本机将原 PDF 按 80 页拆分为多个 `part_XXX.pdf`（不做逐页渲染成图片）。
  2. 逐段调用 `/layout-parsing`（`fileType=0`）。
  3. 按段顺序、页顺序合并为一个 `merged_result.md`（页间插入固定分隔符）。
- **失败处理**:
  - 分段失败不影响其他段/其他文件；失败段可自动重试。
  - 可选降级：同一段连续失败时，自动将该段再二分（例如 80 → 40 → 20）提升成功率（v0.2，可先只提供手动调整分段大小）。

### 2.5 断点续跑（必须）
- 每个输入文件在输出目录写入 `task_state.json`，记录：
  - 分段信息（段号、起止页、是否完成、耗时、失败原因）
  - 已下载图片清单/数量
  - 生成的 `merged_result.md` 状态（是否完成）
- 软件重启后：
  - 默认继续未完成任务（至少“跳过已完成分段/文件”）
  - 允许用户选择“从头重跑/继续”

### 2.6 UI 需求（v0.1 必须）
- **极简界面**:
  - 文件拖拽/选择区
  - 任务列表（文件名、状态：等待/进行中/完成/失败）
  - 进度条与当前日志（可折叠）
- **队列控制**:
  - 开始 / 暂停 / 继续 / 取消当前任务
  - 失败项“一键重试”
  - “打开输出目录 / 打开 merged_result.md”

### 2.7 非功能需求
- **稳定性**:
  - 串行请求为默认策略
  - 网络超时、5xx、429 等支持重试（见 5.2）
- **交付**: Windows `.exe`（无需安装 Python 环境）。
- **隐私**: 本地运行，仅将数据发送至用户配置的 Serving API。
- **可观测性**: 本地日志（按天或按任务），包含每文件/每分段：耗时、HTTP 状态码、失败原因。

## 3. 技术架构方案

### 3.1 技术栈
- **语言**: Python 3.10+
- **GUI**: PySide6
- **HTTP**: `requests`
- **PDF 拆分**: `pypdf`（或 `PyPDF2`）
- **打包**: PyInstaller（优先）或 Nuitka

### 3.2 模块划分（建议）
- **core/**: 队列、任务状态机、重试、断点续跑、日志
- **adapters/**: API 调用层（layout-parsing / restructure-pages）
- **pdf/**: PDF 分段拆分工具
- **md/**: Markdown 合并与图片落盘、路径改写
- **ui/**: PySide6 界面与交互

### 3.3 API 契约（可直接实现）
#### 3.3.1 `POST /layout-parsing`
- **Endpoint（示例）**: `https://aascabmaeaz1g4p7.aistudio-app.com/layout-parsing`
- **Headers**:
  - `Authorization: token <TOKEN>`
  - `Content-Type: application/json`
- **Request Body（必填）**:
  - `file`: base64 字符串（本地文件 bytes → base64）
  - `fileType`: `0=PDF`，`1=图片`
- **Request Body（可选）**:
  - `useDocOrientationClassify`: bool
  - `useDocUnwarping`: bool
  - `useChartRecognition`: bool
- **Response（关键字段）**:
  - `response.json()["result"]["layoutParsingResults"]`: list
    - 每个元素包含：
      - `markdown.text`: string
      - `markdown.images`: dict（`{ "relative/path.png": "<image-url>" }`）
      - `outputImages`: dict（可选，用于调试/可视化）

#### 3.3.2 `POST /restructure-pages`（可选，v0.2）
- 作用：跨页表格合并、标题层级识别、页面拼接、Markdown 美化等。
- v0.1 策略：先不依赖该接口，直接按页拼接 `markdown.text`。

### 3.4 Serving 端页数限制（部署侧说明）
若你是服务拥有者且可编辑产线配置，可通过以下配置解除输入图片数限制（通常等价于 PDF 页数上限）：

```yaml
Serving:
  extra:
    max_num_input_imgs: null
```

注意：解除限制会显著增加服务端耗时与资源占用；客户端仍建议默认按 80 页分段以提升稳定性与可恢复性。

## 4. 交互流程 (User Flow)
1. 打开软件（双击 EXE）。
2. 拖拽或选择文件（可多选 PDF/图片）。
3. 任务列表显示所有文件，状态为“等待中”。
4. 点击“开始识别”。
5. 处理中：
   - 单图：上传/识别/落盘，更新状态与耗时。
   - 大 PDF：自动拆分为多个 80 页分段，逐段处理；进度显示“当前段/总段 + 当前页/总页（若可得）”。
6. 异常：
   - 单段失败：标记失败原因，自动重试；超过重试次数继续处理下一段/下一个文件。
7. 全部完成：提示“处理完成”，可一键打开输出目录。

## 5. 关键策略（实现细则）
### 5.1 合并与图片落盘（必须）
- 合并顺序：按“分段顺序 → 页顺序”拼接 `markdown.text`。
- 页分隔：页与页之间插入固定分隔符 `\n\n---\n\n`（可配置）。
- 图片落盘：遍历 `markdown.images`：
  - `img_path` 为相对路径（例如 `images/xxx.png`）
  - `img` 为图片 URL：使用 HTTP GET 下载并写入 `output_dir/img_path`

### 5.2 重试与错误分类（必须）
- 超时：连接超时、读取超时分别可配置（例如 10s/120s）。
- 可重试：网络错误、超时、5xx、429（指数退避：1s/2s/4s + 抖动）。
- 不可重试：鉴权失败（401/403）、参数错误（4xx 其他）、文件不可读/格式不支持。
- 默认重试次数：3（可配置）。

### 5.3 并发与限流（默认稳定优先）
- 默认并发=1。
- 可选：每次请求间加入最小间隔（例如 200~500ms，可配置）。

## 6. 配置项（v0.1 必须可配置）
- `API_URL`（例如 `https://.../layout-parsing`）
- `TOKEN`
- `OUTPUT_DIR`（默认 `E:\\output\\`）
- `PDF_CHUNK_PAGES`（默认 80）
- `MAX_RETRIES`（默认 3）
- `CONNECT_TIMEOUT_S` / `READ_TIMEOUT_S`

## 7. 验收标准（v0.1）
1. 支持拖拽/多选导入 ≥ 50 个文件，按列表顺序串行处理。
2. PDF 默认按 80 页/段拆分并依次请求；设置中可修改分段大小。
3. 对单个 500 页 PDF：最终生成 1 个 `merged_result.md`，且 Markdown 内图片均能正常显示（图片文件已落盘）。
4. 任意任务失败不阻断队列：失败原因可见，且可一键重试。
5. 支持暂停/继续/取消当前任务；取消不导致程序崩溃或输出目录损坏。
6. 关闭软件后再次打开可继续未完成任务（至少“跳过已完成分段/文件”）。
