"""翻译模块 — 非中文标题与摘要 → 中文（腾讯云 TMT / 百度翻译）

**翻译范围**：默认只翻译**标题**和**摘要**（报告 UI 里「摘要」一栏显示的
ai_summary，即抽取式摘要），**正文不翻译**。范围由 `config/settings.json`
的 `translate.fields` 控制，可选 `title` / `summary` / `ai_summary`。

**渠道选择**：`config/settings.json` 的 `translate.provider`，可选
`tencent`（腾讯云 TMT，默认）或 `baidu`（百度翻译开放平台）。密钥只从
`config/secrets.json` 或环境变量读取——腾讯云 `tmt_secret_id`/`tmt_secret_key`，
百度 `baidu_appid`/`baidu_key`。

2026-11-10（渠道可切换）：
  - 翻译调用改为**按 `translate.provider` 分发**，腾讯云/百度共用同一套
    缓存、重试、额度快停与统计逻辑；渠道差异收敛在 `PROVIDERS` 表里
    （单条字符上限、请求间隔、单价）。
  - 百度通用翻译 API 走 HTTP 表单 + MD5 签名（`appid+q+salt+密钥`），
    无需安装 SDK；标准版 1 QPS 且单条 1000 字符，参数已按渠道区分。
  - 源语言改为 `auto`（原先写死 `en`，法语/克罗地亚语信源会翻坏）。

2026-11-10（额度优化，解决"免费额度被重跑刷光"）：
  1. **译文落盘缓存** `data/state/translate_cache.json`：缓存键是
     `渠道:文本SHA256`。此前缓存只是进程内字典，进程结束即丢，每次
     `--skip-fetch` 重跑都会把全部文本重新计费一遍。现在重跑只补新增内容。
  2. **只翻最终上屏的条目**（`translate.only_displayed`，默认开）：展示规则复用
     `report_generator.select_display_items()`，未上报告的条目不送翻译。
     实测同一批数据 223 条全量需 4.6 万字符，上屏 109 条只需 3.1 万（-33%）。
  3. **失败重试**（`translate.retry_times`，默认 3，退避 1s/3s/7s）。
  4. **额度耗尽快停**：识别 `NoFreeAmount` / 百度 52003/54001/54004 等不可重试
     错误后立即停止后续调用（此前会逐条空打两百多次、刷满日志），
     状态文件记为 `quota_exhausted` / `auth_failed` / `sdk_missing`。
  5. 统计里新增本次消耗字符数与估算花费，便于盯着额度用。

2026-09-29 修复：
  1. **缓存键与翻译内容不一致**：缓存键取 `text[:200]`，实际翻译 `text[:1500]`。
     前缀相同、后段不同的两段文本会命中同一条缓存，返回错误译文。现在缓存
     键就是被翻译的完整文本（SHA256）。
  2. **只翻译 language == "en" 的条目**：配置里存在 `fr`（CERT-FR）、
     `hr`（CERT.hr）等信源，这些内容永远保持外语进入中文报告。现在改为
     按“文本是否已是中文”判断，与 language 标记解耦。
  3. **降级策略不明确**：翻译失败与翻译成功都返回原文，无法区分。现在按条目
     记录翻译结果，并把统计写入 translation_status.json，供报告如实展示。
  4. **死代码**：`_call_free_translate` 从未被调用，且依赖未列入 requirements。
     已移除。翻译不可用时走明确的降级路径，而不是留一段永远不执行的代码。
"""

import hashlib
import json
import os
import random
import time
from datetime import datetime as dt

from ..utils import (DATA_DIR, SETTINGS_PATH, STATE_DIR, TRANSLATE_CACHE_PATH,
                     atomic_write, load_secrets)

ENHANCED_ITEMS_PATH = DATA_DIR / "enhanced_items.json"
TRANSLATED_ITEMS_PATH = DATA_DIR / "translated_items.json"
TRANSLATION_STATUS_PATH = DATA_DIR / "translation_status.json"

