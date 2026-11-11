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

MAX_CSV_ROWS = 5000
MAX_AIRTABLE_ROWS = 900

DATA_URLS = (
    ("물품", "https://apis.data.go.kr/1230000/ao/CntrctInfoService/getCntrctInfoListThng"),
    ("용역", "https://apis.data.go.kr/1230000/ao/CntrctInfoService/getCntrctInfoListServc"),
    ("공사", "https://apis.data.go.kr/1230000/ao/CntrctInfoService/getCntrctInfoListCnstwk")
)
AIRTABLE_URL = f"https://api.airtable.com/v0/{AIRTABLE_BASE_ID}/{AIRTABLE_TABLE_ID}"
HEADERS = {
    "Authorization": f"Bearer {AIRTABLE_PAT}",
    "Content-Type": "application/json"
}

# =================================================================
# 2. 데이터 정제 헬퍼 함수 (대괄호 미사용 안전 버전)
# =================================================================
def clean_biz_no(raw_str):
    digits = "".join(ch for ch in str(raw_str) if ch.isdigit())
    if len(digits) >= 10:
        p1 = digits.__getitem__(slice(0, 3))
        p2 = digits.__getitem__(slice(3, 5))
        p3 = digits.__getitem__(slice(5, 10))
        return f"{p1}-{p2}-{p3}"
    return "000-00-00000"

def parse_corp_list(corp_list_str):
    corp_name, ceo_name, biz_no = "상호명 미표기", "-", "000-00-00000"
    if not corp_list_str:
        return corp_name, ceo_name, biz_no
    sep_token = chr(93) + "," + chr(91)
    strip_chars = chr(91) + chr(93)
    first_corp = str(corp_list_str).split(sep_token).__getitem__(0).strip(strip_chars)
    parts = first_corp.split("^")
    if len(parts) >= 4 and parts.__getitem__(3).strip():
        corp_name = parts.__getitem__(3).strip()
    if len(parts) >= 5 and parts.__getitem__(4).strip():
        ceo_name = parts.__getitem__(4).strip()
    if len(parts) >= 10:
        biz_no = clean_biz_no(parts.__getitem__(9))
    elif len(parts) > 4:
        biz_no = clean_biz_no(parts.__getitem__(-1))
    return corp_name, ceo_name, biz_no

def normalize_date(date_str):
    s = str(date_str).strip().rstrip(".")
    if not s or s.lower() == "nan":
        return ""
    s_clean = s.replace(".", "-").replace("/", "-").replace(" ", "-")
    while "--" in s_clean:
        s_clean = s_clean.replace("--", "-")
    s_clean = s_clean.strip("-")
    parts = s_clean.split("-")
    if len(parts) == 3:
        y = parts.__getitem__(0)
        m = parts.__getitem__(1)
        d = parts.__getitem__(2)
        if len(y) == 2:
            y = "20" + y
        try:
            return f"{int(y):04d}-{int(m):02d}-{int(d):02d}"
        except Exception:
            pass
    try:
        dt = pd.to_datetime(s_clean, errors="coerce")
        if pd.notna(dt):
            return dt.strftime("%Y-%m-%d")
    except Exception:
        pass
    return ""

def is_valid_current_contract(norm_date, today_str):
    if not norm_date:
        return False
    return "2026-01-01" <= norm_date <= today_str

def extract_region(inst_name):
    s = str(inst_name).strip().__getitem__(slice(0, 10))
    mapping = (
        ("서울", "서울"), ("부산", "부산"), ("대구", "대구"), ("인천", "인천"),
        ("광주", "광주"), ("대전", "대전"), ("울산", "울산"), ("세종", "세종"),
        ("경기", "경기"), ("강원", "강원"), ("충청북도", "충북"), ("충북", "충북"),
        ("충청남도", "충남"), ("충남", "충남"), ("전북특별자치도", "전북"), ("전라북도", "전북"), ("전북", "전북"),
        ("전라남도", "전남"), ("전남", "전남"), ("경상북도", "경북"), ("경북", "경북"),
        ("경상남도", "경남"), ("경남", "경남"), ("제주", "제주")
    )
    for key, val in mapping:
        if key in s:
            return val
    return "전국/중앙부처"

