#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import os
import re
import requests
import time
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

# ---------- 辅助函数 ----------
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
    else:
        return ["笔记"]


# ---------- Notion API - 支持完整分页 ----------
def get_page_content(block_id, start_cursor=None):
    """支持分页获取 block 的 children"""
    url = f'https://api.notion.com/v1/blocks/{block_id}/children'
    params = {'page_size': 100}
    if start_cursor:
        params['start_cursor'] = start_cursor

    response = requests.get(url, headers=headers, params=params)
    if response.status_code != 200:
        print(f"Error getting children for {block_id}: {response.status_code}")
        print(response.text)
        return [], None

    data = response.json()
    results = data.get('results', [])
    has_more = data.get('has_more', False)
    next_cursor = data.get('next_cursor') if has_more else None
    return results, next_cursor


def fetch_all_children(block_id):
    """完整递归 + 分页获取所有后代 blocks"""
    all_blocks = []
    start_cursor = None

    print(f"  ↳ 正在获取页面内容...")

    while True:
        children, next_cursor = get_page_content(block_id, start_cursor)
        all_blocks.extend(children)

        if not next_cursor:
            break
        start_cursor = next_cursor
        time.sleep(0.25)  # 避免 rate limit

    # 递归处理有子块的 block
    for block in all_blocks:
        if block.get('has_children', False):
            block['children'] = fetch_all_children(block['id'])

    return all_blocks


# ---------- 优化后的图片下载函数（关键修复） ----------
def download_image(img_url, page_id, block_id, caption_text):
    """优化版：已存在的图片不再重复下载"""
    parsed_url = urlparse(img_url)
    path = unquote(parsed_url.path)
    original_filename = os.path.basename(path)
    ext = os.path.splitext(original_filename)[1].lower()
    if not ext:
        ext = '.jpg'

    page_short = page_id.replace('-', '')[-8:] if page_id else 'unknown'
    block_short = block_id.replace('-', '')[-8:] if block_id else 'unknown'
    unique_name = f"{page_short}_{block_short}{ext}"
    final_filename = unique_name

    images_dir = 'assets/images/posts'
    os.makedirs(images_dir, exist_ok=True)
    local_path = os.path.join(images_dir, final_filename)

    # 如果文件已存在且大小正常，则跳过下载
    if os.path.exists(local_path):
        file_size = os.path.getsize(local_path)
        if file_size > 1024:  # 大于 1KB 认为是有效图片
            print(f"  ⏭️  图片已存在，跳过下载: {final_filename} ({file_size//1024} KB)")
            return f"/assets/images/posts/{final_filename}"
        else:
            print(f"  ⚠️  图片文件损坏，准备重新下载: {final_filename}")

    # 执行下载
    try:
        print(f"  → 下载新图片: {final_filename}")
        response = requests.get(img_url, stream=True, timeout=30)
        
        if response.status_code == 200:
            # 根据 Content-Type 修正扩展名
            content_type = response.headers.get('content-type', '').lower()
            if 'png' in content_type:
                ext = '.png'
            elif 'gif' in content_type:
                ext = '.gif'
            elif 'webp' in content_type:
                ext = '.webp'
            elif 'jpeg' in content_type or 'jpg' in content_type:
                ext = '.jpg'

            final_filename = f"{page_short}_{block_short}{ext}"
            local_path = os.path.join(images_dir, final_filename)

            with open(local_path, 'wb') as f:
                for chunk in response.iter_content(chunk_size=8192):
                    f.write(chunk)

            file_size = os.path.getsize(local_path)
            print(f"  ✅ 下载完成: {final_filename} ({file_size//1024} KB)")
            return f"/assets/images/posts/{final_filename}"
        else:
            print(f"  ⚠️  图片下载失败 ({response.status_code})")
            return None

    except Exception as e:
        print(f"  ⚠️  图片下载异常: {e}")
        return None


def convert_children(children, page_id, indent_level=0):
    """递归转换子块列表"""
    md = []
    for child in children:
        md.append(block_to_markdown(child, page_id, indent_level + 1))
    return ''.join(md)


