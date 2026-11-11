import os
import requests
import json
import re
import time
from datetime import datetime, timedelta
import pandas as pd

# =================================================================
# 1. 환경 설정 (새 에어테이블 주소 반영 완료)
# =================================================================
DATA_GO_KR_KEY = os.environ.get("DATA_GO_KR_KEY", "f1116ba156f900a07b00c69c8eef2a81f701a8901229d58034d89b3cab48eb81")
AIRTABLE_PAT = os.environ.get("AIRTABLE_PAT", "").strip()
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
# 2. 데이터 정제 함수
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
    s = str(date_str).strip()
    if not s: return ""
    s_clean = re.sub(r'[./]', '-', s)
    try:
        dt = pd.to_datetime(s_clean, errors='coerce')
        if pd.notna(dt): return dt.strftime('%Y-%m-%d')
    except: pass
    return ""

def is_valid_current_contract(norm_date, today_str):
    if not norm_date: return False
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
# 4. 에어테이블 기존 창고 전수 검사 & 빈 줄/중복 자동 청소
# =================================================================
existing_records = []
offset = None
while True:
    params = {"offset": offset} if offset else {}
    res = requests.get(AIRTABLE_URL, headers=HEADERS, params=params)
    if res.status_code != 200:
        print(f"⚠️ 에어테이블 접근 확인 필요: {res.text}")
        break
    data = res.json()
    existing_records.extend(data.get("records", []))
    offset = data.get("offset")
    if not offset: break

seen_keys = {}
delete_ids = []
update_batch = []

for r in existing_records:
    rid = r["id"]
    f = r.get("fields", {})
    c_name = str(f.get("계약명", "")).strip()
    comp_name = str(f.get("업체명", "")).strip()
    raw_dt = str(f.get("계약일자", "")).strip()
    norm_dt = normalize_date(raw_dt)
    raw_biz = str(f.get("사업자번호", "")).strip()
    clean_biz = clean_biz_no(raw_biz)
    
    # 테이블 생성 시 기본으로 들어있는 빈 행(1, 2, 3번 줄)이나 2026년 이전 데이터 자동 삭제
    if not c_name or not norm_dt or norm_dt < "2026-01-01" or norm_dt > today_str:
        delete_ids.append(rid); continue
    key = (c_name, comp_name, norm_dt)
    if key in seen_keys:
        delete_ids.append(rid); continue
    seen_keys[key] = rid
    if raw_dt != norm_dt or raw_biz != clean_biz:
        update_batch.append({"id": rid, "fields": {"계약일자": norm_dt, "사업자번호": clean_biz}})

if delete_ids:
    for i in range(0, len(delete_ids), 10):
        requests.delete(AIRTABLE_URL, headers=HEADERS, params=[("records[]", x) for x in delete_ids[i:i+10]])
        time.sleep(0.2)
if update_batch:
    for i in range(0, len(update_batch), 10):
        requests.patch(AIRTABLE_URL, headers=HEADERS, json={"records": update_batch[i:i+10]})
        time.sleep(0.2)

# =================================================================
# 5. 에어테이블에 신규 데이터 Upsert 동기화
# =================================================================
records_to_upsert = []
for key, item_data in collected_map.items():
    core_fields = {
        "계약명": item_data["계약명"], "업체명": item_data["업체명"],
        "사업자번호": item_data["사업자번호"], "계약금액": item_data["계약금액"],
        "계약일자": item_data["계약일자"], "발주기관": item_data["발주기관"],
        "비고": item_data["비고"]
    }
    records_to_upsert.append({"fields": core_fields})

for i in range(0, len(records_to_upsert), 10):
    batch = records_to_upsert[i:i+10]
    res = requests.patch(AIRTABLE_URL, headers=HEADERS, json={
        "performUpsert": {"fieldsToMergeOn": ["계약명", "업체명", "계약일자"]},
        "records": batch
    })
    if res.status_code != 200 and "UNKNOWN_FIELD_NAME" in res.text:
        fallback = [{"fields": {k: v for k, v in rec["fields"].items() if k != "비고"}} for rec in batch]
        requests.patch(AIRTABLE_URL, headers=HEADERS, json={
            "performUpsert": {"fieldsToMergeOn": ["계약명", "업체명", "계약일자"]},
            "records": fallback
        })
    time.sleep(0.2)

# =================================================================
# 6. 크몽 판매용 마스터 CSV (clean_contracts.csv) 자동 생성 및 누적 저장
# =================================================================
csv_filename = "clean_contracts.csv"
new_df = pd.DataFrame(list(collected_map.values()))
try:
    old_df = pd.read_csv(csv_filename)
    combined_df = pd.concat([old_df, new_df], ignore_index=True)
except FileNotFoundError:
    combined_df = new_df

if not combined_df.empty:
    combined_df["계약일자"] = combined_df["계약일자"].apply(normalize_date)
    combined_df["사업자번호"] = combined_df["사업자번호"].apply(clean_biz_no)
    combined_df = combined_df[(combined_df["계약일자"] >= "2026-01-01") & (combined_df["계약일자"] <= today_str)]
    combined_df = combined_df.drop_duplicates(subset=["계약명", "업체명", "계약일자"], keep="last")
    combined_df = combined_df.sort_values(by=["계약일자", "계약금액"], ascending=[False, False]).reset_index(drop=True)
    combined_df.to_csv(csv_filename, index=False, encoding="utf-8-sig")
    print(f"💾 마스터 CSV({csv_filename}) 총 {len(combined_df)}건 저장 완료!")
