#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import os
import re
import requests
import time
import hashlib
from datetime import datetime
from urllib.parse import urlparse, unquote

NOTION_TOKEN = os.environ.get('NOTION_TOKEN')
DATABASE_ID = os.environ.get('NOTION_DATABASE_ID')
OUTPUT_DIR = './_posts/'

headers = {
    'Authorization': f'Bearer {NOTION_TOKEN}',
    'Content-Type': 'application/json',
    'Notion-Version': '2022-06-28'
}

# ---------- 工具函数 ----------
def rich_text_to_markdown(rich_text_array):
    if not rich_text_array:
        return ''
    result = []
    for segment in rich_text_array:
        text = segment.get('plain_text', '')
        if not text:
            continue
        annotations = segment.get('annotations', {})
        if annotations.get('bold'):
            text = f"**{text}**"
        if annotations.get('italic'):
            text = f"*{text}*"
        if annotations.get('code'):
            text = f"`{text}`"
        if annotations.get('strikethrough'):
            text = f"~~{text}~~"
        if annotations.get('underline'):
            text = f"<u>{text}</u>"
        if segment.get('href'):
            text = f"[{text}]({segment['href']})"
        result.append(text)
    return ''.join(result)


def clean_slug(s):
    s = s.lower().strip()
    s = re.sub(r'\s+', '-', s)
    s = re.sub(r'[^\w\-]', '', s)
    s = re.sub(r'-+', '-', s)
    return s.strip('-')


def get_notion_tags(props):
    tags_prop = props.get('Tags', {}).get('multi_select', [])
    return [tag['name'] for tag in tags_prop]


def get_notion_categories(props):
    categories_prop = props.get('Categories', {}).get('select', {})
    if categories_prop and categories_prop.get('name'):
        return [categories_prop['name']]
    return ["笔记"]


def compute_content_hash(content: str) -> str:
    """计算内容哈希，用于判断是否真正需要更新文件"""
    return hashlib.sha256(content.encode('utf-8')).hexdigest()


# ---------- Notion API ----------
def get_page_content(block_id, start_cursor=None):
    url = f'https://api.notion.com/v1/blocks/{block_id}/children'
    params = {'page_size': 100}
    if start_cursor:
        params['start_cursor'] = start_cursor

    response = requests.get(url, headers=headers, params=params)
    if response.status_code != 200:
        print(f"  Error getting children for {block_id}: {response.status_code}")
        return [], None

    data = response.json()
    return data.get('results', []), data.get('next_cursor')


def fetch_all_children(block_id):
    """完整分页 + 递归获取所有 blocks"""
    all_blocks = []
    start_cursor = None

    while True:
        children, next_cursor = get_page_content(block_id, start_cursor)
        all_blocks.extend(children)
        if not next_cursor:
            break
        start_cursor = next_cursor
        time.sleep(0.25)  # 防止 rate limit

    # 递归处理子块
    for block in all_blocks:
        if block.get('has_children', False):
            block['children'] = fetch_all_children(block['id'])

    return all_blocks


# ---------- 图片下载（优化版） ----------
def download_image(img_url, page_id, block_id, caption_text):
    parsed_url = urlparse(img_url)
    ext = os.path.splitext(os.path.basename(unquote(parsed_url.path)))[1].lower() or '.jpg'

    page_short = page_id.replace('-', '')[-8:]
    block_short = block_id.replace('-', '')[-8:]
    final_filename = f"{page_short}_{block_short}{ext}"

    images_dir = 'assets/images/posts'
    os.makedirs(images_dir, exist_ok=True)
    local_path = os.path.join(images_dir, final_filename)

    # 如果文件已存在且大小正常，则跳过
    if os.path.exists(local_path) and os.path.getsize(local_path) > 1024:
        print(f"  ⏭️  图片已存在，跳过下载: {final_filename}")
        return f"/assets/images/posts/{final_filename}"

    try:
        print(f"  → 下载图片: {final_filename}")
        response = requests.get(img_url, stream=True, timeout=30)
        if response.status_code == 200:
            content_type = response.headers.get('content-type', '').lower()
            if 'webp' in content_type:
                ext = '.webp'
            elif 'png' in content_type:
                ext = '.png'
            elif 'gif' in content_type:
                ext = '.gif'

            final_filename = f"{page_short}_{block_short}{ext}"
            local_path = os.path.join(images_dir, final_filename)

            with open(local_path, 'wb') as f:
                for chunk in response.iter_content(chunk_size=8192):
                    f.write(chunk)

            print(f"  ✅ 下载完成: {final_filename} ({os.path.getsize(local_path)//1024} KB)")
            return f"/assets/images/posts/{final_filename}"
        else:
            print(f"  ⚠️ 下载失败 ({response.status_code})")
            return None
    except Exception as e:
        print(f"  ⚠️ 下载异常: {e}")
        return None


def convert_children(children, page_id, indent_level=0):
    md = []
    for child in children:
        md.append(block_to_markdown(child, page_id, indent_level + 1))
    return ''.join(md)


