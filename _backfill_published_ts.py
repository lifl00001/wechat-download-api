#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
一次性回填脚本：修复 news_items.published_ts 历史空值

背景：
    tavily 引擎返回的 published_date 是 RFC822 格式（'Mon, 15 Jun 2026 00:02:43 GMT'），
    旧版 news_store.py 的日期解析只认 ISO8601/标准日期，导致这批数据 published_ts 全为 0，
    按时间排序时全部沉底。news_store.py 已经修好（新增 parse_date_to_ts），本脚本回填历史数据。

作用范围：
    WHERE published_ts = 0 AND published_date != ''
    （只修"有发布时间但 ts 解析失败"的，不动 newsnow 等本来就无发布时间的记录）

连接目标：
    直连 MySQL（we_mp 库）。项目默认 db_manager 走 SQLite，但真实数据在 MySQL，
    所以这里独立用 pymysql 连接，不走 db_manager。

用法:
    python _backfill_published_ts.py            # 默认 dry-run（只预览，不写库）
    python _backfill_published_ts.py --apply    # 实际执行回填

依赖:
    pymysql, python-dotenv（读 .env 里的 DB_* 配置）
"""

import os
import sys
from pathlib import Path

# 让脚本能从项目根目录直接跑（python _backfill_published_ts.py）
sys.path.insert(0, str(Path(__file__).parent))

import pymysql

# 从 .env 读数据库配置（与项目其他脚本一致）
from dotenv import load_dotenv
load_dotenv(Path(__file__).parent / ".env")

DB_HOST = os.environ.get("DB_HOST", "127.0.0.1")
DB_PORT = int(os.environ.get("DB_PORT", "3306"))
DB_DATABASE = os.environ.get("DB_DATABASE", "we_mp")
DB_USERNAME = os.environ.get("DB_USERNAME", "root")
DB_PASSWORD = os.environ.get("DB_PASSWORD", "")

# 复用修复后的解析函数
from utils.news_store import parse_date_to_ts


def main():
    import argparse
    parser = argparse.ArgumentParser(description="回填 news_items.published_ts 历史空值")
    parser.add_argument("--apply", action="store_true",
                        help="实际执行回填（默认只 dry-run 预览）")
    args = parser.parse_args()

    print("=" * 60)
    print("  news_items.published_ts 历史回填")
    print(f"  目标库: {DB_DATABASE}@{DB_HOST}:{DB_PORT}")
    print(f"  模式:   {'APPLY（写库）' if args.apply else 'DRY-RUN（只预览，不写库）'}")
    print("=" * 60)

    conn = pymysql.connect(
        host=DB_HOST, port=DB_PORT, database=DB_DATABASE,
        user=DB_USERNAME, password=DB_PASSWORD, charset="utf8mb4",
    )
    try:
        cur = conn.cursor()

        # 1) 摸底：要修多少条
        cur.execute("SELECT COUNT(*) FROM news_items WHERE published_ts = 0 AND published_date != ''")
        total = cur.fetchone()[0]
        print(f"\n待回填总数: {total}")

        if total == 0:
            print("\n✅ 没有需要回填的数据，退出。")
            return

        # 2) 拉取所有待修记录，逐条解析
        cur.execute("""SELECT id, source_engine, published_date
                       FROM news_items
                       WHERE published_ts = 0 AND published_date != ''
                       ORDER BY id""")
        rows = cur.fetchall()

        fixes = []      # (new_ts, id) 成功解析的
        skipped = []    # 仍然解析失败的（异常样本）
        by_engine = {}  # 按引擎统计成功数

        from datetime import datetime
        for row_id, engine, pdate in rows:
            new_ts = parse_date_to_ts(pdate)
            if new_ts > 0:
                fixes.append((new_ts, row_id))
                by_engine[engine] = by_engine.get(engine, 0) + 1
            else:
                skipped.append((row_id, engine, pdate))

        print(f"\n解析成功: {len(fixes)} 条")
        print(f"解析失败: {len(skipped)} 条")
        print("\n按引擎成功分布:")
        for eng, cnt in sorted(by_engine.items(), key=lambda x: -x[1]):
            print(f"  {eng:25} {cnt}")

        # 预览前 5 个修复样本
        if fixes:
            print("\n修复样本（前5）:")
            cur.execute("""SELECT id, source_engine, published_date
                           FROM news_items WHERE id IN (%s)
                           ORDER BY id LIMIT 5"""
                        % ",".join(str(f[1]) for f in fixes[:5]))
            for rid, eng, pdate in cur.fetchall():
                ts = next(t for t, i in fixes if i == rid)
                readable = datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")
                print(f"  id={rid:5} {eng:12} {pdate[:35]:35} -> ts={ts} ({readable})")

        # 失败样本（如果有）
        if skipped:
            print(f"\n⚠️ 仍有 {len(skipped)} 条解析失败（请把样本贴给开发者）:")
            for rid, eng, pdate in skipped[:10]:
                print(f"  id={rid} engine={eng} published_date={pdate!r}")

        # 3) 实际写库
        if not args.apply:
            print(f"\n📝 DRY-RUN：以上是预览，未写库。")
            print(f"   确认无误后加 --apply 参数执行回填：")
            print(f"   python _backfill_published_ts.py --apply")
            return

        if not fixes:
            print("\n没有可修复的数据，退出。")
            return

        print(f"\n写入 {len(fixes)} 条更新...")
        # 批量参数化 UPDATE
        cur.executemany(
            "UPDATE news_items SET published_ts = %s WHERE id = %s",
            fixes,
        )
        conn.commit()
        print(f"✅ 完成：成功回填 {cur.rowcount} 条 published_ts")

        # 4) 复验
        cur.execute("SELECT COUNT(*) FROM news_items WHERE published_ts = 0 AND published_date != ''")
        remaining = cur.fetchone()[0]
        print(f"\n复验：剩余 published_ts=0 且有 date 的记录: {remaining}")
        if remaining == 0:
            print("🎉 全部回填完成")
        else:
            print(f"（剩余 {remaining} 条可能是格式异常，见上方失败样本）")

    finally:
        conn.close()


if __name__ == "__main__":
    main()
