from __future__ import annotations

import base64
import logging
import random
import time
from dataclasses import dataclass
from typing import Any, Optional

import requests

from pabble_ocr.config import AppConfig


logger = logging.getLogger(__name__)


class NonRetryableError(RuntimeError):
    pass


class RetryableError(RuntimeError):
    pass


@dataclass(frozen=True)
class LayoutParsingPage:
    markdown_text: str
    markdown_images: dict[str, str]
    pruned_result: Optional[dict[str, Any]] = None


@dataclass(frozen=True)
class LayoutParsingResult:
    pages: list[LayoutParsingPage]


def _b64_file(path: str) -> str:
    raw = open(path, "rb").read()
    return base64.b64encode(raw).decode("ascii")


def _is_retryable_status(status: int) -> bool:
    return status in (408, 429, 500, 502, 503, 504)

def _normalize_layout_shape_mode(value: str) -> str:
    v = (value or "").strip().lower()
    allowed = {"rect", "quad", "poly", "auto"}
    if v not in allowed:
        raise NonRetryableError(f"layoutShapeMode 不合法：{value!r}（允许：rect/quad/poly/auto）")
    return v

def _normalize_layout_merge_bboxes_mode(value: str) -> str:
    v = (value or "").strip().lower()
    allowed = {"large", "small", "union"}
    if v not in allowed:
        raise NonRetryableError(f"layoutMergeBboxesMode 不合法：{value!r}（允许：large/small/union）")
    return v


def _build_payload_options(config: AppConfig) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    prompt_label = None
    if getattr(config, "prompt_label", None) is not None and str(getattr(config, "prompt_label") or "").strip():
        prompt_label = str(getattr(config, "prompt_label")).strip()

    # promptLabel 仅在 useLayoutDetection=false 时有效；很多用户会只填 promptLabel 却忘了关 layoutDetection，
    # 导致服务端按版面检测输出（可能看起来“识别不全/只识别了部分区域”）。
    # 这里做一次“显式纠错”：当用户设置了 promptLabel 但未显式指定 useLayoutDetection 时，默认关闭版面检测以匹配用户意图。
    use_layout_detection = config.use_layout_detection
    if prompt_label and use_layout_detection is None:
        logger.warning("检测到 promptLabel=%r 但 useLayoutDetection 未设置；将自动设置 useLayoutDetection=false。", prompt_label)
        use_layout_detection = False
        payload["useLayoutDetection"] = False
    if config.use_doc_orientation_classify is not None:
        payload["useDocOrientationClassify"] = bool(config.use_doc_orientation_classify)
    if config.use_doc_unwarping is not None:
        payload["useDocUnwarping"] = bool(config.use_doc_unwarping)
    if config.use_chart_recognition is not None:
        payload["useChartRecognition"] = bool(config.use_chart_recognition)
    if use_layout_detection is not None and "useLayoutDetection" not in payload:
        payload["useLayoutDetection"] = bool(use_layout_detection)
    # 当 useLayoutDetection=false 时，版面检测相关参数对用户而言应“无效”。
    # 某些 Serving 实现可能仍会读取这些参数并触发裁剪/过滤，造成“只识别局部/识别不全”的错觉；
    # 因此客户端在 useLayoutDetection=false 时不再发送这些字段，避免误导服务端走版面逻辑。
    if payload.get("useLayoutDetection") is not False:
        if getattr(config, "layout_merge_bboxes_mode", None) is not None and str(getattr(config, "layout_merge_bboxes_mode") or "").strip():
            payload["layoutMergeBboxesMode"] = _normalize_layout_merge_bboxes_mode(str(getattr(config, "layout_merge_bboxes_mode")))
        if config.layout_shape_mode is not None and str(config.layout_shape_mode).strip():
            payload["layoutShapeMode"] = _normalize_layout_shape_mode(str(config.layout_shape_mode))
    if config.visualize is not None:
        payload["visualize"] = bool(config.visualize)
    if config.restructure_pages is not None:
        payload["restructurePages"] = bool(config.restructure_pages)
    if config.merge_tables is not None:
        payload["mergeTables"] = bool(config.merge_tables)
    if config.relevel_titles is not None:
        payload["relevelTitles"] = bool(config.relevel_titles)
    if config.prettify_markdown is not None:
        payload["prettifyMarkdown"] = bool(config.prettify_markdown)
    if config.show_formula_number is not None:
        payload["showFormulaNumber"] = bool(config.show_formula_number)

    if prompt_label and payload.get("useLayoutDetection") is False:
        payload["promptLabel"] = prompt_label

    # 兼容：部分 Serving 使用 snake_case 参数名；为避免“参数被忽略导致策略未生效”，这里同时下发一份 snake_case。
    # 注意：仅对已存在的字段做镜像，保证两套字段值完全一致，避免歧义。
    _mirror_snake_case(payload)
    return payload