# 各渠道参数：单条最大字符、请求间隔（按官方 QPS 留余量）、后付费单价
PROVIDERS = {
    "tencent": {
        "label": "腾讯云 TMT",
        "max_chars": 1500,
        "interval": 0.22,          # 默认 5 QPS
        "price_per_million": 58.0,
        "quota_note": "免费额度 500 万字符/月",
    },
    "baidu": {
        "label": "百度翻译",
        "max_chars": 1000,         # 标准版单次上限 1000 字符
        "interval": 1.5,           # 标准版 1 QPS，留足余量避免 54003 限流
        "price_per_million": 49.0,
        "quota_note": "标准版 5 万字符/月，实名升级后 100 万字符/月",
    },
}
DEFAULT_PROVIDER = "tencent"
BAIDU_API_URL = "https://fanyi-api.baidu.com/api/trans/vip/translate"

# 失败重试的等待时长（秒），按次数递增；仅对"可重试"的错误使用
RETRY_BACKOFF = (1.0, 3.0, 7.0)
# 出现这些标记说明重试没有意义，立即停手：
#   腾讯云 NoFreeAmount/鉴权类；百度 52003 未授权、54001 签名错、54004 余额不足、
#   58000 IP 非法、58002 服务关闭、90107 认证未生效；以及本地缺 SDK/版本不符
FATAL_MARKERS = ("NoFreeAmount", "UserNotRegistered", "ServiceIsolate",
                 "AuthFailure", "UnauthorizedOperation", "InvalidCredential",
                 "InsufficientBalance", "No module named", "has no attribute",
                 "52003", "54001", "54004", "58000", "58001", "58002", "90107")

# 可翻译的字段与其含义：
#   title      标题
#   summary    短摘要（RSS description；若被全文替换，则是被替换前的那段）
#   ai_summary 报告「摘要」栏显示的抽取式摘要
SUPPORTED_FIELDS = ("title", "summary", "ai_summary")
# 默认只翻标题与摘要，正文保持原文
DEFAULT_TRANSLATE_FIELDS = ("title", "ai_summary")

# 译文缓存：{缓存键: {"t": 译文, "ts": 写入时间, "hits": 命中次数}}
_cache: dict[str, dict] = {}
_cache_loaded = False
_cache_dirty = False
# 翻译失败的原因（仅记录第一处致命错误）
_fatal_reason: str | None = None
_fatal_code: str | None = None

_stats = {"calls": 0, "cache_hits": 0, "failures": 0, "chars": 0,
          "retries": 0, "skipped_not_displayed": 0}


def _is_chinese(text: str) -> bool:
    """检测文本是否已经是中文。

    旧规则只看“中文字符 > 30%”，会把「Linux内核IPv4 TCP释放后重用漏洞
    （CVE-2026-98432）通告」这类中英混排的标题当成外文送翻：百度识别出
    zh→zh 后原样返回，于是被记为“翻译失败”，报告里无鄂多出一个“原文”标记。
    现在改为：中文字符≥6 且占非空白字符 15% 即视为中文（真正的外文标题
    里偶尔夹一个中文词不会误判）。
    """
    if not text:
        return False
    chinese_chars = sum(1 for c in text if '\u4e00' <= c <= '\u9fff')
    compact = sum(1 for c in text if not c.isspace())
    if not compact:
        return False
    if chinese_chars >= 6 and chinese_chars / compact >= 0.15:
        return True
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


def fields_to_translate() -> tuple[str, ...]:
    """读取要翻译的字段集合（settings.json 的 translate.fields）"""
    raw = _load_translate_config().get("fields")
    if isinstance(raw, list):
        selected = tuple(f for f in raw if f in SUPPORTED_FIELDS)
        if selected:
            return selected
    return DEFAULT_TRANSLATE_FIELDS


