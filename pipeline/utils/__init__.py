"""管道工具函数 — 原子写入、预压缩、密钥加载"""

import gzip
import json
import os
import tempfile
from pathlib import Path
from typing import Any


SECRETS_PATH = Path("config/secrets.json")


def load_secrets() -> dict:
    """从 config/secrets.json 加载 API 密钥，文件不存在则返回空字典"""
    if SECRETS_PATH.exists():
        try:
            with open(SECRETS_PATH, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def atomic_write(path: str | Path, data: Any, **json_kwargs):
    """原子写入 JSON 文件：先写临时文件，再 rename 覆盖目标路径。

    管道各阶段共享 parsed_items.json 作为中间数据，普通写入若在
    写入中途崩溃会导致 JSON 截断/损坏。本函数确保写入要么完全
    成功，要么完全不改变目标文件。
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, **json_kwargs)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def precompress(path: Path, level: int = 6, remove_original: bool = False) -> Path | None:
    """为文件生成预压缩 .gz 版本，返回压缩文件路径（压缩后更大则跳过）"""
    if not path.exists():
        return None
    gz_path = path.with_name(path.name + ".gz")
    raw = path.read_bytes()
    compressed = gzip.compress(raw, compresslevel=level)
    if len(compressed) >= len(raw):
        return None
    gz_path.write_bytes(compressed)
    if remove_original:
        path.unlink()
    return gz_path
