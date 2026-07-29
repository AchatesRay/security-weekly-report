import json
import time
import os
from pathlib import Path

from . import atomic_write, load_secrets

DATA_DIR = Path("data")
CONFIG_PATH = Path("config/settings.json")
ENHANCED_ITEMS_PATH = DATA_DIR / "enhanced_items.json"
TRANSLATED_ITEMS_PATH = DATA_DIR / "translated_items.json"
TRANSLATION_STATUS_PATH = DATA_DIR / "translation_status.json"

_cache: dict[str, str] = {}


def _is_chinese(text: str) -> bool:
    """检测文本是否主要是中文"""
    if not text:
        return False
    chinese_chars = sum(1 for c in text if '一' <= c <= '鿿')
    return chinese_chars > len(text) * 0.3 if text else False


def _load_translate_config() -> dict:
    try:
        with open(CONFIG_PATH, encoding="utf-8") as f:
            cfg = json.load(f)
        return cfg.get("translate", {})
    except Exception:
        return {}


_translate_cfg = _load_translate_config()
TRANSLATE_TIMEOUT = _translate_cfg.get("timeout", 8)


def _get_tencent_creds() -> tuple[str, str]:
    """从 secrets.json 读取腾讯云翻译密钥，兼容环境变量回退"""
    secrets = load_secrets()
    sid = secrets.get("tmt_secret_id") or os.environ.get("TMT_SECRET_ID") or os.environ.get("TENCENT_SECRET_ID")
    key = secrets.get("tmt_secret_key") or os.environ.get("TMT_SECRET_KEY") or os.environ.get("TENCENT_SECRET_KEY")
    return sid or "", key or ""


def _call_free_translate(text: str) -> str | None:
    """使用 MyMemory API 免费翻译"""
    try:
        from deep_translator import MyMemoryTranslator
        result = MyMemoryTranslator(source="en-GB", target="zh-CN").translate(text[:500])
        if result and result != text:
            return result
        return None
    except Exception:
        return None


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
        print(f"[TRANSLATOR] 腾讯云翻译失败: {e}")
        return None


def translate_text(text: str) -> str:
    """使用腾讯云翻译英文文本到中文，失败时返回原文"""
    if not text or len(text.strip()) == 0:
        return text
    key = f"tr:{text[:200]}"
    if key in _cache:
        return _cache[key]

    src = text[:1500]
    result = _call_tencent_translate(src)

    if result is not None and result != src:
        _cache[key] = result
        return result

    return text


def translate_all() -> list[dict]:
    with open(ENHANCED_ITEMS_PATH, "r", encoding="utf-8") as f:
        items = json.load(f)

    en_items = [i for i in items if i.get("language") == "en"]
    other_items = [i for i in items if i.get("language") != "en"]
    total = len(en_items)
    print(f"[TRANSLATOR] 需翻译: {total} 条英文内容")

    from datetime import datetime as dt
    start = dt.now()
    translated_count = 0
    ai_translated = 0

    for idx, item in enumerate(en_items):
        title = item.get("title", "")
        summary = item.get("original_summary") or item.get("summary", "")

        item["title_zh"] = translate_text(title)
        time.sleep(0.22)
        if len(summary) > 500:
            item["summary_zh"] = translate_text(summary[:500])
        else:
            item["summary_zh"] = translate_text(summary)

        if item["title_zh"] != title:
            translated_count += 1

        if (idx + 1) % 10 == 0:
            elapsed = (dt.now() - start).total_seconds()
            print(f"  [TRANSLATOR] 进度: {idx+1}/{total} ({elapsed:.0f}s)")

        time.sleep(0.22)

    # 检查所有条目的 ai_summary，英文则翻译（已按 4 QPS 限速）
    for item in items:
        ai_summary = item.get("ai_summary", "")
        if ai_summary and not _is_chinese(ai_summary):
            translated = translate_text(ai_summary)
            time.sleep(0.22)
            if translated and translated != ai_summary:
                item["ai_summary_zh"] = translated
                ai_translated += 1
            else:
                item["ai_summary_zh"] = ai_summary
        else:
            item["ai_summary_zh"] = ai_summary

    all_items = en_items + other_items
    elapsed = (dt.now() - start).total_seconds()
    print(f"[TRANSLATOR] 翻译完成: {total} 条, 其中成功 {translated_count} 条, "
          f"AI 摘要翻译 {ai_translated} 条, 耗时 {elapsed:.0f}s")

    atomic_write(TRANSLATED_ITEMS_PATH, all_items, indent=2)

    return all_items


def run():
    """翻译入口：检查腾讯云翻译可用性，不可用时跳过并记录状态"""
    secret_id, secret_key = _get_tencent_creds()
    available = bool(secret_id and secret_key)

    if not available:
        print("[TRANSLATOR] 腾讯云翻译未配置（需设置 TMT_SECRET_ID/TMT_SECRET_KEY）")
        print("[TRANSLATOR] 跳过翻译阶段，原文保留")
        # 直接将增强数据透传为翻译数据
        with open(ENHANCED_ITEMS_PATH, "r", encoding="utf-8") as f:
            items = json.load(f)
        atomic_write(TRANSLATED_ITEMS_PATH, items, indent=2)
        atomic_write(TRANSLATION_STATUS_PATH, {
            "status": "unavailable",
            "message": "腾讯云翻译API未配置，部分内容显示为英文原文",
        })
        return

    # 翻译可用，执行翻译
    translate_all()
    atomic_write(TRANSLATION_STATUS_PATH, {"status": "ok"})


if __name__ == "__main__":
    run()
