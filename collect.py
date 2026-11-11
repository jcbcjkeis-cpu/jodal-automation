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

DATA_URLS = [
    ("물품", "https://apis.data.go.kr/1230000/ao/CntrctInfoService/getCntrctInfoListThng"),
    ("용역", "https://apis.data.go.kr/1230000/ao/CntrctInfoService/getCntrctInfoListServc"),
    ("공사", "https://apis.data.go.kr/1230000/ao/CntrctInfoService/getCntrctInfoListCnstwk")
]
AIRTABLE_URL = f"https://api.airtable.com/v0/{AIRTABLE_BASE_ID}/{AIRTABLE_TABLE_ID}"
HEADERS = {
    "Authorization": f"Bearer {AIRTABLE_PAT}",
    "Content-Type": "application/json"
}

# =================================================================
# 2. 데이터 정제 헬퍼 함수 (2자리 연도 및 공백 포함 날짜 완벽 호환)
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
    if len(parts) >= 4 and parts[3].strip(): corp_name = parts[3].strip()
    if len(parts) >= 5 and parts[4].strip(): ceo_name = parts[4].strip()
    if len(parts) >= 10: biz_no = clean_biz_no(parts[9])
    elif len(parts) > 4: biz_no = clean_biz_no(parts[-1])
    return corp_name, ceo_name, biz_no

def normalize_date(date_str):
    """'26. 8. 10.', '2026.5.4', '2026-05-04' 등 모든 날짜 형식을 YYYY-MM-DD로 안전 변환"""
    s = str(date_str).strip().rstrip('.')
    if not s or s.lower() == 'nan':
        return ""
    s_clean = re.sub(r'[\.\s/]+', '-', s).strip('-')
    parts = s_clean.split('-')
    if len(parts) == 3:
        y, m, d = parts[0], parts[1], parts[2]
        if len(y) == 2:
            y = "20" + y
        try:
            return f"{int(y):04d}-{int(m):02d}-{int(d):02d}"
        except:
            pass
    try:
        dt = pd.to_datetime(s_clean, errors='coerce')
        if pd.notna(dt):
            return dt.strftime('%Y-%m-%d')
    except:
        pass
    return ""

def is_valid_current_contract(norm_date, today_str):
    if not norm_date:
        return False
    return "2026-01-01" <= norm_date <= today_str

def extract_region(inst_name):
    s = str(inst_name).strip()
    mapping = [
        ('서울', '서울'), ('부산', '부산'), ('대구', '대구'), ('인천', '인천'),
        ('광주', '광주'), ('대전', '대전'), ('울산', '울산'), ('세종', '세종'),
        ('경기', '경기'), ('강원', '강원'), ('충청북도', '충북'), ('충북', '충북'),
        ('충청남도', '충남'), ('충남', '충남'), ('전북특별자치도', '전북'), ('전라북도', '전북'), ('전북', '전북'),
        ('전라남도', '전남'), ('전남', '전남'), ('경상북도', '경북'), ('경북', '경북'),
        ('경상남도', '경남'), ('경남', '경남'), ('제주', '제주')
    ]
    for key, val in mapping:
        if key in s[:10]: return val
    return "전국/중앙부처"

def build_remarks(cntrct_nm, thtm_amt, tot_amt, method_nm, category_nm):
    tags = [f"[{category_nm}]"]
    if method_nm: tags.append(method_nm)
    final_amt = thtm_amt if thtm_amt > 0 else tot_amt
    if final_amt == 0: tags.append("금액미정/단가계약")
    elif final_amt < 1000000: tags.append("소액/단가계약")
    elif "단가" in cntrct_nm or "연간" in cntrct_nm: tags.append("연간단가계약")
    else: tags.append("일반수의계약")
    return " / ".join(tags)

# =================================================================
# 3. 조달청 API 수집 (최근 14일치 물품/용역/공사 통합 스캔)
# =================================================================
now = datetime.now()
today_str = now.strftime("%Y-%m-%d")
two_weeks_ago = now - timedelta(days=14)
inqryBgnDt = two_weeks_ago.strftime("%Y%m%d0000")
inqryEndDt = now.strftime("%Y%m%d%H%M")

print(f"📅 조회 기간: {inqryBgnDt} ~ {inqryEndDt}")
collected_map = {}

for cat_name, api_url in DATA_URLS:
    for page in range(1, 6):
        params = {
            'serviceKey': DATA_GO_KR_KEY, 'type': 'json', 'inqryDiv': '1',
            'inqryBgnDt': inqryBgnDt, 'inqryEndDt': inqryEndDt,
            'numOfRows': '100', 'pageNo': str(page)
        }
        try:
            res = requests.get(api_url, params=params, timeout=15)
            if res.status_code != 200: break
            items = res.json().get('response', {}).get('body', {}).get('items', [])
            if not items: break
            for item in items:
                method_nm = str(item.get('cntrctCnclsMthdNm', '')).strip()
                if "수의" not in method_nm: continue
                raw_date = str(item.get('cntrctCnclsDate', '')).strip() or str(item.get('cntrctDate', '')).strip()
                norm_date = normalize_date(raw_date)
                if not is_valid_current_contract(norm_date, today_str): continue
                
                corp_name, ceo_name, biz_no = parse_corp_list(item.get('corpList', ''))
                cntrct_nm = str(item.get('cntrctNm', '무제명 계약')).strip()
                try: thtm_amt = int(float(item.get('thtmCntrctAmt', 0) or 0))
                except: thtm_amt = 0
                try: tot_amt = int(float(item.get('totCntrctAmt', 0) or 0))
                except: tot_amt = 0
                final_amt = thtm_amt if thtm_amt > 0 else tot_amt
                
                cntrct_instt = str(item.get('cntrctInsttNm', '공공기관')).strip()
                dept_nm = str(item.get('cntrctInsttOfclDeptNm', '') or item.get('cntrctInsttChrgDeptNm', '')).strip()
                ofcl_tel = str(item.get('cntrctInsttOfclTelNo', '')).strip()
                dtl_url = str(item.get('cntrctDtlInfoUrl', '')).strip()
                unty_cntrct_no = str(item.get('untyCntrctNo', '')).strip()
                region = extract_region(cntrct_instt)
                remarks = build_remarks(cntrct_nm, thtm_amt, tot_amt, method_nm, cat_name)
                
                extra_info = [f"분야:{cat_name}", f"지역:{region}"]
                if ceo_name != "-": extra_info.append(f"대표:{ceo_name}")
                if dept_nm: extra_info.append(f"부서:{dept_nm}")
                if ofcl_tel: extra_info.append(f"연락처:{ofcl_tel}")
                
                key = (cntrct_nm, corp_name, norm_date)
                collected_map[key] = {
                    "계약명": cntrct_nm, "업체명": corp_name, "사업자번호": biz_no,
                    "계약금액": final_amt, "계약일자": norm_date, "발주기관": cntrct_instt,
                    "비고": f"{remarks} ({', '.join(extra_info)})", "지역": region,
                    "계약방법": method_nm, "대표자명": ceo_name, "담당부서": dept_nm,
                    "담당자전화": ofcl_tel, "계약번호": unty_cntrct_no, "상세링크": dtl_url
                }
            time.sleep(0.3)
        except Exception as e:
            print(f"⚠️ {cat_name} {page}페이지 오류: {e}")

print(f"✅ 조달청 신규 정제 데이터 {len(collected_map)}건 확보 완료.")

# =================================================================
# 4. 에어테이블 기존 데이터 스캔 & 안전 정제 (날짜 파싱 실패 시 삭제 금지!)
# =================================================================
existing_records =
