#!/bin/bash
# 每日投注簿 xlsx 生成（8:10 抓取+比分回填后运行, 账本结算最全）
cd /data/data/com.termux/files/home/football-dashboard || exit 1
python3 scripts/gen_ledger_xlsx.py
