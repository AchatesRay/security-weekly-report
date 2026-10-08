"""翻译模块 — 非中文摘要/标题 → 中文（腾讯云 TMT）

2026-09-29 修复：
  1. **缓存键与翻译内容不一致**：缓存键取 `text[:200]`，实际翻译 `text[:1500]`。
     前缀相同、后段不同的两段文本会命中同一条缓存，返回错误译文。现在缓存
     键就是被翻译的完整文本。
  2. **只翻译 language == "en" 的条目**：配置里存在 `fr`（CERT-FR）、
     `hr`（CERT.hr）等信源，这些内容永远保持外语进入中文周报。现在改为
     按“文本是否已是中文”判断，与 language 标记解耦。
  3. **降级策略不明确**：翻译失败与翻译成功都返回原文，无法区分。现在按条目
     记录翻译结果，并把统计写入 translation_status.json，供报告如实展示。
  4. **死代码**：`_call_free_translate` 从未被调用，且依赖未列入 requirements。
     已移除。翻译不可用时走明确的降级路径，而不是留一段永远不执行的代码。
"""

import hashlib
import json
import os
import time

from ..utils import (DATA_DIR, SETTINGS_PATH, atomic_write, load_secrets)

ENHANCED_ITEMS_PATH = DATA_DIR / "enhanced_items.json"
TRANSLATED_ITEMS_PATH = DATA_DIR / "translated_items.json"
TRANSLATION_STATUS_PATH = DATA_DIR / "translation_status.json"

# 每条翻译请求之间的最小间隔（腾讯云 TMT 默认 5 QPS，留出余量）
REQUEST_INTERVAL = 0.22
# 单条送去翻译的最大字符数
MAX_TRANSLATE_CHARS = 1500

_cache: dict[str, str] = {}
_stats = {"calls": 0, "cache_hits": 0, "failures": 0}


def _is_chinese(text: str) -> bool:
    """检测文本是否主要是中文（>30% 字符为中文）"""
    if not text:
        return False
    chinese_chars = sum(1 for c in text if '\u4e00' <= c <= '\u9fff')
    return chinese_chars > len(text) * 0.3


def needs_translation(text: str) -> bool:
    """需要翻译 = 有内容且不是中文（不再依赖 language 标记）"""
    return bool(text and text.strip()) and not _is_chinese(text)


def _load_translate_config() -> dict:
    try:
        with open(SETTINGS_PATH, encoding="utf-8") as f:
            cfg = json.load(f)
        return cfg.get("translate", {})
    except Exception:
        return {}


_translate_cfg = _load_translate_config()
try:
    TRANSLATE_TIMEOUT = int(_translate_cfg.get("timeout", 8))
except (TypeError, ValueError):
    TRANSLATE_TIMEOUT = 8


def _get_tencent_creds() -> tuple[str, str]:
    """从 config/secrets.json 读取腾讯云密钥，回退到环境变量。

    注意：配置管理后台不再保存密钥（历史上曾写入 config/settings.json，
    已移除）。密钥来源只有 secrets.json 与环境变量两处。
    """
    secrets = load_secrets()
    sid = (secrets.get("tmt_secret_id")
           or os.environ.get("TMT_SECRET_ID")
           or os.environ.get("TENCENT_SECRET_ID"))
    key = (secrets.get("tmt_secret_key")
           or os.environ.get("TMT_SECRET_KEY")
           or os.environ.get("TENCENT_SECRET_KEY"))
    return sid or "", key or ""


def translation_available() -> bool:
    sid, key = _get_tencent_creds()
    return bool(sid and key)


def _call_tencent_translate(text: str) -> str | None:
    """腾讯云翻译（TMT），失败返回 None"""
    secret_id, secret_key = _get_tencent_creds()
    if not secret_id or not secret_key:
        return None
    try:
        from tencentcloud.common import credential
        from tencentcloud.common.profile.client_profile import ClientProfile
        from tencentcloud.common.profile.http_profile import HttpProfile
        from tencentcloud.tmt.v20180321 import tmt_client, models

        cred = credential.Credential(secret_id, secret_key)
        httpProfile = HttpProfile()
        httpProfile.reqTimeout = TRANSLATE_TIMEOUT
        clientProfile = ClientProfile()
        clientProfile.httpProfile = httpProfile

        client = tmt_client.TmtClient(cred, "ap-guangzhou", clientProfile)
        req = models.TextTranslateRequest()
        req.SourceText = text
        req.Source = "en"
        req.Target = "zh"
        req.ProjectId = 0

        resp = client.TextTranslate(req)
        return resp.TargetText
    except Exception as e:
        _stats["failures"] += 1
        print(f"[TRANSLATOR] 腾讯云翻译失败: {e}")
        return None


def _translate_one(text: str) -> tuple[str, bool]:
    """翻译一段文本，返回 (结果文本, 是否真的翻译成功)。

    缓存键使用被翻译文本的摘要，键与内容严格对应（旧实现用 text[:200] 作键
    却翻译 text[:1500]，会造成不同文本互相串味）。
    """
    if not text or not text.strip():
        return text, False

    payload = text[:MAX_TRANSLATE_CHARS]
    cache_key = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    if cache_key in _cache:
        _stats["cache_hits"] += 1
        return _cache[cache_key], True

    result = _call_tencent_translate(payload)
    _stats["calls"] += 1
    # 仅在真正发起请求后限速，缓存命中不再空等
    time.sleep(REQUEST_INTERVAL)

    if result is not None and result.strip() and result != payload:
        _cache[cache_key] = result
        return result, True
    return text, False


