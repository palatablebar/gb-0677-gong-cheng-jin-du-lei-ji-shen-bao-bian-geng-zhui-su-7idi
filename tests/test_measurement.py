from app.pipeline import calculate
from app.measurement import summarize
CHAIN={"C1":{"approved":True,"effective":"2026-11","price":"100.00",
             "cap":"100","reversal":"approved","item":"001"},
       "S1":{"supplement_to":"C1","approved":True,"effective":"2027-01","price":"130.00"}}
HEADER="claim_id,item,period,cumulative_qty,previous_qty,change_id,reversal,reported,note\n"
LINES=["P1,001,2026-12,40,0,C1,none,4000,首期",
       "P2,001,2027-02,90,40,S1,none,6500,补充变更后",
       "P3,001,2027-03,130,90,S1,none,5200,超限"]
def run(): return calculate((HEADER+"\n".join(LINES)+"\n").encode(),{"changes":CHAIN})
def test_single_signable_result_names_current_change():
    "工作台只产出一条可签认结果，说明现行变更、继承条款、生效区间与未签认余量"
    m=run()["measurement"]
    assert m["current_change"]=="S1"
    assert m["inherited_terms"]==["cap","items","reversal"]
    assert m["effective_interval"]["text"]=="2027-01~不限"
    assert m["chains"][0]["chain_interval"]["text"]=="2026-11~不限"
    assert m["unsigned_remaining"]["quantity"]=="10"
    assert m["unsigned_remaining"]["amount"]=="1300.00"
    assert m["unsigned_remaining"]["scope"]=="C1/001"
    assert m["signable"] is True
def test_cap_excess_kept_out_of_measurement():
    m=run()["measurement"]
    assert m["unsigned_remaining"]["used"]=="90"
    assert m["unmatched"]==1
def test_row_level_traceability():
    "每一笔申报都能追到原依据：申报版本、适用版本、条款来源"
    rows=run()["stages"][3]["rows"]
    latest=[r for r in rows if r["claim_id"]=="P2"][0]
    assert latest["claimed_change"]=="S1" and latest["applied_change"]=="S1"
    assert latest["change_basis"]["cap_source"]=="C1"
    assert latest["change_basis"]["reversal_rule_source"]=="C1"
    assert latest["change_basis"]["price_source"]=="S1"
    assert latest["_sources"]==[3]
def test_result_id_is_stable_for_same_input():
    a=run()["measurement"];b=run()["measurement"]
    assert a["result_id"]==b["result_id"]
def test_measurement_survives_repeat_calculation():
    first=run()["measurement"];second=run()["measurement"]
    assert first["chains"]==second["chains"]
def test_pending_amount_matches_deduction_stage():
    result=run()
    assert result["measurement"]["unsigned_remaining"]["pending_amount"]==result["stages"][4]["metrics"]["net_total"]
def test_empty_measurement_is_safe():
    m=summarize([])
    assert m["chains"]==[] and m["signable"] is True and m["result_id"]