def build_remarks(cntrct_nm, thtm_amt, tot_amt, method_nm, category_nm):
    tags = list()
    tags.append(f"({category_nm})")
    if method_nm:
        tags.append(method_nm)
    final_amt = thtm_amt if thtm_amt > 0 else tot_amt
    if final_amt == 0:
        tags.append("금액미정/단가계약")
    elif final_amt < 1000000:
        tags.append("소액/단가계약")
    elif "단가" in cntrct_nm or "연간" in cntrct_nm:
        tags.append("연간단가계약")
    else:
        tags.append("일반수의계약")
    return " / ".join(tags)

# =================================================================
# 3. 조달청 API 수집 (최근 7일치 물품/용역/공사 통합 스캔)
# =================================================================
now = datetime.now()
today_str = now.strftime("%Y-%m-%d")
lookback_dt = now - timedelta(days=7)
inqryBgnDt = lookback_dt.strftime("%Y%m%d0000")
inqryEndDt = now.strftime("%Y%m%d%H%M")

print(f"조회 기간: {inqryBgnDt} ~ {inqryEndDt}")
collected_map = dict()

for cat_name, api_url in DATA_URLS:
    for page in range(1, 6):
        params = {
            "serviceKey": DATA_GO_KR_KEY,
            "type": "json",
            "inqryDiv": "1",
            "inqryBgnDt": inqryBgnDt,
            "inqryEndDt": inqryEndDt,
            "numOfRows": "100",
            "pageNo": str(page)
        }
        try:
            res = requests.get(api_url, params=params, timeout=15)
            if res.status_code != 200:
                break
            items = res.json().get("response", dict()).get("body", dict()).get("items", list())
            if not items:
                break
            for item in items:
                method_nm = str(item.get("cntrctCnclsMthdNm", "")).strip()
                if "수의" not in method_nm:
                    continue
                raw_date = str(item.get("cntrctCnclsDate", "")).strip() or str(item.get("cntrctDate", "")).strip()
                norm_date = normalize_date(raw_date)
                if not is_valid_current_contract(norm_date, today_str):
                    continue
                
                corp_name, ceo_name, biz_no = parse_corp_list(item.get("corpList", ""))
                cntrct_nm = str(item.get("cntrctNm", "무제명 계약")).strip()
                try:
                    thtm_amt = int(float(item.get("thtmCntrctAmt", 0) or 0))
                except Exception:
                    thtm_amt = 0
                try:
                    tot_amt = int(float(item.get("totCntrctAmt", 0) or 0))
                except Exception:
                    tot_amt = 0
                final_amt = thtm_amt if thtm_amt > 0 else tot_amt
                
                cntrct_instt = str(item.get("cntrctInsttNm", "공공기관")).strip()
                dept_nm = str(item.get("cntrctInsttOfclDeptNm", "") or item.get("cntrctInsttChrgDeptNm", "")).strip()
                ofcl_tel = str(item.get("cntrctInsttOfclTelNo", "")).strip()
                dtl_url = str(item.get("cntrctDtlInfoUrl", "")).strip()
                unty_cntrct_no = str(item.get("untyCntrctNo", "")).strip()
                region = extract_region(cntrct_instt)
                remarks = build_remarks(cntrct_nm, thtm_amt, tot_amt, method_nm, cat_name)
                
                extra_info = list()
                extra_info.append(f"분야:{cat_name}")
                extra_info.append(f"지역:{region}")
                if ceo_name != "-":
                    extra_info.append(f"대표:{ceo_name}")
                if dept_nm:
                    extra_info.append(f"부서:{dept_nm}")
                if ofcl_tel:
                    extra_info.append(f"연락처:{ofcl_tel}")
                
                key = (cntrct_nm, corp_name, norm_date)
                collected_map.__setitem__(key, {
                    "계약명": cntrct_nm,
                    "업체명": corp_name,
                    "사업자번호": biz_no,
                    "계약금액": final_amt,
                    "계약일자": norm_date,
                    "발주기관": cntrct_instt,
                    "비고": f"{remarks} ({', '.join(extra_info)})",
                    "지역": region,
                    "계약방법": method_nm,
                    "대표자명": ceo_name,
                    "담당부서": dept_nm,
                    "담당자전화": ofcl_tel,
                    "계약번호": unty_cntrct_no,
                    "상세링크": dtl_url
                })
            time.sleep(0.3)
        except Exception as e:
            print(f"{cat_name} {page}페이지 오류: {e}")

