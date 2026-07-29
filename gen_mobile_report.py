#!/usr/bin/env python3
"""
网络安全周报 · 移动版转换脚本
桌面版 HTML → 剥离详情数据 → 注入移动端 CSS+JS → 生成 _mobile.html

数据架构（移动端）：
  - 内联数据仅含列表字段（标题/来源/日期/分类）
  - 详情字段（full_body/ai_summary/summary/scoring_matched）按需从 JSON 加载
"""
import re, os, sys, json

HUB = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..'))
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

LIST_ONLY_FIELDS = {
    'title', 'source_name', 'published_date', 'content_type', 'source_type',
    'category', 'source_hue', 'filter_decision', 'confidence_score',
    'merged_sources', 'fulltext_fetched', 'url',
}
DETAIL_FIELDS = {'full_body', 'summary', 'ai_summary', 'scoring_matched'}


def _read_file(path: str) -> str:
    full_path = os.path.join(SCRIPT_DIR, path)
    if not os.path.exists(full_path):
        print(f"⚠️  文件不存在: {full_path}")
        return ""
    with open(full_path, 'r', encoding='utf-8') as f:
        return f.read()


MOBILE_CSS = _read_file('mobile.css')
MOBILE_JS = _read_file('mobile.js')


def _extract_json_array(text: str, start_marker: str) -> tuple:
    """从 JS 文本中提取 JSON 数组，返回 (json_text, start_pos, end_pos)"""
    start = text.find(start_marker)
    if start < 0:
        return None, -1, -1
    start += len(start_marker)
    # 从 [ 开始匹配到对应的 ]
    depth = 0
    in_str = False
    esc = False
    for i in range(start, len(text)):
        c = text[i]
        if esc:
            esc = False
            continue
        if c == '\\' and in_str:
            esc = True
            continue
        if c == '"':
            in_str = not in_str
            continue
        if in_str:
            continue
        if c == '[':
            depth += 1
        elif c == ']':
            depth -= 1
            if depth == 0:
                return text[start:i+1], start, i+1
    return None, -1, -1


def _strip_item(item: dict) -> dict:
    """移除详情字段，仅保留列表所需字段"""
    return {k: v for k, v in item.items() if k in LIST_ONLY_FIELDS}


def strip_detail_data(html: str) -> str:
    """从 HTML 的内联 JS 数据中剥离详情字段"""
    markers = ['var _allItems = ', 'var _reviewCount = (']
    for marker in markers:
        json_text, s_start, s_end = _extract_json_array(html, marker)
        if json_text is None:
            continue
        try:
            items = json.loads(json_text)
        except json.JSONDecodeError as e:
            print(f"⚠️  JSON 解析失败 ({marker}): {e}")
            continue
        if isinstance(items, list):
            stripped = [_strip_item(item) if isinstance(item, dict) else item for item in items]
            new_json = json.dumps(stripped, ensure_ascii=False, separators=(',',':'))
            html = html[:s_start] + new_json + html[s_end:]
            print(f"  📦 {marker.strip()}  {len(json_text):,} → {len(new_json):,} bytes "
                  f"(-{(1-len(new_json)/len(json_text))*100:.0f}%)")
    return html


def add_mobile_support(html: str) -> str:
    """注入移动端 CSS 和 JS"""
    html = html.replace('</style>', MOBILE_CSS + '\n</style>')
    last_script_end = html.rfind('</script>')
    if last_script_end > 0:
        html = html[:last_script_end] + '\n' + MOBILE_JS + '\n' + html[last_script_end:]
    return html


def process_file(input_path: str, output_path: str = None) -> bool:
    if not os.path.exists(input_path):
        print(f"❌ 文件不存在: {input_path}")
        return False
    with open(input_path, 'r', encoding='utf-8') as f:
        html = f.read()

    bytes_before = len(html)
    html = strip_detail_data(html)
    html = add_mobile_support(html)

    if output_path is None:
        base, ext = os.path.splitext(input_path)
        output_path = f"{base}_mobile{ext}"
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(html)
    print(f"✅ {os.path.basename(output_path)}  ({bytes_before:,} → {len(html):,} bytes)")
    return True


def deploy_data_files():
    """将当前周 data_<week>.json 复制到 hub 下供移动端按需加载"""
    src_dir = os.path.join(SCRIPT_DIR, 'reports')
    dst_dir = os.path.join(HUB, 'zhuanjia', 'data')
    os.makedirs(dst_dir, exist_ok=True)
    count = 0
    if os.path.isdir(src_dir):
        # 找到最新周的 data 文件
        data_files = [f for f in os.listdir(src_dir) if re.match(r'data_\d{4}W\d{2}\.json$', f)]
        if data_files:
            latest = sorted(data_files)[-1]  # 按文件名排序，最大的周号即最新
            src = os.path.join(src_dir, latest)
            dst = os.path.join(dst_dir, latest)
            with open(src, 'rb') as sf:
                with open(dst, 'wb') as df:
                    df.write(sf.read())
            count += 1
            # 同时复制 review 文件（如存在）
            review_file = latest.replace('.json', '_review.json')
            review_src = os.path.join(src_dir, review_file)
            if os.path.exists(review_src):
                review_dst = os.path.join(dst_dir, review_file)
                with open(review_src, 'rb') as sf, open(review_dst, 'wb') as df:
                    df.write(sf.read())
                count += 1
    print(f"📁 已部署 {count} 个数据文件到 {dst_dir}")
    return count


def process_all_reports():
    cybersec_dir = os.path.join(HUB, 'cybersec')
    zhuanjia_dir = os.path.join(HUB, 'zhuanjia')
    count = 0
    if os.path.isdir(cybersec_dir):
        for f in sorted(os.listdir(cybersec_dir)):
            if re.match(r'cybersec_weekly_\d{8}\.html$', f):
                base, ext = os.path.splitext(f)
                if process_file(os.path.join(cybersec_dir, f), os.path.join(cybersec_dir, f"{base}_mobile{ext}")):
                    count += 1
    if os.path.isdir(zhuanjia_dir):
        for f in sorted(os.listdir(zhuanjia_dir)):
            if f == 'cybersec_report.html':
                base, ext = os.path.splitext(f)
                if process_file(os.path.join(zhuanjia_dir, f), os.path.join(zhuanjia_dir, f"{base}_mobile{ext}")):
                    count += 1
    print(f"\n📊 共处理 {count} 个文件")
    return count


if __name__ == '__main__':
    if len(sys.argv) >= 2 and sys.argv[1] in ('--all', '-a'):
        deploy_data_files()
        process_all_reports()
    elif len(sys.argv) >= 2:
        process_file(sys.argv[1], sys.argv[2] if len(sys.argv) >= 3 else None)
    else:
        cybersec_dir = os.path.join(HUB, 'cybersec')
        if os.path.isdir(cybersec_dir):
            files = sorted([f for f in os.listdir(cybersec_dir) if re.match(r'cybersec_weekly_\d{8}\.html$', f)])
            if files:
                process_file(os.path.join(cybersec_dir, files[-1]))
                rp = os.path.join(HUB, 'zhuanjia', 'cybersec_report.html')
                if os.path.exists(rp):
                    process_file(rp)