def _mirror_snake_case(payload: dict[str, Any]) -> None:
    mapping = {
        "useDocOrientationClassify": "use_doc_orientation_classify",
        "useDocUnwarping": "use_doc_unwarping",
        "useChartRecognition": "use_chart_recognition",
        "useLayoutDetection": "use_layout_detection",
        "layoutMergeBboxesMode": "layout_merge_bboxes_mode",
        "layoutShapeMode": "layout_shape_mode",
        "restructurePages": "restructure_pages",
        "mergeTables": "merge_tables",
        "relevelTitles": "relevel_titles",
        "prettifyMarkdown": "prettify_markdown",
        "showFormulaNumber": "show_formula_number",
        "promptLabel": "prompt_label",
    }
    for camel, snake in mapping.items():
        if camel in payload and snake not in payload:
            payload[snake] = payload[camel]


def build_layout_parsing_options(config: AppConfig) -> dict[str, Any]:
    """
    对外提供：本次 layout-parsing 请求将携带的“选项字段”（不含 file/fileType 等大字段）。
    用于日志与调试落盘，也用于“参数变更自动重跑”的 fingerprint 计算。
    """
    return _build_payload_options(config)


class LayoutParsingClient:
    def __init__(self, config: AppConfig) -> None:
        self._config = config
        self._session = requests.Session()
        # 是否读取环境变量/系统代理配置（HTTP(S)_PROXY/NO_PROXY 等）
        self._session.trust_env = bool(getattr(config, "use_system_proxy", True))
        self._last_request_at: Optional[float] = None

    def layout_parsing(self, *, file_path: str, file_type: int) -> LayoutParsingResult:
        if not self._config.api_url:
            raise NonRetryableError("未配置 API_URL")
        if not self._config.token:
            raise NonRetryableError("未配置 TOKEN")

        payload: dict[str, Any] = {
            "file": _b64_file(file_path),
            "fileType": int(file_type),
        }
        # 兼容：部分 Serving 使用 snake_case（file_type）。
        payload["file_type"] = int(file_type)
        payload.update(_build_payload_options(self._config))

        headers = {
            "Authorization": f"token {self._config.token}",
            "Content-Type": "application/json",
        }

        attempt = 0
        while True:
            attempt += 1
            self._respect_min_interval()
            try:
                resp = self._session.post(
                    self._config.api_url,
                    json=payload,
                    headers=headers,
                    timeout=(self._config.connect_timeout_s, self._config.read_timeout_s),
                )
            except requests.RequestException as e:
                # ReadTimeout 场景下，服务端可能已经接收并在后台处理（甚至计费），客户端重试可能造成重复请求/扣费。
                if isinstance(e, requests.exceptions.ReadTimeout) and not bool(self._config.retry_on_read_timeout):
                    raise RetryableError(
                        "网络错误：Read timed out。为避免重复请求/重复扣费，本次不自动重试；"
                        "可提高 READ_TIMEOUT_S 后重试；或调小 PDF_CHUNK_PAGES 重新切分后重试（若当前仅 1 个 pdf_full 分段且未完成，会自动重新切分）。"
                    ) from e
                if attempt <= self._config.max_retries:
                    self._sleep_backoff(attempt)
                    continue
                raise RetryableError(f"网络错误：{e}") from e

            if resp.status_code in (401, 403):
                raise NonRetryableError(f"鉴权失败（HTTP {resp.status_code}）")
            if 400 <= resp.status_code < 500 and resp.status_code not in (408, 429):
                body = resp.text[:200]
                if resp.status_code == 404:
                    raise NonRetryableError(
                        "接口不存在（HTTP 404）："
                        f"{body}；请检查 API_URL 是否指向 layout-parsing 路由（例如以 `/layout-parsing` 结尾）。"
                    )
                raise NonRetryableError(f"请求参数/客户端错误（HTTP {resp.status_code}）：{body}")

            if resp.status_code >= 400:
                if _is_retryable_status(resp.status_code) and attempt <= self._config.max_retries:
                    self._sleep_backoff(attempt)
                    continue
                raise RetryableError(f"服务端错误（HTTP {resp.status_code}）：{resp.text[:200]}")

            try:
                data = resp.json()
            except Exception as e:
                if attempt <= self._config.max_retries:
                    self._sleep_backoff(attempt)
                    continue
                raise RetryableError("响应不是 JSON") from e

            try:
                pages_raw = data["result"]["layoutParsingResults"]
            except Exception as e:
                raise NonRetryableError("响应缺少 result.layoutParsingResults") from e

            pages = _parse_pages(pages_raw)

            return LayoutParsingResult(pages=pages)

    def restructure_pages(self, *, pages: list[LayoutParsingPage]) -> LayoutParsingResult:
        url = (self._config.restructure_api_url or "").strip() or _derive_restructure_url(self._config.api_url)
        if not url:
            raise NonRetryableError("未配置 RESTRUCTURE_API_URL，且无法从 API_URL 推导 /restructure-pages")

        body: dict[str, Any] = {"pages": []}
        if self._config.concatenate_pages is not None:
            body["concatenatePages"] = bool(self._config.concatenate_pages)
            body["concatenate_pages"] = bool(self._config.concatenate_pages)
        if self._config.merge_tables is not None:
            body["mergeTables"] = bool(self._config.merge_tables)
            body["merge_tables"] = bool(self._config.merge_tables)
        if self._config.relevel_titles is not None:
            body["relevelTitles"] = bool(self._config.relevel_titles)
            body["relevel_titles"] = bool(self._config.relevel_titles)
        if self._config.prettify_markdown is not None:
            body["prettifyMarkdown"] = bool(self._config.prettify_markdown)
            body["prettify_markdown"] = bool(self._config.prettify_markdown)
        if self._config.show_formula_number is not None:
            body["showFormulaNumber"] = bool(self._config.show_formula_number)
            body["show_formula_number"] = bool(self._config.show_formula_number)

        for p in pages:
            body["pages"].append(
                {
                    "prunedResult": p.pruned_result or {},
                    "markdownImages": p.markdown_images or {},
                }
            )

        headers = {
            "Authorization": f"token {self._config.token}",
            "Content-Type": "application/json",
        }

        attempt = 0
        while True:
            attempt += 1
            self._respect_min_interval()
            try:
                resp = self._session.post(
                    url,
                    json=body,
                    headers=headers,
                    timeout=(self._config.connect_timeout_s, self._config.read_timeout_s),
                )
            except requests.RequestException as e:
                if isinstance(e, requests.exceptions.ReadTimeout) and not bool(self._config.retry_on_read_timeout):
                    raise RetryableError(
                        "网络错误：Read timed out。为避免重复请求/重复扣费，本次不自动重试；"
                        "可提高 READ_TIMEOUT_S 后重试。"
                    ) from e
                if attempt <= self._config.max_retries:
                    self._sleep_backoff(attempt)
                    continue
                raise RetryableError(f"网络错误：{e}") from e

            if resp.status_code in (401, 403):
                raise NonRetryableError(f"鉴权失败（HTTP {resp.status_code}）")
            if 400 <= resp.status_code < 500 and resp.status_code not in (408, 429):
                body_text = resp.text[:200]
                raise NonRetryableError(f"请求参数/客户端错误（HTTP {resp.status_code}）：{body_text}")

            if resp.status_code >= 400:
                if _is_retryable_status(resp.status_code) and attempt <= self._config.max_retries:
                    self._sleep_backoff(attempt)
                    continue
                raise RetryableError(f"服务端错误（HTTP {resp.status_code}）：{resp.text[:200]}")

            try:
                data = resp.json()
            except Exception as e:
                if attempt <= self._config.max_retries:
                    self._sleep_backoff(attempt)
                    continue
                raise RetryableError("响应不是 JSON") from e

            try:
                pages_raw = data["result"]["layoutParsingResults"]
            except Exception as e:
                raise NonRetryableError("响应缺少 result.layoutParsingResults") from e

            return LayoutParsingResult(pages=_parse_pages(pages_raw))

    def _respect_min_interval(self) -> None:
        ms = int(self._config.request_min_interval_ms or 0)
        if ms <= 0:
            return
        now = time.time()
        if self._last_request_at is None:
            self._last_request_at = now
            return
        delta = now - self._last_request_at
        need = ms / 1000.0 - delta
        if need > 0:
            time.sleep(need)
        self._last_request_at = time.time()

    def _sleep_backoff(self, attempt: int) -> None:
        base = 2 ** max(0, attempt - 1)
        jitter = random.uniform(0.0, 0.3)
        time.sleep(min(30.0, base + jitter))


def _derive_restructure_url(api_url: str) -> str:
    u = (api_url or "").strip()
    if not u:
        return ""
    if u.endswith("/layout-parsing"):
        return u[: -len("/layout-parsing")] + "/restructure-pages"
    if u.endswith("/layout_parsing"):
        return u[: -len("/layout_parsing")] + "/restructure-pages"
    return u.rstrip("/") + "/restructure-pages"


def _parse_pages(pages_raw: Any) -> list[LayoutParsingPage]:
    pages: list[LayoutParsingPage] = []
    for page in pages_raw or []:
        if not isinstance(page, dict):
            continue
        md = page.get("markdown") or {}
        if not isinstance(md, dict):
            md = {}
        text = md.get("text") or ""
        images = md.get("images") or {}
        if not isinstance(images, dict):
            images = {}
        pruned = page.get("prunedResult")
        if pruned is not None and not isinstance(pruned, dict):
            pruned = None
        pages.append(
            LayoutParsingPage(
                markdown_text=str(text),
                markdown_images={str(k): str(v) for k, v in images.items()},
                pruned_result=pruned,
            )
        )
    return pages
