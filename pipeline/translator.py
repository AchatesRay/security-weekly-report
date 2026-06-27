import json
import time
import os
from pathlib import Path

DATA_DIR = Path("data")
CONFIG_PATH = Path("config/settings.json")
CLASSIFIED_ITEMS_PATH = DATA_DIR / "classified_items.json"
TRANSLATED_ITEMS_PATH = DATA_DIR / "translated_items.json"

_cache: dict[str, str] = {}


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
    """从环境变量或 config_settings.json 读取腾讯云密钥"""
    sid = os.environ.get("TENCENT_SECRET_ID")
    key = os.environ.get("TENCENT_SECRET_KEY")
    if sid and key:
        return sid, key
    cfg = _load_translate_config()
    return cfg.get("tencent_secret_id", ""), cfg.get("tencent_secret_key", "")


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
    """腾讯云翻译（TMT）"""
    secret_id, secret_key = _get_tencent_creds()
    if not secret_id or not secret_key:
        # 回退到免费翻译
        return _call_free_translate(text)
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
    with open(CLASSIFIED_ITEMS_PATH, "r", encoding="utf-8") as f:
        items = json.load(f)

    en_items = [i for i in items if i.get("language") == "en"]
    other_items = [i for i in items if i.get("language") != "en"]
    total = len(en_items)
    print(f"[TRANSLATOR] 需翻译: {total} 条英文内容")

    from datetime import datetime as dt
    start = dt.now()
    translated_count = 0
    consecutive_failures = 0

    for idx, item in enumerate(en_items):
        title = item.get("title", "")
        summary = item.get("original_summary") or item.get("summary", "")

        # 连续失败 5 次则跳过后续（被限流）
        if consecutive_failures < 5:
            item["title_zh"] = translate_text(title)
            if len(summary) > 500:
                item["summary_zh"] = translate_text(summary[:500])
            else:
                item["summary_zh"] = translate_text(summary)

            if item["title_zh"] != title:
                translated_count += 1
                consecutive_failures = 0
            else:
                consecutive_failures += 1
        else:
            item["title_zh"] = title

        if (idx + 1) % 10 == 0:
            elapsed = (dt.now() - start).total_seconds()
            print(f"  [TRANSLATOR] 进度: {idx+1}/{total} ({elapsed:.0f}s)"
                  f"{' [跳过剩余]' if consecutive_failures >= 5 else ''}")

        time.sleep(0.5)

    all_items = en_items + other_items
    elapsed = (dt.now() - start).total_seconds()
    print(f"[TRANSLATOR] 翻译完成: {total} 条, 其中成功 {translated_count} 条, "
          f"耗时 {elapsed:.0f}s")

    with open(TRANSLATED_ITEMS_PATH, "w", encoding="utf-8") as f:
        json.dump(all_items, f, ensure_ascii=False, indent=2)

    return all_items


def run():
    translate_all()


if __name__ == "__main__":
    run()
