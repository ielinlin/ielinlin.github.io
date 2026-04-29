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

# ====================== 工具函数 ======================
def rich_text_to_markdown(rich_text_array):
    if not rich_text_array:
        return ''
    result = []
    for segment in rich_text_array:
        text = segment.get('plain_text', '')
        if not text: continue
        ann = segment.get('annotations', {})
        if ann.get('bold'): text = f"**{text}**"
        if ann.get('italic'): text = f"*{text}*"
        if ann.get('code'): text = f"`{text}`"
        if ann.get('strikethrough'): text = f"~~{text}~~"
        if ann.get('underline'): text = f"<u>{text}</u>"
        if segment.get('href'): text = f"[{text}]({segment['href']})"
        result.append(text)
    return ''.join(result)


def clean_slug(s):
    s = s.lower().strip()
    s = re.sub(r'[\s｜：:｜|•·]', '-', s)
    s = re.sub(r'[^\w\-]', '', s)
    s = re.sub(r'-+', '-', s)
    return s.strip('-') or 'untitled'


def compute_hash(text):
    lines = [line for line in text.split('\n') if not line.strip().startswith('date:')]
    return hashlib.md5('\n'.join(lines).encode('utf-8')).hexdigest()


# ====================== Notion API ======================
def get_page_content(block_id, start_cursor=None):
    url = f'https://api.notion.com/v1/blocks/{block_id}/children'
    params = {'page_size': 100}
    if start_cursor:
        params['start_cursor'] = start_cursor
    response = requests.get(url, headers=headers, params=params)
    if response.status_code != 200:
        return [], None
    data = response.json()
    return data.get('results', []), data.get('next_cursor')


def fetch_all_children(block_id):
    all_blocks = []
    start_cursor = None
    while True:
        children, next_cursor = get_page_content(block_id, start_cursor)
        all_blocks.extend(children)
        if not next_cursor: break
        start_cursor = next_cursor
        time.sleep(0.25)
    for block in all_blocks:
        if block.get('has_children', False):
            block['children'] = fetch_all_children(block['id'])
    return all_blocks


# ====================== 图片下载 ======================
def download_image(img_url, page_id, block_id):
    page_short = page_id.replace('-', '')[-8:]
    block_short = block_id.replace('-', '')[-8:]
    filename = f"{page_short}_{block_short}.jpg"

    images_dir = 'assets/images/posts'
    os.makedirs(images_dir, exist_ok=True)
    local_path = os.path.join(images_dir, filename)

    if os.path.exists(local_path) and os.path.getsize(local_path) > 1024:
        print(f"  ⏭️  图片已存在，跳过")
        return f"/assets/images/posts/{filename}"

    try:
        print(f"  → 下载图片")
        r = requests.get(img_url, stream=True, timeout=30)
        if r.status_code == 200:
            with open(local_path, 'wb') as f:
                for chunk in r.iter_content(8192):
                    f.write(chunk)
            print(f"  ✅ 下载完成")
            return f"/assets/images/posts/{filename}"
    except:
        pass
    return None


def convert_children(children, page_id, indent_level=0):
    return ''.join(block_to_markdown(child, page_id, indent_level + 1) for child in children)


def block_to_markdown(block, page_id, indent_level=0):
    block_type = block.get('type')
    block_data = block.get(block_type, {}) if block_type else {}
    rich_text = block_data.get('rich_text', []) if block_type in block else []
    indent = ' ' * indent_level

    if block_type == 'paragraph':
        md_text = rich_text_to_markdown(rich_text)
        return indent + md_text + '\n\n' if md_text.strip() else '\n'

    elif block_type in ['heading_1', 'heading_2', 'heading_3']:
        level = block_type[-1]
        md_text = rich_text_to_markdown(rich_text)
        return indent + '#' * int(level) + ' ' + md_text + '\n\n' if md_text.strip() else ''

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
        if not img_url: return ''
        caption_text = rich_text_to_markdown(image_data.get('caption', []))
        block_id = block.get('id', '')
        img_ref = download_image(img_url, page_id, block_id)
        return indent + f"![{caption_text}]({img_ref})\n\n" if img_ref else ''

    elif block_type == 'table':
        return indent + "*(表格暂不支持，请查看 Notion 原文)*\n\n"

    else:
        if block.get('children'):
            return convert_children(block['children'], page_id, indent_level)
        return ''


# ====================== 主流程 ======================
def main():
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    resp = requests.post(f'https://api.notion.com/v1/databases/{DATABASE_ID}/query', headers=headers)
    if resp.status_code != 200:
        print("查询失败")
        return

    pages = resp.json().get('results', [])
    print(f"Found {len(pages)} published pages.\n")

    valid_filenames = set()

    for page in pages:
        page_id = page['id']
        props = page.get('properties', {})

        title = "Untitled"
        for v in props.values():
            if v.get('type') == 'title' and v.get('title'):
                title = v['title'][0].get('plain_text', 'Untitled')
                break

        if props.get('Status', {}).get('select', {}).get('name') != 'Published':
            continue

        # 使用 Notion Date 字段（重点修复）
        date_prop = props.get('Date', {}).get('date', {})
        if date_prop and date_prop.get('start'):
            start = date_prop['start']
            date_str = start.replace('T', ' ').split('.')[0] + " +0800"
            file_date_part = start[:10]                    # ← 关键：直接取 YYYY-MM-DD
        else:
            date_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S +0800')
            file_date_part = datetime.now().strftime('%Y-%m-%d')

        slug = clean_slug(props.get('Slug', {}).get('rich_text', [{}])[0].get('plain_text', '') or title)

        print(f"Fetching: {title}  (Date: {file_date_part})")

        blocks = fetch_all_children(page_id)
        markdown_body = ''.join(block_to_markdown(b, page_id) for b in blocks)

        full_content = f"""---
layout: post
title: {title}
date: {date_str}
categories: {get_notion_categories(props)}
tags: {get_notion_tags(props)}
permalink: /posts/{slug}/
author_profile: true
---

{markdown_body}
"""

        filename = f"{file_date_part}-{slug}.md"
        filepath = os.path.join(OUTPUT_DIR, filename)

        need_write = True
        if os.path.exists(filepath):
            with open(filepath, 'r', encoding='utf-8') as f:
                old = f.read()
            if compute_hash(old) == compute_hash(full_content):
                print(f"  ✓ 内容未变化，跳过")
                need_write = False

        if need_write:
            with open(filepath, 'w', encoding='utf-8') as f:
                f.write(full_content)
            print(f"✅ Updated: {filename}")
        else:
            print(f"  ✓ 已跳过: {filename}")

        valid_filenames.add(filename)

    # 删除失效文件
    for fname in os.listdir(OUTPUT_DIR):
        if fname.endswith('.md') and fname not in valid_filenames:
            os.remove(os.path.join(OUTPUT_DIR, fname))
            print(f"🗑️ 删除: {fname}")

    print("\n🎉 同步完成！")


def get_notion_tags(props):
    return [tag['name'] for tag in props.get('Tags', {}).get('multi_select', [])]

def get_notion_categories(props):
    cat = props.get('Categories', {}).get('select', {})
    return [cat.get('name')] if cat and cat.get('name') else ["笔记"]


if __name__ == "__main__":
    main()
