#!/usr/bin/env python3
"""
安全管理后台 — 配置服务器

提供 REST API + 静态文件服务，用于管理周报系统的各项配置。

用法:
    python config_server.py [--project-dir DIR] [port]
    python config_server.py 8081
    python config_server.py --project-dir /path/to/project 8081

默认端口 8081，--project-dir 默认为项目根目录（server/ 的父目录）。
访问 http://localhost:8081/config.html
"""

import json
import os
import sys
import subprocess
import http.server
import urllib.parse
from pathlib import Path
from datetime import datetime

# ── 路径初始化（在 main() 中调用 _init_paths 完成）──
SERVER_DIR = Path(__file__).resolve().parent
_PROJECT_DIR = None
_CONFIG_DIR = None
_DATA_DIR = None
_REPORT_DIR = None
_SETTINGS_PATH = None
_SOURCES_PATH = None
_LLM_PATH = None
_KEYWORDS_PATH = None
_PIPELINE_LOG_PATH = None

_pipeline_proc: subprocess.Popen | None = None


def _init_paths(project_dir: str | None = None):
    global _PROJECT_DIR, _CONFIG_DIR, _DATA_DIR, _REPORT_DIR
    global _SETTINGS_PATH, _SOURCES_PATH, _LLM_PATH, _KEYWORDS_PATH
    global _PIPELINE_LOG_PATH, _SCORING_KEYWORDS_PATH

    _PROJECT_DIR = Path(project_dir).resolve() if project_dir else SERVER_DIR.parent
    # 确保 pipeline 模块可导入
    if str(_PROJECT_DIR) not in sys.path:
        sys.path.insert(0, str(_PROJECT_DIR))
    _CONFIG_DIR = _PROJECT_DIR / "config"
    _DATA_DIR = _PROJECT_DIR / "data"
    _REPORT_DIR = _PROJECT_DIR / "reports"
    _SETTINGS_PATH = _CONFIG_DIR / "settings.json"
    _SOURCES_PATH = _CONFIG_DIR / "source_config.yaml"
    _LLM_PATH = _CONFIG_DIR / "llm_config.yaml"
    _KEYWORDS_PATH = _CONFIG_DIR / "keywords.json"
    _SCORING_KEYWORDS_PATH = _CONFIG_DIR / "scoring_keywords.json"
    _PIPELINE_LOG_PATH = _DATA_DIR / "pipeline_run.log"


# ── Read/Write helpers ──