def block_to_markdown(block, page_id, indent_level=0):
    """将 Notion 块转换为 Markdown"""
    block_type = block.get('type')
    block_data = block.get(block_type, {}) if block_type else {}
    rich_text = block_data.get('rich_text', []) if block_type in block else []
    indent = ' ' * indent_level

    if block_type == 'paragraph':
        md_text = rich_text_to_markdown(rich_text)
        if not md_text.strip():
            return '\n'
        return indent + md_text + '\n\n'

    elif block_type == 'heading_1':
        md_text = rich_text_to_markdown(rich_text)
        if not md_text.strip():
            return ''
        return indent + '# ' + md_text + '\n\n'
    elif block_type == 'heading_2':
        md_text = rich_text_to_markdown(rich_text)
        if not md_text.strip():
            return ''
        return indent + '## ' + md_text + '\n\n'
    elif block_type == 'heading_3':
        md_text = rich_text_to_markdown(rich_text)
        if not md_text.strip():
            return ''
        return indent + '### ' + md_text + '\n\n'

    elif block_type == 'bulleted_list_item':
        line = indent + '- ' + rich_text_to_markdown(rich_text) + '\n'
        children = block.get('children', [])
        if children:
            line += convert_children(children, page_id, indent_level)
        return line

    elif block_type == 'numbered_list_item':
        line = indent + '1. ' + rich_text_to_markdown(rich_text) + '\n'
        children = block.get('children', [])
        if children:
            line += convert_children(children, page_id, indent_level)
        return line

    elif block_type == 'quote':
        md_text = rich_text_to_markdown(rich_text)
        result = indent + '> ' + md_text + '\n'
        children = block.get('children', [])
        if children:
            child_content = convert_children(children, page_id, indent_level)
            child_lines = child_content.split('\n')
            quoted = '\n'.join(['> ' + line if line.strip() else '' for line in child_lines])
            result += quoted + '\n'
        return result + '\n'

    elif block_type == 'divider':
        return indent + '---\n\n'

    elif block_type == 'to_do':
        checked = block.get(block_type, {}).get('checked', False)
        checkbox = '[x]' if checked else '[ ]'
        text = rich_text_to_markdown(rich_text)
        line = indent + f'- {checkbox} {text}\n'
        children = block.get('children', [])
        if children:
            line += convert_children(children, page_id, indent_level)
        return line

    elif block_type == 'code':
        language = block.get(block_type, {}).get('language', '')
        code_content = ''.join([seg.get('plain_text', '') for seg in rich_text])
        return indent + f"```{language}\n{code_content}\n```\n\n"

    elif block_type == 'bookmark':
        url = block.get(block_type, {}).get('url', '')
        caption_blocks = block.get(block_type, {}).get('caption', [])
        caption_text = rich_text_to_markdown(caption_blocks)
        if not caption_text:
            caption_text = url
        return indent + f"[{caption_text}]({url})\n\n"

    elif block_type == 'image':
        image_data = block.get('image', {})
        if 'external' in image_data:
            img_url = image_data['external']['url']
        elif 'file' in image_data:
            img_url = image_data['file']['url']
        else:
            return ''
        caption_blocks = image_data.get('caption', [])
        caption_text = rich_text_to_markdown(caption_blocks)
        block_id = block.get('id', '')
        img_ref = download_image(img_url, page_id, block_id, caption_text)
        if img_ref:
            return indent + f"![{caption_text}]({img_ref})\n\n"
        return ''

    elif block_type == 'table':
        return indent + "*(表格暂不支持，请查看 Notion 原文)*\n\n"

    else:
        children = block.get('children', [])
        if children:
            return convert_children(children, page_id, indent_level)
        return ''


# ---------- 主流程 ----------
def main():
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    # 查询数据库
    url = f'https://api.notion.com/v1/databases/{DATABASE_ID}/query'
    response = requests.post(url, headers=headers)
    if response.status_code != 200:
        print(f"Error querying database: {response.status_code}")
        print(response.text)
        return

    data = response.json()
    pages = data.get('results', [])
    print(f"Found {len(pages)} published pages in database.\n")

    valid_filenames = set()

    for page in pages:
        page_id = page['id']
        props = page.get('properties', {})

        # 获取标题
        title = "Untitled"
        for key, value in props.items():
            if value.get('type') == 'title':
                title_items = value.get('title', [])
                if title_items:
                    title = title_items[0].get('plain_text', 'Untitled')
                break

        # 状态检查
        status_prop = props.get('Status', {}).get('select', {})
        status_value = status_prop.get('name') if status_prop else None
        if status_value != 'Published':
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

        # Slug 处理
        slug_prop = props.get('Slug', {}).get('rich_text', [])
        if slug_prop:
            raw_slug = slug_prop[0].get('plain_text', '')
            slug = clean_slug(raw_slug) if raw_slug else clean_slug(title)
        else:
            slug = clean_slug(title)

        if not slug:
            slug = datetime.now().strftime('%Y%m%d%H%M%S')

        tags = get_notion_tags(props) or ["笔记"]
        categories = get_notion_categories(props)

        print(f"Fetching content for: {title}")

        # 获取完整内容（支持分页）
        blocks = fetch_all_children(page_id)

        content = []
        for block in blocks:
            md = block_to_markdown(block, page_id)
            if md:
                content.append(md)

        # 写入文件
        file_date_part = date_str.split()[0]
        filename = f"{file_date_part}-{slug}.md"
        filepath = os.path.join(OUTPUT_DIR, filename)

        with open(filepath, 'w', encoding='utf-8') as f:
            f.write("---\n")
            f.write(f"layout: post\n")
            f.write(f"title: {title}\n")
            f.write(f"date: {date_str}\n")
            f.write(f"categories: {categories}\n")
            f.write(f"tags: {tags}\n")
            f.write(f"permalink: /posts/{slug}/\n")
            f.write(f"author_profile: true\n")
            f.write("---\n\n")
            f.write(''.join(content))

        valid_filenames.add(filename)
        print(f"✅ Created / Updated: {filename}\n")

    # 删除本地不再需要的文章
    for fname in os.listdir(OUTPUT_DIR):
        if fname.endswith('.md') and fname not in valid_filenames:
            full_path = os.path.join(OUTPUT_DIR, fname)
            print(f"🗑️ 删除本地失效文章：{fname}")
            os.remove(full_path)

    print("🎉 Notion 同步完成！")


if __name__ == "__main__":
    main()