_translate_cfg = _load_translate_config()
try:
    TRANSLATE_TIMEOUT = int(_translate_cfg.get("timeout", 8))
except (TypeError, ValueError):
    TRANSLATE_TIMEOUT = 8


def _cfg_int(key: str, default: int) -> int:
    try:
        return int(_translate_cfg.get(key, default))
    except (TypeError, ValueError):
        return default


def _cfg_bool(key: str, default: bool) -> bool:
    value = _translate_cfg.get(key, default)
    if isinstance(value, bool):
        return value
    return default


def active_provider() -> str:
    """当前生效的翻译渠道（settings.json 的 translate.provider）"""
    name = str(_translate_cfg.get("provider") or DEFAULT_PROVIDER).strip().lower()
    return name if name in PROVIDERS else DEFAULT_PROVIDER


def _provider_meta() -> dict:
    return PROVIDERS[active_provider()]


# ─────────────────────────── 密钥 ───────────────────────────

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


def _get_baidu_creds() -> tuple[str, str]:
    """百度翻译开放平台的 APP ID 与密钥（appid + 密钥，两者缺一不可）"""
    secrets = load_secrets()
    appid = (secrets.get("baidu_appid")
             or os.environ.get("BAIDU_APPID")
             or os.environ.get("BAIDU_APP_ID"))
    key = (secrets.get("baidu_key")
           or os.environ.get("BAIDU_KEY")
           or os.environ.get("BAIDU_SECRET_KEY"))
    return str(appid or ""), str(key or "")


def translation_available() -> bool:
    """当前渠道的凭据是否齐全"""
    if active_provider() == "baidu":
        appid, key = _get_baidu_creds()
        return bool(appid and key)
    sid, key = _get_tencent_creds()
    return bool(sid and key)


# ─────────────────────────── 译文缓存 ───────────────────────────

def _load_cache():
    """从 data/state/translate_cache.json 载入历史译文（只读一次）"""
    global _cache, _cache_loaded
    if _cache_loaded:
        return
    _cache_loaded = True
    if not _cfg_bool("cache_enabled", True):
        print("[TRANSLATOR] 译文缓存已关闭（translate.cache_enabled=false）")
        return
    if not TRANSLATE_CACHE_PATH.exists():
        return
    try:
        with open(TRANSLATE_CACHE_PATH, "r", encoding="utf-8") as f:
            raw = json.load(f)
        if isinstance(raw, dict):
            _cache = {k: v for k, v in raw.items()
                      if isinstance(v, dict) and isinstance(v.get("t"), str)}
        print(f"[TRANSLATOR] 译文缓存已载入: {len(_cache)} 条")
    except Exception as e:  # noqa: BLE001 — 缓存坏了不影响翻译，只影响省额度
        print(f"[TRANSLATOR] 译文缓存读取失败（按空缓存继续）: {type(e).__name__}: {e}")


def _save_cache():
    """把缓存写回磁盘（按最后使用时间保留最近的 N 条）"""
    global _cache, _cache_dirty
    if not _cfg_bool("cache_enabled", True) or not _cache_dirty:
        return
    limit = max(_cfg_int("cache_max_entries", 20000), 100)
    if len(_cache) > limit:
        kept = sorted(_cache.items(),
                      key=lambda kv: str(kv[1].get("ts") or ""), reverse=True)[:limit]
        _cache = dict(kept)
    try:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        # atomic_write 内部已固定 ensure_ascii=False，这里只覆盖分隔符（紧凑体积）
        atomic_write(TRANSLATE_CACHE_PATH, _cache, separators=(",", ":"))
        _cache_dirty = False
    except Exception as e:  # noqa: BLE001
        print(f"[TRANSLATOR] 译文缓存写入失败: {type(e).__name__}: {e}")


def _cache_key(payload: str) -> str:
    """缓存键带渠道前缀：换渠道不会命中另一家的旧译文"""
    return f"{active_provider()}:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


# ─────────────────────────── 渠道调用 ───────────────────────────

