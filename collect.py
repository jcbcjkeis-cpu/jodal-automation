import os
import requests
import json
import re
import time
from datetime import datetime, timedelta
import pandas as pd

# =================================================================
# 1. 환경 설정
# =================================================================
DATA_GO_KR_KEY = os.environ.get("DATA_GO_KR_KEY", "f1116ba156f900a07b00c69c8eef2a81f701a8901229d58034d89b3cab48eb81").strip()
AIRTABLE_PAT = os.environ.get("AIRTABLE_PAT", "patYy6iGFmqD0qLlY.8b706bc8107841a449678868788e05a647c455df70cf0ae9a1842d4c9fbf934f").strip()
AIRTABLE_BASE_ID = "appuPX40djuy2emNF"
AIRTABLE_TABLE_ID = "tblAfxWeOchBvwbha"

DATA_URLS = list([
    ("물품", "https://apis.data.go.kr/1230000/ao/CntrctInfoService/getCntrctInfoListThng"),
    ("용역", "https://apis.data.go.kr/1230000/ao/CntrctInfoService/getCntrctInfoListServc"),
    ("공사", "https://apis.data.go.kr/1230000/ao/CntrctInfoService/getCntrctInfoListCnstwk")
])
AIRTABLE_URL = f"https://api.airtable.com/v0/{AIRTABLE_BASE_ID}/{AIRTABLE_TABLE_ID}"
HEADERS = {
    "Authorization": f"Bearer {AIRTABLE_PAT}",
    "Content-Type": "application/json"
}

# =================================================================
# 2. 데이터 정제 헬퍼 함수
# =================================================================
def clean_biz_no(raw_str):
    digits = re.sub(r'[^0-9]', '', str(raw_str))
    if len(digits) >= 10:
        d = digits[:10]
        return f"{d[:3]}-{d[3:5]}-{d[5:]}"
    return "000-00-00000"

def parse_corp_list(corp_list_str):
    corp_name, ceo_name, biz_no = "상호명 미표기", "-", "000-00-00000"
    if not corp_list_str:
        return corp_name, ceo_name, biz_no
    first_corp = str(corp_list_str).split('],[')[0].strip('[]')
    parts = first_corp.split('^')
    if len(parts) >= 4 and parts[3].strip():
        corp_name = parts[3].strip()
    if len(parts) >= 5 and parts[4].strip():
        ceo_name = parts[4].strip()
    if len(parts) >= 10:
        biz_no = clean_biz_no(parts[9])
    elif len(parts) > 4:
        biz_no = clean_biz