def translate_text(text: str) -> str:
    """翻译入口：翻译失败时返回原文（调用方可用 translate_text_ex 区分）"""
    return _translate_one(text)[0]


def translate_text_ex(text: str) -> tuple[str, bool]:
    """返回 (译文或原文, 是否翻译成功)"""
    return _translate_one(text)


def translate_all() -> list[dict]:
    if not ENHANCED_ITEMS_PATH.exists():
        raise FileNotFoundError(
            f"缺少上一步产物 {ENHANCED_ITEMS_PATH}，无法翻译（请检查摘要步骤）")

    with open(ENHANCED_ITEMS_PATH, "r", encoding="utf-8") as f:
        items = json.load(f)

    from datetime import datetime as dt
    start = dt.now()

    title_total = title_ok = 0
    summary_total = summary_ok = 0
    ai_total = ai_ok = 0

    for idx, item in enumerate(items):
        # ── 标题 ──
        title = item.get("title", "") or ""
        if needs_translation(title):
            title_total += 1
            translated, ok = translate_text_ex(title)
            item["title_zh"] = translated
            item["title_translated"] = ok
            if ok:
                title_ok += 1
        else:
            item["title_zh"] = title
            item["title_translated"] = True

        # ── 摘要（优先翻译原始短摘要；全文替换过的条目用 original_summary）──
        summary = item.get("original_summary") or item.get("summary", "") or ""
        if needs_translation(summary):
            summary_total += 1
            translated, ok = translate_text_ex(summary)
            item["summary_zh"] = translated
            item["summary_translated"] = ok
            if ok:
                summary_ok += 1
        else:
            item["summary_zh"] = summary
            item["summary_translated"] = True

        if (idx + 1) % 10 == 0:
            elapsed = (dt.now() - start).total_seconds()
            print(f"  [TRANSLATOR] 进度: {idx+1}/{len(items)} ({elapsed:.0f}s)")

    # ── AI 摘要 ──
    for item in items:
        ai_summary = item.get("ai_summary", "") or ""
        if needs_translation(ai_summary):
            ai_total += 1
            translated, ok = translate_text_ex(ai_summary)
            item["ai_summary_zh"] = translated
            item["ai_summary_translated"] = ok
            if ok:
                ai_ok += 1
        else:
            item["ai_summary_zh"] = ai_summary
            item["ai_summary_translated"] = True

    elapsed = (dt.now() - start).total_seconds()
    untranslated = (title_total - title_ok) + (summary_total - summary_ok) + (ai_total - ai_ok)
    print(f"[TRANSLATOR] 翻译完成: 标题 {title_ok}/{title_total}, "
          f"摘要 {summary_ok}/{summary_total}, AI 摘要 {ai_ok}/{ai_total}, "
          f"失败 {untranslated} 处, 耗时 {elapsed:.0f}s"
          f"（API 调用 {_stats['calls']} 次，缓存命中 {_stats['cache_hits']} 次）")

    atomic_write(TRANSLATED_ITEMS_PATH, items, indent=2)

    return items, {
        "title_total": title_total, "title_ok": title_ok,
        "summary_total": summary_total, "summary_ok": summary_ok,
        "ai_total": ai_total, "ai_ok": ai_ok,
        "untranslated": untranslated,
        "api_calls": _stats["calls"],
        "cache_hits": _stats["cache_hits"],
        "failures": _stats["failures"],
    }


def run():
    """翻译入口：检查腾讯云翻译可用性，不可用时跳过并如实记录状态"""
    if not ENHANCED_ITEMS_PATH.exists():
        raise FileNotFoundError(
            f"缺少上一步产物 {ENHANCED_ITEMS_PATH}，无法翻译（请检查摘要步骤）")

    if not translation_available():
        print("[TRANSLATOR] 腾讯云翻译未配置（需设置 TMT_SECRET_ID/TMT_SECRET_KEY，"
              "或写入 config/secrets.json）")
        print("[TRANSLATOR] 跳过翻译阶段，非中文内容将保持原文")
        with open(ENHANCED_ITEMS_PATH, "r", encoding="utf-8") as f:
            items = json.load(f)
        atomic_write(TRANSLATED_ITEMS_PATH, items, indent=2)
        atomic_write(TRANSLATION_STATUS_PATH, {
            "status": "unavailable",
            "message": "腾讯云翻译API未配置，非中文内容将显示原文",
            "untranslated": len(items),
        })
        return items

    items, stats = translate_all()
    status = "ok" if stats["untranslated"] == 0 else "partial"
    atomic_write(TRANSLATION_STATUS_PATH, {
        "status": status,
        "message": ("" if stats["untranslated"] == 0 else
                    f"有 {stats['untranslated']} 处内容翻译失败，已保留原文"),
        **stats,
    })
    if stats["untranslated"]:
        print(f"[TRANSLATOR] 警告：{stats['untranslated']} 处内容未能翻译，报告中将显示原文")
    return items


if __name__ == "__main__":
    run()