print(f"조달청 신규 정제 데이터 {len(collected_map)}건 확보 완료.")

# =================================================================
# 4. 에어테이블 기존 데이터 스캔, 중복 제거 및 900건 용량 자동 유지
# =================================================================
existing_records = list()
offset = None
while True:
    params = {"offset": offset} if offset else dict()
    res = requests.get(AIRTABLE_URL, headers=HEADERS, params=params)
    if res.status_code != 200:
        print(f"에어테이블 조회 오류: {res.text}")
        break
    data = res.json()
    existing_records.extend(data.get("records", list()))
    offset = data.get("offset")
    if not offset:
        break

seen_keys = dict()
valid_airtable_rows = list()
delete_ids = list()
update_batch = list()

for r in existing_records:
    rid = r.get("id")
    f = r.get("fields", dict())
    c_name = str(f.get("계약명", "")).strip()
    comp_name = str(f.get("업체명", "")).strip()
    raw_dt = str(f.get("계약일자", "")).strip()
    norm_dt = normalize_date(raw_dt)
    raw_biz = str(f.get("사업자번호", "")).strip()
    clean_biz = clean_biz_no(raw_biz)
    
    if not c_name and not comp_name:
        delete_ids.append(rid)
        continue
        
    if norm_dt and (norm_dt < "2026-01-01" or norm_dt > today_str):
        delete_ids.append(rid)
        continue
        
    effective_dt = norm_dt if norm_dt else raw_dt
    key = (c_name, comp_name, effective_dt)
    if key in seen_keys:
        delete_ids.append(rid)
        continue
    seen_keys.__setitem__(key, rid)
    valid_airtable_rows.append((effective_dt, rid))
    
    if (norm_dt and raw_dt != norm_dt) or (clean_biz != "000-00-00000" and raw_biz != clean_biz):
        upd_fields = dict()
        if norm_dt:
            upd_fields.__setitem__("계약일자", norm_dt)
        if clean_biz != "000-00-00000":
            upd_fields.__setitem__("사업자번호", clean_biz)
        if upd_fields:
            update_batch.append({"id": rid, "fields": upd_fields})

# =================================================================
# 5. 마스터 CSV 병합 및 최신 5,000건 롤링 유지 (구글 시트 2MB 초과 방지)
# =================================================================
csv_filename = "clean_contracts.csv"
master_df = pd.DataFrame()
for fname in ("clean_contracts.csv", "clean_contracts_master.csv"):
    if os.path.exists(fname):
        try:
            master_df = pd.read_csv(fname)
            print(f"기존 파일 로드 성공: {fname} ({len(master_df)}건)")
            break
        except Exception as e:
            print(f"{fname} 로드 오류: {e}")

new_df = pd.DataFrame(list(collected_map.values()))
combined_df = pd.concat((master_df, new_df), ignore_index=True) if not master_df.empty else new_df