def block_to_markdown(block, page_id, indent_level=0):
    block_type = block.get('type')
    block_data = block.get(block_type, {}) if block_type else {}
    rich_text = block_data.get('rich_text', []) if block_type in block else []
    indent = ' ' * indent_level

    if block_type == 'paragraph':
        md_text = rich_text_to_markdown(rich_text)
        if not md_text.strip():
            return '\n'
        return indent + md_text + '\n\n'

    elif block_type in ['heading_1', 'heading_2', 'heading_3']:
        level = block_type[-1]
        md_text = rich_text_to_markdown(rich_text)
        if not md_text.strip():
            return ''
        return indent + '#' * int(level) + ' ' + md_text + '\n\n'

    elif block_type == 'bulleted_list_item':
        line = indent + '- ' + rich_text_to_markdown(rich_text) + '\n'
        if block.get('children'):
            line += convert_children(block['children'], page_id, indent_level)
        return line

    elif block_type == 'numbered_list_item':
        line = indent + '1. ' + rich_text_to_markdown(rich_text) + '\n'
        if block.get('children'):
            line += convert_children(block['children'], page_id, indent_level)
        return line

    elif block_type == 'quote':
        md_text = rich_text_to_markdown(rich_text)
        result = indent + '> ' + md_text + '\n'
        if block.get('children'):
            child_content = convert_children(block['children'], page_id, indent_level)
            quoted = '\n'.join(['> ' + line if line.strip() else '' for line in child_content.split('\n')])
            result += quoted + '\n'
        return result + '\n'

    elif block_type == 'divider':
        return indent + '---\n\n'

    elif block_type == 'to_do':
        checked = block.get(block_type, {}).get('checked', False)
        checkbox = '[x]' if checked else '[ ]'
        text = rich_text_to_markdown(rich_text)
        line = indent + f'- {checkbox} {text}\n'
        if block.get('children'):
            line += convert_children(block['children'], page_id, indent_level)
        return line

    elif block_type == 'code':
        language = block.get(block_type, {}).get('language', '')
        code_content = ''.join([seg.get('plain_text', '') for seg in rich_text])
        return indent + f"```{language}\n{code_content}\n```\n\n"

    elif block_type == 'bookmark':
        url = block.get(block_type, {}).get('url', '')
        caption = rich_text_to_markdown(block.get(block_type, {}).get('caption', []))
        return indent + f"[{caption or url}]({url})\n\n"

    elif block_type == 'image':
        image_data = block.get('image', {})
        img_url = image_data.get('external', {}).get('url') or image_data.get('file', {}).get('url')
        if not img_url:
            return ''
        caption_text = rich_text_to_markdown(image_data.get('caption', []))
        block_id = block.get('id', '')
        img_ref = download_image(img_url, page_id, block_id, caption_text)
        if img_ref:
            return indent + f"![{caption_text}]({img_ref})\n\n"
        return ''

    elif block_type == 'table':
        return indent + "*(表格暂不支持，请查看 Notion 原文)*\n\n"

    else:
        if block.get('children'):
            return convert_children(block['children'], page_id, indent_level)
        return ''


# ---------- 主流程 ----------
def main():
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    # 查询数据库
    resp = requests.post(f'https://api.notion.com/v1/databases/{DATABASE_ID}/query', headers=headers)
    if resp.status_code != 200:
        print(f"查询数据库失败: {resp.status_code}")
        print(resp.text)
        return

    pages = resp.json().get('results', [])
    print(f"Found {len(pages)} published pages in database.\n")

    valid_filenames = set()

    for page in pages:
        page_id = page['id']
        props = page.get('properties', {})

        # 获取标题
        title = "Untitled"
        for value in props.values():
            if value.get('type') == 'title':
                title_items = value.get('title', [])
                if title_items:
                    title = title_items[0].get('plain_text', 'Untitled')
                break

        # 状态检查
        status = props.get('Status', {}).get('select', {}).get('name')
        if status != 'Published':
            continue

        # 日期处理
        date_prop = props.get('Date', {}).get('date', {})
        if date_prop and date_prop.get('start'):
            date_str = date_prop['start']
            if 'T' not in date_str:
                date_str = f"{date_str} 12:00:00 +0800"
            else:
                date_str = date_str.replace('T', ' ').split('.')[0] + " +0800"
        else:
            date_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S +0800')

        # Slug
        slug_prop = props.get('Slug', {}).get('rich_text', [])
        raw_slug = slug_prop[0].get('plain_text', '') if slug_prop else ''
        slug = clean_slug(raw_slug) if raw_slug else clean_slug(title)
        if not slug:
            slug = datetime.now().strftime('%Y%m%d%H%M%S')

        tags = get_notion_tags(props) or ["笔记"]
        categories = get_notion_categories(props)

        print(f"Fetching: {title}")

        # 获取内容
        blocks = fetch_all_children(page_id)
        content_blocks = [block_to_markdown(block, page_id) for block in blocks if block_to_markdown(block, page_id)]

        markdown_body = ''.join(content_blocks)

        # 构建完整文件内容
        full_content = f"""---
layout: post
title: {title}
date: {date_str}
categories: {categories}
tags: {tags}
permalink: /posts/{slug}/
author_profile: true
---

{markdown_body}
"""

        file_date_part = date_str.split()[0]
        filename = f"{file_date_part}-{slug}.md"
        filepath = os.path.join(OUTPUT_DIR, filename)

        # ==================== 核心优化：判断内容是否变化 ====================
        need_write = True
        if os.path.exists(filepath):
            with open(filepath, 'r', encoding='utf-8') as f:
                existing = f.read()
            if compute_content_hash(existing) == compute_content_hash(full_content):
                print(f"  ✓ 内容未变化，跳过写入: {filename}")
                need_write = False

        if need_write:
            with open(filepath, 'w', encoding='utf-8') as f:
                f.write(full_content)
            print(f"✅ Updated: {filename}")
        else:
            print(f"  ✓ 已跳过: {filename}")

        valid_filenames.add(filename)

    # 删除本地已失效的文章
    for fname in os.listdir(OUTPUT_DIR):
        if fname.endswith('.md') and fname not in valid_filenames:
            os.remove(os.path.join(OUTPUT_DIR, fname))
            print(f"🗑️ 删除失效文章：{fname}")

    print("\n🎉 Notion 同步完成！")


if __name__ == "__main__":
    main()
