from app.modules.m3_changes.rules import evaluate
def row(change="C1",period="2026-11"):return {"change_id":change,"period":period}
def test_real_unapproved_rejected(result): assert any(r["reason"]=="UNAPPROVED_CHANGE" for r in result["stages"][2]["quarantine"])
def test_effective_boundary():
    "TRAP-23"
    assert evaluate([row()],{})["rows"][0]["unit_price"]=="120.00"
def test_before_effective(): assert evaluate([row(period="2026-10")],{})["quarantine"][0]["reason"]=="CHANGE_NOT_EFFECTIVE"
def test_draft_price_not_used(): assert evaluate([row("DRAFT")],{})["rows"]==[]
def test_unknown_change(): assert evaluate([row("X")],{})["quarantine"][0]["reason"]=="UNAPPROVED_CHANGE"
def test_real_original_and_change_versions(result): assert result["stages"][2]["metrics"]["versions"]==["C0","C1"]
def test_negative_price_rejected(): assert evaluate([row()],{"parameters":{"changes":{"C1":{"approved":True,"effective":"2026-01","price":"-1"}}}})["quarantine"][0]["reason"]=="BAD_PRICE"


CHAIN={"C1":{"approved":True,"effective":"2026-11","price":"100.00",
             "cap":"100","reversal":"approved","item":"001"},
       "S1":{"supplement_to":"C1","approved":True,"effective":"2027-01","price":"130.00"}}
def chain_row(claim,period,change,item="001",reversal="none"):
    return {"claim_id":claim,"item":item,"period":period,"change_id":change,"reversal":reversal}
def test_supplement_only_from_effective_month():
    "补充变更只改单价，生效前周期仍按原变更计价"
    rows=[chain_row("A","2026-12","C1"),chain_row("B","2027-01","S1"),chain_row("C","2027-03","S1")]
    result=evaluate(rows,{"parameters":{"changes":CHAIN}})
    prices={r["claim_id"]:r["unit_price"] for r in result["rows"]}
    assert prices=={"A":"100.00","B":"130.00","C":"130.00"}
def test_unrestated_terms_inherit_from_original_change():
    "未重述的数量上限与冲回规则继续向原变更继承"
    row=evaluate([chain_row("B","2027-02","S1")],{"parameters":{"changes":CHAIN}})["rows"][0]
    assert row["applied_change"]=="S1" and row["change_chain"]=="C1"
    assert row["inherited_terms"]==["cap","items","reversal"]
    assert row["quantity_cap"]=="100" and row["cap_source"]=="C1"
    assert row["reversal_rule"]=="approved" and row["reversal_rule_source"]=="C1"
    assert row["change_basis"]["cap_source"]=="C1" and row["change_basis"]["price_source"]=="S1"
def test_supplement_must_restate_price():
    bad={"C1":CHAIN["C1"],"S1":{"supplement_to":"C1","approved":True,"effective":"2027-01"}}
    result=evaluate([chain_row("B","2027-02","S1")],{"parameters":{"changes":bad}})
    assert result["quarantine"][0]["reason"]=="MISSING_PRICE"
def test_whole_replacement_request_does_not_drop_inheritance():
    "现场要求整份替代签证时，未重述条款仍按原变更继承"
    defs={"C1":CHAIN["C1"],"S1":{**CHAIN["S1"],"mode":"replace"}}
    row=evaluate([chain_row("B","2027-02","S1")],{"parameters":{"changes":defs}})["rows"][0]
    assert row["inherited_terms"]==["cap","items","reversal"]
    assert row["replacement_requested"] is True and "继承" in row["replacement_note"]
def test_effective_interval_end_is_exclusive():
    "补充变更的区间为[start,end)，止月之后回到原变更，不沿用补充单价"
    defs={"C1":CHAIN["C1"],"S1":{**CHAIN["S1"],"end":"2027-03"}}
    rows=[chain_row("B","2027-02","S1"),chain_row("C","2027-03","S1")]
    result=evaluate(rows,{"parameters":{"changes":defs}})
    prices={r["claim_id"]:(r["applied_change"],r["unit_price"]) for r in result["rows"]}
    assert prices=={"B":("S1","130.00"),"C":("C1","100.00")}
    assert result["quarantine"]==[]
def test_unapproved_supplement_rejected():
    defs={"C1":CHAIN["C1"],"S1":{**CHAIN["S1"],"approved":False}}
    assert evaluate([chain_row("B","2027-02","S1")],{"parameters":{"changes":defs}})["quarantine"][0]["reason"]=="UNAPPROVED_CHANGE"
def test_supplement_cycle_rejected():
    defs={"S1":{"supplement_to":"S2","price":"1","effective":"2027-01"},
          "S2":{"supplement_to":"S1","price":"2","effective":"2027-02"}}
    assert evaluate([chain_row("B","2027-02","S1")],{"parameters":{"changes":defs}})["quarantine"][0]["reason"]=="CHANGE_CYCLE"
def test_grouped_supplements_shorthand():
    params={"changes":{"C1":CHAIN["C1"]},
            "supplements":{"C1":{"S1":{"approved":True,"effective":"2027-01","price":"130.00"}}}}
    row=evaluate([chain_row("B","2027-02","S1")],{"parameters":params})["rows"][0]
    assert row["applied_change"]=="S1" and row["inherited_terms"]==["cap","items","reversal"]
def test_chain_metrics_expose_inheritance():
    rows=[chain_row("A","2026-12","C1"),chain_row("B","2027-02","S1")]
    m=evaluate(rows,{"parameters":{"changes":CHAIN}})["metrics"]
    assert m["chains"][0]["chain"]=="C1"
    assert m["chains"][0]["applied_changes"]==["C1","S1"]
    assert m["chains"][0]["inherited_terms"]==["cap","items","reversal"]