def read_yaml(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except Exception:
        return ""


def write_yaml(path: Path, content: str) -> bool:
    try:
        path.write_text(content, encoding="utf-8")
        return True
    except Exception as e:
        print(f"[CONFIG] 写入失败 {path.name}: {e}")
        return False


def read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def write_json(path: Path, data: dict) -> bool:
    try:
        path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return True
    except Exception as e:
        print(f"[CONFIG] 写入失败 {path.name}: {e}")
        return False


def read_json_array(path: Path) -> list:
    """读取 JSON 数组文件，失败返回空列表"""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except Exception:
        return []


def _count_by_source(items: list) -> dict:
    """按 source_name 统计条目数"""
    counts = {}
    for item in items:
        name = item.get("source_name", "")
        if name:
            counts[name] = counts.get(name, 0) + 1
    return counts


def _load_source_config() -> list[dict]:
    """从 source_config.yaml 读取信源列表"""
    try:
        import yaml
        cfg = yaml.safe_load(_SOURCES_PATH.read_text(encoding="utf-8"))
        return cfg.get("sources", [])
    except Exception:
        return []


def _check_raw_has_rss(raw_items: list, source_name: str) -> bool:
    """检查原始抓取内容是否包含有效的 RSS/XML"""
    for r in raw_items:
        if r.get("source_name") == source_name:
            xml = r.get("xml_text", "") or ""
            if not xml.strip():
                return False
            # 检查是否为 HTML（非 RSS）
            stripped = xml.strip()
            if stripped.startswith("<!DOCTYPE html") or stripped.startswith("<html"):
                return False
            return True
    return False


def _build_source_trace(
    name: str, group: str,
    fetch_status: dict, raw_items: list,
    parsed_counts: dict, deduped_counts: dict, final_counts: dict,
) -> dict:
    """构建单个信源的管道追踪记录"""
    fs = fetch_status.get(name, {})
    fetch_ok = fs.get("status") == "success"
    fetch_error = fs.get("error") or ""
    parsed = parsed_counts.get(name, 0)
    deduped = deduped_counts.get(name, 0)
    final = final_counts.get(name, 0)

    chain_parts = []
    reason = ""
    status = "ok"

    if not fetch_ok:
        # 抓取失败
        if "404" in fetch_error:
            reason = "URL 404，信源地址已失效，需要更新"
            chain_parts.append("404 抓取失败")
        elif "403" in fetch_error:
            reason = "403 被拒绝访问，信源可能开启了反爬"
            chain_parts.append("403 抓取失败")
        elif "500" in fetch_error or "503" in fetch_error:
            reason = f"信源服务器错误（{fetch_error[:30]}）"
            chain_parts.append(f"服务器错误")
        elif "SSL" in fetch_error or "certificate" in fetch_error.lower():
            reason = "SSL 证书验证失败"
            chain_parts.append("SSL 证书错误")
        else:
            reason = fetch_error[:80] if fetch_error else "抓取失败"
            chain_parts.append("抓取失败")
        status = "failed"
    elif parsed == 0:
        # 抓取成功但解析出 0 条
        has_rss = _check_raw_has_rss(raw_items, name)
        if not has_rss:
            reason = "返回 HTML 而非 RSS/XML，feedparser 无法解析"
            chain_parts.append("0 条解析")
        else:
            reason = "RSS 内容存在但未提取到有效条目（可能是空 feed 或格式异常）"
            chain_parts.append("0 条解析")
        status = "empty"
    else:
        chain_parts.append(f"{parsed} 条解析")

        if deduped == 0:
            reason = "全部文章超过7天有效期，被过期过滤策略丢弃"
            chain_parts.append(f"0 条进去重")
            status = "filtered"
        else:
            chain_parts.append(f"{deduped} 条进去重")

            if final == 0:
                # 查 deduped 和 parsed 的关系，判断是在 stage1 还是 stage2 丢的
                reason = "全部文章关键词评分不足30分，经两阶段评分过滤后丢弃"
                chain_parts.append("0 条过评分")
                status = "filtered"
            else:
                dropped = deduped - final
                if dropped > 0:
                    ratio = dropped / deduped * 100
                    if ratio > 70:
                        reason = f"大部分文章关键词评分偏低，经两阶段过滤仅保留 {final}/{deduped} 条"
                    elif ratio > 30:
                        reason = f"部分文章关键词匹配度不足，经两阶段过滤保留 {final}/{deduped} 条"
                    else:
                        reason = f"少量文章被评分过滤，保留 {final}/{deduped} 条"
                    status = "filtered"
                else:
                    reason = "正常"
                    status = "ok"
                chain_parts.append(f"{final} 条过评分")

    return {
        "name": name,
        "group": group,
        "chain": " → ".join(chain_parts),
        "reason": reason,
        "status": status,
        "parsed": parsed,
        "deduped": deduped,
        "final": final,
    }


def send_json(handler, data, status=200):
    body = json.dumps(data, ensure_ascii=False).encode("utf-8")
    try:
        handler.send_response(status)
        handler.send_header("Content-Type", "application/json; charset=utf-8")
        handler.send_header("Access-Control-Allow-Origin", "*")
        handler.send_header("Content-Length", str(len(body)))
        handler.end_headers()
        handler.wfile.write(body)
    except OSError:
        pass  # 客户端断开连接，忽略


def read_body(handler) -> str:
    length = int(handler.headers.get("Content-Length", 0))
    if length == 0:
        return ""
    return handler.rfile.read(length).decode("utf-8")


# ── Request Handler ──

class ConfigHandler(http.server.SimpleHTTPRequestHandler):

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(_PROJECT_DIR), **kwargs)

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, PUT, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def _safe_call(self, fn):
        """执行 API 方法，捕获所有异常避免服务器崩溃"""
        try:
            fn()
        except OSError:
            pass  # 客户端断开连接
        except Exception as e:
            print(f"[CONFIG ERROR] {self.path}: {e}")
            import traceback
            traceback.print_exc()
            try:
                send_json(self, {"ok": False, "error": f"服务器内部错误: {e}"}, 500)
            except OSError:
                pass  # 客户端已断开，忽略

    def do_GET(self):
        self._safe_call(self._do_get_impl)

    def do_PUT(self):
        self._safe_call(self._do_put_impl)

    def do_POST(self):
        self._safe_call(self._do_post_impl)

    def _do_get_impl(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path.rstrip("/")

        # /config.html 快捷路径
        if path == "/config.html":
            self.send_response(302)
            self.send_header("Location", "/templates/config.html")
            self.end_headers()
            return

        # 根路径：有周报则跳转，否则跳到配置页
        if path == "" or path == "/":
            report_path = _PROJECT_DIR / "reports" / "Security_Reports.html"
            target = "/reports/Security_Reports.html" if report_path.exists() else "/templates/config.html"
            self.send_response(302)
            self.send_header("Location", target)
            self.end_headers()
            return

        # ── API routes ──
        if path == "/api/config/sources":
            return self._get_sources()
        elif path == "/api/config/settings":
            return self._get_settings()
        elif path == "/api/config/llm":
            return self._get_llm()
        elif path == "/api/pipeline/status":
            return self._pipeline_status()
        elif path == "/api/config/category_order":
            return self._get_category_order()
        elif path == "/api/config/source_status":
            return self._get_source_status()
        elif path == "/api/config/pipeline_trace":
            return self._get_pipeline_trace()
        elif path == "/api/config/security_keywords":
            return self._get_security_keywords()
        elif path == "/api/config/scoring_keywords":
            return self._get_scoring_keywords()
        elif path == "/api/config/scoring_keywords/save":
            return self._put_scoring_keywords()

        # ── Static files ──
        return super().do_GET()

    def _do_put_impl(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path.rstrip("/")

        if path == "/api/config/sources":
            return self._put_sources()
        elif path == "/api/config/settings":
            return self._put_settings()
        elif path == "/api/config/llm":
            return self._put_llm()
        elif path == "/api/config/category_order":
            return self._put_category_order()
        elif path == "/api/config/security_keywords":
            return self._put_security_keywords()
        elif path == "/api/config/scoring_keywords":
            return self._put_scoring_keywords()

        send_json(self, {"error": "Not found"}, 404)

    def _do_post_impl(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path.rstrip("/")

        if path == "/api/pipeline/run":
            return self._run_pipeline()

        send_json(self, {"error": "Not found"}, 404)

    # ── API Implementations ──

    def _get_sources(self):
        text = read_yaml(_SOURCES_PATH)
        send_json(self, {"ok": True, "yaml": text})

    def _put_sources(self):
        body = read_body(self)
        data = json.loads(body)
        ok = write_yaml(_SOURCES_PATH, data.get("yaml", ""))
        send_json(self, {"ok": ok})

    def _get_settings(self):
        cfg = read_json(_SETTINGS_PATH)
        send_json(self, {"ok": True, "settings": cfg})

    def _put_settings(self):
        body = read_body(self)
        data = json.loads(body)
        ok = write_json(_SETTINGS_PATH, data.get("settings", {}))
        send_json(self, {"ok": ok})

    def _get_llm(self):
        text = read_yaml(_LLM_PATH)
        send_json(self, {"ok": True, "yaml": text})

    def _put_llm(self):
        body = read_body(self)
        data = json.loads(body)
        ok = write_yaml(_LLM_PATH, data.get("yaml", ""))
        send_json(self, {"ok": ok})

    def _get_category_order(self):
        cfg = read_json(_SETTINGS_PATH)
        order = cfg.get("category_order", [])
        send_json(self, {"ok": True, "order": order})

    def _put_category_order(self):
        body = read_body(self)
        data = json.loads(body)
        cfg = read_json(_SETTINGS_PATH)
        cfg["category_order"] = data.get("order", [])
        ok = write_json(_SETTINGS_PATH, cfg)
        send_json(self, {"ok": ok})

    def _get_source_status(self):
        status_path = _DATA_DIR / "fetch_status.json"
        status = read_json(status_path)
        send_json(self, {"ok": True, "status": status})

    def _get_pipeline_trace(self):
        """追踪每个信源在管道各阶段的数据流"""
        # 读取各阶段数据（如果存在）
        fetch_status = read_json(_DATA_DIR / "fetch_status.json")
        raw_items = read_json_array(_DATA_DIR / "raw_items.json")
        parsed_items = read_json_array(_DATA_DIR / "parsed_items.json")
        deduped_items = read_json_array(_DATA_DIR / "deduped_items.json")
        enhanced_items = read_json_array(_DATA_DIR / "enhanced_items.json")
        source_config = _load_source_config()

        # 按信源名称聚合各阶段计数
        parsed_counts = _count_by_source(parsed_items)
        deduped_counts = _count_by_source(deduped_items)
        final_counts = _count_by_source(enhanced_items)

        # 构建每个信源的追踪记录
        result = []
        seen = set()

        # 遍历信源配置中所有启用的信源（保持配置中的顺序）
        for src in source_config:
            name = src["name"]
            group = src.get("group", "其他")
            seen.add(name)

            trace = _build_source_trace(
                name, group, fetch_status, raw_items,
                parsed_counts, deduped_counts, final_counts,
            )
            result.append(trace)

        # 补充不在配置中但出现在数据中的信源
        all_data_names = set(parsed_counts) | set(deduped_counts) | set(final_counts)
        for name in all_data_names:
            if name not in seen:
                trace = _build_source_trace(
                    name, "其他", fetch_status, raw_items,
                    parsed_counts, deduped_counts, final_counts,
                )
                result.append(trace)

        send_json(self, {"ok": True, "sources": result})



    def _get_security_keywords(self):
        """读取关键字列表"""
        from pipeline.keyword_filter import load_keywords, init_default_keywords
        init_default_keywords()  # 首次自动初始化默认值
        keywords = load_keywords()
        send_json(self, {"ok": True, "keywords": keywords})

    def _put_security_keywords(self):
        """替换关键字列表"""
        body = read_body(self)
        data = json.loads(body)
        from pipeline.keyword_filter import save_keywords, DEFAULT_KEYWORDS, load_keywords
        kws = data.get("keywords", [])
        if not kws:
            # 空列表 = 恢复默认
            ok = save_keywords(DEFAULT_KEYWORDS)
        else:
            ok = save_keywords(kws)
        send_json(self, {"ok": ok, "keywords": load_keywords()})

    def _get_scoring_keywords(self):
        """读取评分关键词完整配置"""
        cfg = read_json(_SCORING_KEYWORDS_PATH)
        if not cfg:
            send_json(self, {"ok": False, "error": "评分配置文件不存在"})
            return
        send_json(self, {"ok": True, "config": cfg})

    def _put_scoring_keywords(self):
        """保存评分关键词完整配置"""
        body = read_body(self)
        data = json.loads(body)
        cfg = data.get("config", {})
        ok = write_json(_SCORING_KEYWORDS_PATH, cfg)
        send_json(self, {"ok": ok})

    def _pipeline_status(self):
        global _pipeline_proc
        running = _pipeline_proc is not None and _pipeline_proc.poll() is None
        log_text = ""
        try:
            if _PIPELINE_LOG_PATH.exists():
                log_text = _PIPELINE_LOG_PATH.read_text(encoding="utf-8")
        except Exception:
            pass
        lines = log_text.strip().split("\n")
        send_json(self, {
            "ok": True,
            "running": running,
            "log": lines,
        })

    def _run_pipeline(self):
        global _pipeline_proc
        if _pipeline_proc is not None and _pipeline_proc.poll() is None:
            send_json(self, {"ok": False, "error": "管道正在运行中"}, 409)
            return

        # 清空并初始化日志文件
        _DATA_DIR.mkdir(parents=True, exist_ok=True)
        _PIPELINE_LOG_PATH.write_text("", encoding="utf-8")
        with open(_PIPELINE_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(f"[{datetime.now().isoformat()}] 管道启动...\n")

        proc = subprocess.Popen(
            [sys.executable, "-u", "app.py", "--run"],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, cwd=str(_PROJECT_DIR),
        )
        _pipeline_proc = proc

        def drain():
            """将子进程 stdout 实时写入日志文件"""
            try:
                with open(_PIPELINE_LOG_PATH, "a", encoding="utf-8") as log_f:
                    for line in iter(proc.stdout.readline, ""):
                        log_f.write(line)
                        log_f.flush()
                    proc.wait()
                    log_f.write(f"[{datetime.now().isoformat()}] 退出码: {proc.returncode}\n")
            except Exception as e:
                with open(_PIPELINE_LOG_PATH, "a", encoding="utf-8") as log_f:
                    log_f.write(f"[{datetime.now().isoformat()}] 管道错误: {e}\n")

        import threading
        t = threading.Thread(target=drain, daemon=True)
        t.start()

        send_json(self, {"ok": True, "message": "管道已启动"})


def main():
    import argparse
    parser = argparse.ArgumentParser(description="安全管理后台配置服务器")
    parser.add_argument("--project-dir", default=None,
                        help="项目根目录路径（默认自动检测）")
    parser.add_argument("port", nargs="?", default=8081, type=int,
                        help="监听端口（默认 8081）")
    args = parser.parse_args()

    _init_paths(args.project_dir)
    port = args.port

    server = http.server.HTTPServer(("0.0.0.0", port), ConfigHandler)
    print(f"[CONFIG] 管理后台: http://localhost:{port}/config.html")
    print(f"[CONFIG] 项目目录: {_PROJECT_DIR}")
    print(f"[CONFIG] API:       http://localhost:{port}/api/config/sources")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[CONFIG] 已停止")
        server.server_close()


if __name__ == "__main__":
    main()
