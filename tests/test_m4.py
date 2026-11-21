from app.modules.m4_increment.rules import evaluate
def claim(id="A",current="125",previous="100",reversal="none"):return {"claim_id":id,"item":"001","period":"2026-11","cumulative_qty":current,"previous_qty":previous,"unit_price":"120","reversal":reversal}
def test_naive_cumulative_would_double_pay():
    r=evaluate([claim()],{})["rows"][0];assert r["increment_qty"]=="25" and r["gross_amount"]=="3000"
def test_previous_approval_revision():
    r=evaluate([claim()],{"parameters":{"previous_revisions":{"A":"110"}}})["rows"][0];assert r["increment_qty"]=="15" and r["gross_amount"]=="1800"
def test_negative_without_evidence(): assert evaluate([claim(current="90")],{})["quarantine"][0]["reason"]=="REVERSAL_REQUIRED"
def test_approved_reversal(): assert evaluate([claim(current="90",reversal="approved")],{})["rows"][0]["increment_qty"]=="-10"
def test_same_period_not_double_counted(): assert len(evaluate([claim("A"),claim("B")],{})["quarantine"])==1
def test_real_negative_isolated(result): assert any(r["reason"]=="REVERSAL_REQUIRED" for r in result["stages"][3]["quarantine"])
def test_zero_current_period_allowed(): assert evaluate([claim(current="100")],{})["rows"][0]["gross_amount"]=="0"
def test_real_sample_32_progress_rows(result): assert len(result["stages"][3]["rows"])==32


CHAIN={"C1":{"approved":True,"effective":"2026-11","price":"100.00",
             "cap":"100","reversal":"approved","item":"001"},
       "S1":{"supplement_to":"C1","approved":True,"effective":"2027-01","price":"130.00"}}
def chain_claim(claim,period,change,current,previous,reversal="none"):
    return {"claim_id":claim,"item":"001","period":period,"cumulative_qty":current,
            "previous_qty":previous,"change_id":change,"reversal":reversal,
            "unit_price":"130" if change=="S1" else "100","reported":"0",
            "change_chain":"C1","quantity_cap":"100","reversal_rule":"approved",
            "applied_change":change,"cap_source":"C1"}
def test_cap_inherited_by_supplement_counts_earlier_quantity():
    "补充变更不重置原变更已用额度：前40+后50=90，剩余10"
    result=evaluate([chain_claim("P1","2026-12","C1","40","0"),
                     chain_claim("P2","2027-02","S1","90","40")],{})
    rows={r["claim_id"]:r for r in result["rows"]}
    assert rows["P2"]["increment_qty"]=="50" and rows["P2"]["gross_amount"]=="6500"
    assert rows["P2"]["remaining_quantity"]=="10"
    assert result["metrics"]["caps"][0]=={"chain":"C1","item":"001","cap":"100","cap_source":"C1",
                                          "used":"90","remaining":"10","remaining_quantity":"10"}
def test_cap_exceeded_after_supplement_is_quarantined():
    result=evaluate([chain_claim("P1","2026-12","C1","40","0"),
                     chain_claim("P2","2027-02","S1","120","40")],{})
    assert result["quarantine"][0]["reason"]=="CAP_EXCEEDED"
    assert "上限100" in result["quarantine"][0]["detail"] and "剩60" in result["quarantine"][0]["detail"]
def test_independent_new_change_would_lose_remaining_quota():
    "若补充变更另起新链（无继承上限），额度会被重新计满；继承后不会"
    standalone={"quantity_cap":None}
    row={**chain_claim("P2","2027-02","S1","90","40"),**standalone}
    result=evaluate([row],{})
    assert result["metrics"]["caps"]==[] and result["metrics"]["cap_remaining_total"] is None
def test_negative_reversal_restores_remaining_quota():
    result=evaluate([chain_claim("P1","2026-12","C1","60","0"),
                     chain_claim("P2","2027-02","S1","90","60"),
                     chain_claim("P3","2027-03","S1","80","90","approved")],{})
    assert [r["increment_qty"] for r in result["rows"]]==["60","30","-10"]
    assert result["rows"][-1]["remaining_quantity"]=="20"
def test_inherited_reversal_rule_still_requires_evidence():
    row={**chain_claim("P2","2027-02","S1","80","90"),"reversal":"none"}
    assert evaluate([row],{})["quarantine"][0]["reason"]=="REVERSAL_REQUIRED"
