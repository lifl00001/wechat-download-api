#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
一次性回填脚本：归一化 news_items.published_date 格式

背景：
    各数据源 published_date 格式不统一：
      - tavily: 'Mon, 15 Jun 2026 00:02:43 GMT'（RFC822）
      - aihot:  '2026-06-15'（仅日期）
      - baidu:  '2026-06-15 00:02:43'（已标准）
    news_store.py 已经修好（新增 normalize_published_date，入库时统一成
    'YYYY-MM-DD HH:MM:SS' 本地时间格式），本脚本回填历史数据。

目标：把所有非标准格式的 published_date 统一成 'YYYY-MM-DD HH:MM:SS'。
    优先用 published_ts（已在上一次回填中修好），ts 缺失时从字符串解析。

连接目标：直连 MySQL（同 _backfill_published_ts.py 的原因）。

用法:
    python _backfill_published_date_format.py          # 默认 dry-run
    python _backfill_published_date_format.py --apply  # 实际执行
"""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import pymysql
from dotenv import load_dotenv
load_dotenv(Path(__file__).parent / ".env")

DB_HOST = os.environ.get("DB_HOST", "127.0.0.1")
DB_PORT = int(os.environ.get("DB_PORT", "3306"))
DB_DATABASE = os.environ.get("DB_DATABASE", "we_mp")
DB_USERNAME = os.environ.get("DB_USERNAME", "root")
DB_PASSWORD = os.environ.get("DB_PASSWORD", "")

from utils.news_store import normalize_published_date


def _is_standard_format(s):
    """判断是否已是 'YYYY-MM-DD HH:MM:SS' 格式"""
    if not s or len(s) != 19:
        return False
    return (s[4] == "-" and s[7] == "-" and s[10] == " "
            and s[13] == ":" and s[16] == ":")


def main():
    import argparse
    parser = argparse.ArgumentParser(description="归一化 news_items.published_date 历史格式")
    parser.add_argument("--apply", action="store_true",
                        help="实际执行（默认只 dry-run 预览）")
    args = parser.parse_args()

    print("=" * 60)
    print("  news_items.published_date 格式归一化")
    print(f"  目标库: {DB_DATABASE}@{DB_HOST}:{DB_PORT}")
    print(f"  模式:   {'APPLY（写库）' if args.apply else 'DRY-RUN（预览）'}")
    print("=" * 60)

    conn = pymysql.connect(
        host=DB_HOST, port=DB_PORT, database=DB_DATABASE,
        user=DB_USERNAME, password=DB_PASSWORD, charset="utf8mb4",
    )
    try:
        cur = conn.cursor()

        # 1) 摸底：非标准格式的有多少
        cur.execute("""SELECT COUNT(*) FROM news_items
                       WHERE published_date != ''
                         AND published_date NOT REGEXP '^[0-9]{4}-[0-9]{2}-[0-9]{2} [0-9]{2}:[0-9]{2}:[0-9]{2}$'""")
        total = cur.fetchone()[0]
        print(f"\n非标准格式总数: {total}")

        if total == 0:
            print("\n✅ 没有需要归一化的数据，退出。")
            return

        # 2) 拉取所有非标准格式记录
        cur.execute("""SELECT id, source_engine, published_date, published_ts
                       FROM news_items
                       WHERE published_date != ''
                         AND published_date NOT REGEXP '^[0-9]{4}-[0-9]{2}-[0-9]{2} [0-9]{2}:[0-9]{2}:[0-9]{2}$'
                       ORDER BY id""")
        rows = cur.fetchall()

        fixes = []       # (new_date, id)
        skipped = []     # 仍无法归一化的
        by_pattern = {}  # 按原格式特征统计

        for row_id, engine, pdate, pts in rows:
            new_date = normalize_published_date(pdate, pts or 0)
            # 记录原始格式特征
            if pdate.startswith(("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")):
                pattern = "RFC822"
            elif len(pdate) == 10:
                pattern = "仅日期"
            else:
                pattern = "其他"
            by_pattern[pattern] = by_pattern.get(pattern, 0) + 1

            if new_date and new_date != pdate and _is_standard_format(new_date):
                fixes.append((new_date, row_id))
            elif not _is_standard_format(new_date):
                skipped.append((row_id, engine, pdate, new_date))

        print(f"\n可归一化: {len(fixes)} 条")
        print(f"无法归一化: {len(skipped)} 条")
        print("\n原始格式分布:")
        for p, cnt in sorted(by_pattern.items(), key=lambda x: -x[1]):
            print(f"  {p:15} {cnt}")

        # 预览样本
        if fixes:
            print("\n归一化样本（前8）:")
            cur.execute("""SELECT id, source_engine, published_date, published_ts
                           FROM news_items WHERE id IN (%s) ORDER BY id LIMIT 8"""
                        % ",".join(str(f[1]) for f in fixes[:8]))
            for rid, eng, pdate, pts in cur.fetchall():
                new_date = next(d for d, i in fixes if i == rid)
                print(f"  id={rid:5} {eng:12} {pdate[:35]:35} -> {new_date}")

        if skipped:
            print(f"\n⚠️ {len(skipped)} 条无法归一化（样本）:")
            for rid, eng, pdate, new_date in skipped[:5]:
                print(f"  id={rid} {eng} 原文={pdate!r} 归一化={new_date!r}")

        # 3) 实际写库
        if not args.apply:
            print(f"\n📝 DRY-RUN：未写库。确认无误后加 --apply：")
            print(f"   python _backfill_published_date_format.py --apply")
            return

        if not fixes:
            print("\n无可更新数据，退出。")
            return

        print(f"\n写入 {len(fixes)} 条更新...")
        # 用 CASE WHEN 单条 SQL 替代 executemany，把 1283 次网络往返压成 1 次
        # WHERE 限定 id 范围避免全表扫描评估所有 CASE
        min_id = min(f[1] for f in fixes)
        max_id = max(f[1] for f in fixes)
        when_clauses = " ".join(
            f"WHEN {rid} THEN %s" for _, rid in fixes
        )
        ids_tuple = tuple(f[1] for f in fixes)
        placeholders = ",".join(["%s"] * len(ids_tuple))
        sql = f"""
            UPDATE news_items
            SET published_date = CASE id {when_clauses} ELSE published_date END
            WHERE id IN ({placeholders})
        """
        params = [new_date for new_date, _ in fixes] + list(ids_tuple)
        cur.execute(sql, params)
        conn.commit()
        print(f"✅ 完成：成功归一化 {cur.rowcount} 条 published_date")

        # 4) 复验
        cur.execute("""SELECT COUNT(*) FROM news_items
                       WHERE published_date != ''
                         AND published_date NOT REGEXP '^[0-9]{4}-[0-9]{2}-[0-9]{2} [0-9]{2}:[0-9]{2}:[0-9]{2}$'""")
        remaining = cur.fetchone()[0]
        print(f"\n复验：剩余非标准格式: {remaining}")
        if remaining == 0:
            print("🎉 全部归一化完成")
        else:
            print(f"（剩余 {remaining} 条见上方失败样本）")

    finally:
        conn.close()


if __name__ == "__main__":
    main()
