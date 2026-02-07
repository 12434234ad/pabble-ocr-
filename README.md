# Pabble OCR（PaddleOCR-VL 客户端）v0.1

本项目用于对接已部署的 PaddleOCR-VL Serving API，将本地 PDF/图片批量识别并输出为带图片的 Markdown。

## 运行（开发态）

```bash
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\\Scripts\\activate
pip install -r requirements.txt
python -m pabble_ocr
```

首次启动后请在“设置”里填写：
- `API_URL`：例如 `https://.../layout-parsing`
- `TOKEN`：`Authorization: token <TOKEN>`
- `OUTPUT_DIR`：默认 `E:\\output\\`

图表图片出现“歪斜/变形”时，通常是 Serving 端在裁剪/矫正阶段生成的图片本身就带透视畸变；可在设置里尝试：
- `layoutShapeMode=rect`（优先推荐，避免四边形透视裁剪）
- 关闭 `useDocUnwarping`（若不需要去弯曲/去畸变）
- 开启 `useDocOrientationClassify`（先做方向纠正）

分页与结构优化相关参数（不同 Serving 版本支持程度可能不同）：
- `PAGE_SEPARATOR`：控制页与页之间插入的分隔符（可设为空，或改成 `\\n\\n`）
- `INSERT_PAGE_NUMBERS`：在每页前插入页码标记（`<!-- page:N -->` + `**第 N 页**`），避免在预览/转换时“看不到页码”
- `restructurePages / mergeTables / relevelTitles / prettifyMarkdown`：由服务端做跨页重排与 Markdown 美化
- `concatenatePages`：通过调用 `/restructure-pages` 将多页结果合并为更连贯的 Markdown（需要服务端支持该路由）
- `promptLabel`：当 `useLayoutDetection=false` 时可选（ocr/formula/table/chart），用于告诉服务端本次更偏向哪类任务
- `layoutMergeBboxesMode`：版面检测重叠框过滤（large/small/union）。若图片被拆得很碎，可尝试 `large`（优先保留外部最大框）
- 说明：当 `useLayoutDetection=false` 时，客户端不会再下发 `layoutMergeBboxesMode/layoutShapeMode` 等版面参数，避免部分 Serving 实现误触发裁剪/过滤导致“识别不全/只识别局部”
- `MERGE_IMAGE_FRAGMENTS`：观感优先的本地后处理——当服务端把“一张大图”拆成多张小图时，客户端会依据 `prunedResult` 的图片区域坐标把碎片重新拼成一张图，并在 Markdown 中替换引用（合并图输出到 `images/merged/`）
- 兼容：若服务端未返回 `prunedResult`，但图片文件名形如 `img_in_image_box_<x0>_<y0>_<x1>_<y1>.*`，也会从文件名解析 bbox 来合并（需要图片已下载到本地）
- `MD_IMAGE_WIDTH_PERCENT`：Markdown 图片缩放（0=不处理；50~80 通常更接近“PDF 一页内可展示”的效果）。用于解决部分 Markdown/PDF/Word 转换链路“按原始像素尺寸渲染图片导致过大”的问题
- `MD_IMAGE_MAX_HEIGHT_PX`：Markdown 图片最大高度（0=不限制；EPUB 建议 600~900）。用于避免重排阅读器把“过高图片”分页切成多屏/多页

大 PDF（几百页）处理建议：
- 先把 `PDF_CHUNK_PAGES` 调小（例如 20~40），降低单次请求耗时与超时风险
- 把 `READ_TIMEOUT_S` 调大（例如 600 或更高），避免服务端处理较久时客户端提前超时
- 若遇到 ReadTimeout：默认不会自动重试（避免重复请求/重复扣费风险），但会生成带“失败占位”的 `merged_result.md` 便于定位缺页；调整参数后可直接续跑失败分段
- 指定分段重打（文档 OCR）：可设置 `PDF_RERUN_SEGMENTS=002`（兼容 `2`），仅重跑 `part_002_pxxxx-yyyy` 命中的分段；非目标分段会跳过并优先复用历史产物，完成后自动重合并 `merged_result.md`
- 极个别页出现“漏字/只识别到部分文本”时：可用 `PDF_IMAGE_OCR_PAGES=15,18-20` 指定页做本地高 DPI 渲染后以“图片模式”重跑并替换该页输出（会增加额外请求，建议只填问题页）
- `PDF_IMAGE_OCR_PAGES` 不会触发“全部已完成分段重跑”；仅命中页所在分段会重跑，完成后自动重新合并 `merged_result.md`
- 对文件名包含页段的分卷 PDF（如 `part_007_p0391-0455.pdf`），`PDF_IMAGE_OCR_PAGES` 同时支持当前文件内页码（`1-65`）和原始绝对页码（`391-455`）
- 注意：`PDF_RERUN_SEGMENTS` 与 `PDF_IMAGE_OCR_PAGES` 不能同时填写；前者是“分段级重打”，后者是“页级图片补漏”