def _mark_fatal(err: str) -> bool:
    """识别不可重试的错误码；命中即记录原因并返回 True"""
    global _fatal_reason, _fatal_code
    for marker in FATAL_MARKERS:
        if marker in err:
            if _fatal_reason is None:
                _fatal_code = marker
                _fatal_reason = f"{_provider_meta()['label']} 返回 {marker}"
                print(f"[TRANSLATOR] 翻译通道不可用（{marker}），已停止后续调用：{err[:200]}")
            return True
    return False


def _call_tencent_translate(text: str) -> str | None:
    """腾讯云翻译（TMT），失败返回 None；致命错误记入 _fatal_reason"""
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
        req.Source = "auto"
        req.Target = "zh"
        req.ProjectId = 0

        resp = client.TextTranslate(req)
        return resp.TargetText
    except Exception as e:  # noqa: BLE001
        _stats["failures"] += 1
        err = f"{type(e).__name__}: {e}"
        if not _mark_fatal(err):
            print(f"[TRANSLATOR] 腾讯云翻译失败: {err[:200]}")
        return None


def _call_baidu_translate(text: str) -> str | None:
    """百度翻译通用 API（HTTP + MD5 签名），失败返回 None"""
    appid, secret = _get_baidu_creds()
    if not appid or not secret:
        return None
    try:
        import httpx

        salt = str(random.randint(32768, 65536))
        sign = hashlib.md5((appid + text + salt + secret).encode("utf-8")).hexdigest()
        payload = {"q": text, "from": "auto", "to": "zh",
                   "appid": appid, "salt": salt, "sign": sign}
        resp = httpx.post(BAIDU_API_URL, data=payload, timeout=TRANSLATE_TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:  # noqa: BLE001
        _stats["failures"] += 1
        err = f"{type(e).__name__}: {e}"
        if not _mark_fatal(err):
            print(f"[TRANSLATOR] 百度翻译请求失败: {err[:200]}")
        return None

    if data.get("error_code"):
        _stats["failures"] += 1
        err = f"{data.get('error_code')}: {data.get('error_msg', '')}"
        if not _mark_fatal(err):
            print(f"[TRANSLATOR] 百度翻译失败: {err[:200]}")
        return None

    parts = [r.get("dst", "") for r in (data.get("trans_result") or []) if r.get("dst")]
    return "\n".join(parts) if parts else None


def _call_provider(text: str) -> str | None:
    """按当前渠道分发翻译调用"""
    if active_provider() == "baidu":
        return _call_baidu_translate(text)
    return _call_tencent_translate(text)


def _translate_with_retry(payload: str) -> str | None:
    """带退避重试的翻译；致命错误（额度/鉴权/SDK）立即放弃"""
    retries = max(_cfg_int("retry_times", len(RETRY_BACKOFF)), 0)
    attempt = 0
    while True:
        if _fatal_reason:
            return None
        _stats["calls"] += 1
        try:
            result = _call_provider(payload)
        except Exception as e:  # noqa: BLE001 — 供应商实现抛异常不能拖垮管道
            _stats["failures"] += 1
            err = f"{type(e).__name__}: {e}"
            if not _mark_fatal(err):
                print(f"[TRANSLATOR] 翻译调用异常: {err[:200]}")
            result = None
        if result is not None and result.strip():
            if result != payload:
                return result
            # 渠道原样返回：原文本就是中文（如中英混排标题），直接当完成
            if sum(1 for c in payload if '\u4e00' <= c <= '\u9fff') >= 2:
                return result
        if result is not None:
            # 接口返回 200 但译文不可用（空串/原样返回）：按可重试失败处理，
            # 不把这种“假成功”写进报告，也不写进缓存。
            _stats["failures"] += 1
            print(f"[TRANSLATOR] 译文不可用（{'原样返回' if result == payload else '空'}），"
                  f"按失败重试：{payload[:40]!r}")
        if _fatal_reason or attempt >= retries:
            return None
        wait = RETRY_BACKOFF[min(attempt, len(RETRY_BACKOFF) - 1)]
        _stats["retries"] += 1
        attempt += 1
        print(f"[TRANSLATOR] 第 {attempt}/{retries} 次重试（等待 {wait:.0f}s）")
        time.sleep(wait)


def _translate_one(text: str) -> tuple[str, bool]:
    """翻译一段文本，返回 (结果文本, 是否真的翻译成功)。

    缓存键使用 `渠道:文本SHA256`，键与内容严格对应；缓存落盘，重跑不重复计费。
    """
    global _cache_dirty
    if not text or not text.strip():
        return text, False

    payload = text[:_provider_meta()["max_chars"]]
    key = _cache_key(payload)
    cached = _cache.get(key)
    if isinstance(cached, dict) and cached.get("t"):
        _stats["cache_hits"] += 1
        cached["hits"] = int(cached.get("hits") or 0) + 1
        cached["ts"] = dt.now().isoformat(timespec="seconds")
        _cache_dirty = True
        return cached["t"], True

    if _fatal_reason:
        return text, False

    _stats["chars"] += len(payload)
    result = _translate_with_retry(payload)
    # 仅在真正发起请求后限速，缓存命中不再空等
    if _stats["calls"]:
        time.sleep(_provider_meta()["interval"])

    if result is not None and result.strip() and result == payload and \
            sum(1 for c in payload if '\u4e00' <= c <= '\u9fff') >= 2:
        # 原文已是中文：缓存成“原文”、记为成功，下次不再白调一次
        _cache[key] = {"t": payload,
                       "ts": dt.now().isoformat(timespec="seconds"),
                       "hits": 0}
        _cache_dirty = True
        return payload, True

    if result is not None and result.strip() and result != payload:
        _cache[key] = {"t": result,
                       "ts": dt.now().isoformat(timespec="seconds"),
                       "hits": 0}
        _cache_dirty = True
        return result, True
    return text, False


def translate_text(text: str) -> str:
    """翻译入口：翻译失败时返回原文（调用方可用 translate_text_ex 区分）"""
    return _translate_one(text)[0]


def translate_text_ex(text: str) -> tuple[str, bool]:
    """返回 (译文或原文, 是否翻译成功)"""
    return _translate_one(text)


# ─────────────────────────── 主流程 ───────────────────────────

def _display_object_ids(items: list[dict]) -> set[int]:
    """返回"最终会上报告"的条目对象 id 集合（复用报告的展示规则）"""
    from .report_generator import select_display_items
    accepted, review = select_display_items(items)
    return {id(x) for x in accepted} | {id(x) for x in review}


def translate_all() -> tuple[list[dict], dict]:
    if not ENHANCED_ITEMS_PATH.exists():
        raise FileNotFoundError(
            f"缺少上一步产物 {ENHANCED_ITEMS_PATH}，无法翻译（请检查摘要步骤）")

    with open(ENHANCED_ITEMS_PATH, "r", encoding="utf-8") as f:
        items = json.load(f)

    start = dt.now()
    _load_cache()

    fields = fields_to_translate()
    only_displayed = _cfg_bool("only_displayed", True)
    visible_ids: set[int] | None = None
    if only_displayed:
        try:
            visible_ids = _display_object_ids(items)
        except Exception as e:  # noqa: BLE001 — 拿不到展示集就退回全量，保证有译文
            print(f"[TRANSLATOR] 无法确定上屏条目，本次全量翻译: {type(e).__name__}: {e}")

    def _displayed(item: dict) -> bool:
        return visible_ids is None or id(item) in visible_ids

    scope = ("仅上屏条目" if visible_ids is not None else "全部条目")
    print(f"[TRANSLATOR] 渠道: {_provider_meta()['label']}；翻译范围: {', '.join(fields)}"
          f"（{scope}，正文不翻译；范围可在 settings.json 的 translate.fields 调整）")

    title_total = title_ok = 0
    summary_total = summary_ok = 0
    ai_total = ai_ok = 0

    for idx, item in enumerate(items):
        selected = _displayed(item)
        if not selected:
            # 未上屏：保留原文、不加 _zh 字段（下游不会渲染这些条目）
            _stats["skipped_not_displayed"] += 1
            item["translate_skipped"] = True
            continue
        item.pop("translate_skipped", None)

        # ── 标题 ──
        title = item.get("title", "") or ""
        if "title" in fields and needs_translation(title):
            title_total += 1
            translated, ok = translate_text_ex(title)
            item["title_zh"] = translated
            item["title_translated"] = ok
            if ok:
                title_ok += 1
        else:
            item["title_zh"] = title
            item["title_translated"] = True

        # ── 短摘要（默认不翻译；正文保持原文）──
        if "summary" in fields:
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
        else:
            # 明确标记为未翻译，避免下游把原文误当作译文
            item.pop("summary_zh", None)
            item["summary_translated"] = False

        if (idx + 1) % 10 == 0:
            elapsed = (dt.now() - start).total_seconds()
            print(f"  [TRANSLATOR] 进度: {idx+1}/{len(items)} ({elapsed:.0f}s)")

    # ── AI 摘要（报告「摘要」栏显示的内容）──
    for item in items:
        if not _displayed(item):
            continue
        ai_summary = item.get("ai_summary", "") or ""
        if "ai_summary" in fields and needs_translation(ai_summary):
            ai_total += 1
            translated, ok = translate_text_ex(ai_summary)
            item["ai_summary_zh"] = translated
            item["ai_summary_translated"] = ok
            if ok:
                ai_ok += 1
        else:
            item["ai_summary_zh"] = ai_summary
            item["ai_summary_translated"] = True

    _save_cache()

    elapsed = (dt.now() - start).total_seconds()
    price = _provider_meta()["price_per_million"]
    untranslated = (title_total - title_ok) + (summary_total - summary_ok) + (ai_total - ai_ok)
    print(f"[TRANSLATOR] 翻译完成: 标题 {title_ok}/{title_total}, "
          f"AI 摘要 {ai_ok}/{ai_total}"
          + (f", 短摘要 {summary_ok}/{summary_total}" if "summary" in fields else
             "（短摘要/正文未翻译）")
          + f", 失败 {untranslated} 处, 耗时 {elapsed:.0f}s"
          f"（API 调用 {_stats['calls']} 次，缓存命中 {_stats['cache_hits']} 次，"
          f"跳过未上屏 {_stats['skipped_not_displayed']} 条，"
          f"消耗 {_stats['chars']:,} 字符 ≈ {_stats['chars'] / 1_000_000 * price:.3f} 元）")

    atomic_write(TRANSLATED_ITEMS_PATH, items, indent=2)

    return items, {
        "title_total": title_total, "title_ok": title_ok,
        "summary_total": summary_total, "summary_ok": summary_ok,
        "ai_total": ai_total, "ai_ok": ai_ok,
        "untranslated": untranslated,
        "api_calls": _stats["calls"],
        "cache_hits": _stats["cache_hits"],
        "retries": _stats["retries"],
        "failures": _stats["failures"],
        "skipped_not_displayed": _stats["skipped_not_displayed"],
        "chars_used": _stats["chars"],
        "estimated_cost_yuan": round(_stats["chars"] / 1_000_000 * price, 4),
        "cached_entries": len(_cache),
    }


def _fatal_status_message() -> str:
    label = _provider_meta()["label"]
    if _fatal_code == "NoFreeAmount":
        return (f"{label}免费额度已用完（NoFreeAmount）。未翻译内容在报告中保持原文；"
                "充值或购买资源包后重跑即可，已翻译过的内容命中本地缓存、不会重复计费。")
    if _fatal_code == "54004":
        return (f"{label}账户额度/余额不足（54004）。未翻译内容在报告中保持原文；"
                "充值后重跑即可，已翻译内容命中本地缓存、不会重复计费。")
    if _fatal_code == "52003":
        return ("百度翻译未授权（52003）：APP ID 或密钥不正确，或该应用未开通通用翻译服务。"
                "请核对 config/secrets.json 里的 baidu_appid / baidu_key。")
    if _fatal_code == "54001":
        return ("百度翻译签名错误（54001）：通常是密钥填错（APP ID 与密钥不匹配）。"
                "请核对 config/secrets.json 里的 baidu_appid / baidu_key。")
    if _fatal_code in ("AuthFailure", "UnauthorizedOperation", "InvalidCredential"):
        return ("翻译鉴权失败，请检查 config/secrets.json 或 .env 中的密钥是否有效。"
                "未翻译内容在报告中保持原文。")
    if _fatal_code == "UserNotRegistered":
        return ("腾讯云账号未开通机器翻译服务，请在控制台开通后重跑。"
                "未翻译内容在报告中保持原文。")
    if _fatal_code == "No module named":
        return ("未装腾讯云 SDK（tencentcloud-sdk-python），无法调用翻译接口。"
                "请执行 pip install -r requirements.txt 后重跑；未翻译内容在报告中保持原文。")
    if _fatal_code == "has no attribute":
        return ("腾讯云 SDK 版本与代码不匹配（缺少 TextTranslateRequest 接口）。"
                "请按 requirements.txt 的锁定版本重装 tencentcloud-sdk-python 后重跑。")
    return (f"翻译通道不可用（{_fatal_reason or '未知原因'}），"
            "未翻译内容在报告中保持原文。")


def run():
    """翻译入口：检查当前渠道可用性，不可用时跳过并如实记录状态"""
    if not ENHANCED_ITEMS_PATH.exists():
        raise FileNotFoundError(
            f"缺少上一步产物 {ENHANCED_ITEMS_PATH}，无法翻译（请检查摘要步骤）")

    provider = active_provider()
    label = _provider_meta()["label"]
    if not translation_available():
        print(f"[TRANSLATOR] {label}未配置（腾讯云需 TMT_SECRET_ID/TMT_SECRET_KEY，"
              "百度需 baidu_appid/baidu_key；可从 .env 或 config/secrets.json 读取）")
        print("[TRANSLATOR] 跳过翻译阶段，非中文内容将保持原文")
        with open(ENHANCED_ITEMS_PATH, "r", encoding="utf-8") as f:
            items = json.load(f)
        atomic_write(TRANSLATED_ITEMS_PATH, items, indent=2)
        atomic_write(TRANSLATION_STATUS_PATH, {
            "status": "unavailable",
            "provider": provider,
            "message": f"{label}密钥未配置，非中文内容将显示原文",
            "untranslated": len(items),
        })
        return items

    items, stats = translate_all()

    if _fatal_reason:
        if _fatal_code in ("54004", "NoFreeAmount"):
            status = "quota_exhausted"
        elif _fatal_code in ("No module named", "has no attribute"):
            status = "sdk_missing"
        else:
            status = "auth_failed"
        message = _fatal_status_message()
    elif stats["untranslated"]:
        status = "partial"
        message = f"有 {stats['untranslated']} 处内容翻译失败，已保留原文"
    else:
        status = "ok"
        message = ""

    atomic_write(TRANSLATION_STATUS_PATH, {
        "status": status,
        "message": message,
        "provider": provider,
        "provider_label": label,
        "fatal_error": _fatal_reason,
        **stats,
    })
    if _fatal_reason:
        print(f"[TRANSLATOR] 警告：{message}")
    elif stats["untranslated"]:
        print(f"[TRANSLATOR] 警告：{stats['untranslated']} 处内容未能翻译，报告中将显示原文")
    return items


if __name__ == "__main__":
    run()