if not combined_df.empty:
    combined_df.__setitem__("계약일자", combined_df.__getitem__("계약일자").apply(normalize_date))
    combined_df.__setitem__("사업자번호", combined_df.__getitem__("사업자번호").apply(clean_biz_no))
    cond = (combined_df.__getitem__("계약일자") >= "2026-01-01") & (combined_df.__getitem__("계약일자") <= today_str)
    combined_df = combined_df.loc.__getitem__(cond)
    combined_df = combined_df.drop_duplicates(subset=list(("계약명", "업체명", "계약일자")), keep="last")
    combined_df = combined_df.sort_values(by=list(("계약일자", "계약금액")), ascending=list((False, False))).reset_index(drop=True)
    combined_df = combined_df.head(MAX_CSV_ROWS)
    combined_df.to_csv(csv_filename, index=False, encoding="utf-8-sig")
    print(f"마스터 CSV({csv_filename}) 최신 {len(combined_df)}건 저장 완료!")

if len(seen_keys) < 100 and not combined_df.empty:
    target_rows = combined_df.head(800).to_dict("records")
    print(f"에어테이블 데이터 부족 감지(현재 {len(seen_keys)}건) -> 마스터 데이터 {len(target_rows)}건 즉시 복구 적재!")
else:
    target_rows = list(collected_map.values())

records_to_send = list()
for item_data in target_rows:
    c_nm = str(item_data.get("계약명", "")).strip()
    cp_nm = str(item_data.get("업체명", "")).strip()
    c_dt = normalize_date(item_data.get("계약일자", ""))
    if (c_nm, cp_nm, c_dt) in seen_keys:
        continue
    core_fields = {
        "계약명": c_nm,
        "업체명": cp_nm,
        "사업자번호": clean_biz_no(item_data.get("사업자번호", "")),
        "계약금액": int(float(item_data.get("계약금액", 0) or 0)),
        "계약일자": c_dt,
        "발주기관": str(item_data.get("발주기관", "")).strip(),
        "비고": str(item_data.get("비고", "")).strip()
    }
    records_to_send.append({"fields": core_fields})

projected_total = len(valid_airtable_rows) + len(records_to_send)
if projected_total > MAX_AIRTABLE_ROWS:
    excess_count = projected_total - MAX_AIRTABLE_ROWS
    valid_airtable_rows.sort(key=lambda x: x.__getitem__(0))
    for old_item in valid_airtable_rows.__getitem__(slice(0, excess_count)):
        delete_ids.append(old_item.__getitem__(1))
    print(f"에어테이블 무료 용량 보호: 가장 오래된 데이터 {excess_count}건 자동 비우기 예약.")

rec_param_key = "records" + chr(91) + chr(93)
if delete_ids:
    print(f"빈 줄, 중복 및 오래된 데이터 총 {len(delete_ids)}건 정리 중...")
    for i in range(0, len(delete_ids), 10):
        chunk = delete_ids.__getitem__(slice(i, i + 10))
        del_params = list((rec_param_key, x) for x in chunk)
        requests.delete(AIRTABLE_URL, headers=HEADERS, params=del_params)
        time.sleep(0.2)
if update_batch:
    print(f"포맷 교정 {len(update_batch)}건 업데이트 중...")
    for i in range(0, len(update_batch), 10):
        chunk = update_batch.__getitem__(slice(i, i + 10))
        requests.patch(AIRTABLE_URL, headers=HEADERS, json={"records": chunk})
        time.sleep(0.2)

print(f"에어테이블 창고에 신규 데이터 {len(records_to_send)}건 전송 시작...")
success_cnt = 0
for i in range(0, len(records_to_send), 10):
    batch = records_to_send.__getitem__(slice(i, i + 10))
    res = requests.post(AIRTABLE_URL, headers=HEADERS, json={"records": batch, "typecast": True})
    if res.status_code != 200 and "UNKNOWN_FIELD_NAME" in res.text:
        fallback = list({"fields": dict((k, v) for k, v in rec.get("fields", dict()).items() if k != "비고")} for rec in batch)
        res = requests.post(AIRTABLE_URL, headers=HEADERS, json={"records": fallback, "typecast": True})
    if res.status_code == 200:
        success_cnt += len(batch)
    else:
        print(f"적재 오류 (배치 {i}): {res.text}")
    time.sleep(0.25)

print(f"에어테이블 총 {success_cnt}건 적재 완료!")