代理相关：
- 本工具底层使用 `requests`，默认会读取系统/环境代理（`HTTP_PROXY`/`HTTPS_PROXY` 等）；某些代理会导致 HTTPS 握手异常或超时
- 可在设置里关闭 `USE_SYSTEM_PROXY` 以直连服务端后再重试

队列会自动保存（下次启动会恢复），输出目录每个输入文件一个子目录，内含：
- `merged_result.md`
- 图片资源（按服务返回的相对路径落盘）
- `task_state.json`（断点续跑）

### 输出目录结构说明（很关键）

合并后的总 Markdown 固定在输出子目录根部：
- `...\\<input_stem>\\merged_result.md`

同时，为了支持断点续跑/定位缺页/后处理，本工具**会保留**分段中间产物在：
- `...\\<input_stem>\\_parts\\part_*.md`（分段 Markdown，数量=分段数）
- `...\\<input_stem>\\_parts\\imgs\\...` / `...\\<input_stem>\\_parts\\images\\...`（图片资源）

图片目录兼容说明（历史任务常见）：
- 旧任务可能是 `imgs/` + `merged/` 引用风格；
- 当前任务常见落盘为 `images/imgs/` + `images/merged/`；
- 合并时会自动做路径对齐，优先改写到可命中的真实文件路径。

如果你在资源管理器里打开了 `...\\_parts\\`，看到很多 `.md` 是正常的；请回到上一层目录查看 `merged_result.md`（或点击界面按钮“打开 merged_result.md”）。

### 修复：已有分段 md 但缺少 merged_result.md

一般情况下任务完成会自动生成 `merged_result.md`。如果因异常/坏配置等原因没有生成，可离线重建（不重跑 OCR）：

```bash
python -m pabble_ocr.tools.rebuild_merged_md "E:\\output\\<input_stem>"
```

也可以直接传 `_parts` 目录或 `task_state.json` 文件路径；批量扫描则加 `--recursive`：

```bash
python -m pabble_ocr.tools.rebuild_merged_md "E:\\output" --recursive --stale
```

Windows 也可直接把输出目录拖拽到 `rebuild_merged_md.bat` 上执行（需在项目目录已创建 `.venv` 并安装依赖）。

### EPUB 转换前校验（Pandoc 推荐）

先检查 `merged_result.md` 的图片引用是否都能命中本地文件：

```bash
python -m pabble_ocr.tools.check_markdown_assets "E:\\output\\<input_stem>\\merged_result.md"
```

- 返回码 `0`：全部命中，可继续转换；
- 返回码 `2`：存在缺图，请先修复路径或重建 merged。

通过校验后再执行 Pandoc（在 `merged_result.md` 所在目录执行）：

```bash
pandoc merged_result.md -o merged_result.epub --resource-path=".;./images;./_parts;./_parts/images;./_parts/imgs"
```

### B 方案（推荐）：导出包后交给 `md-epub`

适用场景：你使用第三方项目 `md-epub`（<https://github.com/12434234ad/md-epub>）做 EPUB 转换，并希望避免“手工搬文件导致缺图”。

关键原则（必须）：
- 不要把 `md` 和图片手工“摊平到同一目录”。
- `merged_result.md` 若引用了 `_parts/...`，就必须保留 `_parts` 目录层级。

一键流程（无需手工修目录）：
1. 用本工具 EXE 完成 OCR，得到 `...\\<input_stem>\\merged_result.md`。
2. 导出 `md-epub` 兼容包：

```bash
python -m pabble_ocr.tools.export_epub_pack "E:\\output\\<input_stem>"
```

可选参数：
- `--force`：覆盖已有 `*_epub_pack` 导出目录。
- `--out <dir>`：自定义导出目录。

Windows 可直接把任务目录拖到 `export_epub_pack.bat` 上（项目目录需已创建 `.venv` 并安装依赖）。

3. 将导出包内的 `merged_result.md`（即 `...\\<input_stem>_epub_pack\\merged_result.md`）拖给 `md-epub` 的 `convert.bat`。
4. 若仍提示缺图，先看导出包内 `export_report.json` 的 `post_check.missing_local_refs` 与 `warnings`。

说明：
- 导出包默认保持 `merged_result.md` 原文，仅复制命中的本地资源并保留层级（不摊平目录）。
- 导出后会自动再做一次资源校验，并输出 `export_report.json`。

## 打包（Windows EXE）

```bash
python -m venv .venv
.venv\\Scripts\\activate
python -m pip install -U pip
python -m pip install -r requirements.txt
python -m pip install pyinstaller
python -m PyInstaller -y --clean pyinstaller.spec
```

产物在 `dist/PabbleOCR/PabbleOCR.exe`（以 spec 为准）。
