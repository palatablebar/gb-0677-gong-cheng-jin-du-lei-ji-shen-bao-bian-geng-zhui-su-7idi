"""本期计量结果归并：按变更链形成唯一可签认结果。

工作台需要同时回答四件事：
- 现行变更（current_change）：本链在本期实际执行的最新版本；
- 继承条款（inherited_terms）：补充变更未重述、继续向原变更继承的条款；
- 生效区间（effective_interval）：现行版本适用的 [起, 止) 月份；
- 未签认余量（unsigned_remaining）：变更链数量上限扣减已采纳累计量后的
  剩余数量与金额，以及本期尚未签认的金额。

同一变更链只产生一条汇总，重试或重复输入不会产生第二条可签认结果。
"""
import hashlib
import json
from decimal import Decimal

from app.changes import term_text
from app.values import number

ZERO=Decimal(0)


def _dec(value,default=ZERO):
    if value in (None,""): return default
    try: return number(value)
    except Exception: return default


def _canonical(rows):
    return json.dumps(rows,sort_keys=True,ensure_ascii=False,default=str,separators=(",",":"))


def _interval(from_period,to_period):
    return {"from":from_period,"to":to_period,"text":f"{from_period or '—'}~{to_period or '不限'}"}


def summarize(rows,decision=None):
    groups={};order=[]
    for row in rows:
        chain=row.get("change_chain") or row.get("change_root") or row.get("approved_change") or row.get("change_id") or "(未指定变更)"
        key=(chain,row.get("item"))
        if key not in groups:
            groups[key]={"chain":chain,"item":row.get("item"),"rows":[]};order.append(key)
        groups[key]["rows"].append(row)
    chains=[]
    for key in order:
        group=groups[key];members=group["rows"]
        periods=sorted(str(r.get("period")) for r in members if r.get("period"))
        current_period=periods[-1] if periods else None
        applied=[r for r in members if r.get("applied_change") or r.get("approved_change")]
        current_row=None
        for row in applied:
            if row.get("period")==current_period: current_row=row;break
        if current_row is None and applied: current_row=applied[-1]
        current_change=(current_row.get("applied_change") or current_row.get("approved_change")) if current_row else None
        versions=sorted(set(x for x in (r.get("applied_change") or r.get("approved_change") for r in members) if x))
        inherited=sorted(set(x for r in members for x in (r.get("inherited_terms") or [])))
        chain_start=min((r.get("change_effective") for r in members if r.get("change_effective")),default=None)
        chain_end=None
        for row in members:
            value=row.get("change_end")
            if value and (chain_end is None or value>chain_end): chain_end=value
        # 生效区间取现行版本自身的 [生效, 止)；链的完整跨度另列为 chain_interval。
        current_interval=current_row.get("effective_interval") if current_row else None
        current_from=current_row.get("change_effective") if current_row else None
        current_to=current_row.get("change_end") if current_row else None
        if current_from is None: current_from=chain_start;current_to=chain_end
        if not current_interval: current_interval=_interval(current_from,current_to)["text"]
        cap_values=[r.get("quantity_cap") for r in members if r.get("quantity_cap") is not None]
        cap=current_row.get("quantity_cap") if current_row and current_row.get("quantity_cap") is not None else (cap_values[-1] if cap_values else None)
        # 已用额度以最新周期的已采纳累计量为准（已批冲回会合法下调累计量）。
        used=ZERO;seen_used=False
        for row in sorted(members,key=lambda r:(str(r.get("period") or ""),)):
            for field in ("used_quantity","cumulative_qty","approved_previous"):
                if row.get(field) is not None:
                    used=_dec(row.get(field));seen_used=True;break
        if not seen_used:
            used=max((_dec(r.get("cumulative_qty")) for r in members),default=ZERO)
        price=_dec(current_row.get("unit_price"),ZERO) if current_row else ZERO
        remaining_qty=(_dec(cap)-used) if cap is not None else None
        remaining_amount=(remaining_qty*price) if remaining_qty is not None else None
        pending=sum((_dec(r.get("expected")) for r in members),ZERO)
        diff=sum((_dec(r.get("difference")) for r in members),ZERO)
        signable=all(bool(r.get("applied_change") or r.get("approved_change") or not r.get("change_id")) for r in members)
        chains.append({
            "chain":group["chain"],"item":group["item"],
            "measurement_id":hashlib.sha256(_canonical({"chain":group["chain"],"item":group["item"],
                "current_change":current_change,"effective_from":current_from,"effective_to":current_to,
                "cap":cap,"used":str(used),"pending":str(pending),"difference":str(diff),
                "records":len(members)}).encode()).hexdigest()[:20],
            "current_change":current_change,
            "current_change_effective":current_from,
            "applied_changes":versions,
            "inherited_terms":inherited,"inherited_text":term_text(inherited),
            "effective_interval":_interval(current_from,current_to),
            "chain_interval":_interval(chain_start,chain_end),
            "effective_from":current_from,"effective_to":current_to,
            "chain_from":chain_start,"chain_to":chain_end,"current_period":current_period,
            "cap":cap,"used_quantity":str(used),
            "unsigned_remaining_quantity":str(remaining_qty) if remaining_qty is not None else None,
            "unsigned_remaining_amount":str(remaining_amount) if remaining_amount is not None else None,
            "unit_price":str(price) if current_row else None,
            "pending_amount":str(pending),"difference_total":str(diff),
            "records":len(members),"signable":signable,
        })
    # 本期主导链：当前周期最晚的一条；同期按记录数、链号稳定排序。
    def rank(c):
        return (c["current_period"] or "",c.get("current_change_effective") or "",c["records"],c["chain"])
    primary=max(chains,key=rank) if chains else None
    capped=[c for c in chains if c["cap"] is not None]
    pending_total=sum((_dec(c["pending_amount"]) for c in chains),ZERO)
    if len(capped)==1:
        remaining_amount=capped[0]["unsigned_remaining_amount"]
        remaining_qty=capped[0]["unsigned_remaining_quantity"]
        remaining_scope=capped[0]["chain"]+"/"+str(capped[0]["item"])
    elif len(capped)>1:
        remaining_amount=str(sum((_dec(c["unsigned_remaining_amount"]) for c in capped),ZERO))
        remaining_qty=None;remaining_scope="多清单，见分链"
    else:
        remaining_amount=None;remaining_qty=None;remaining_scope=None
    result={
        "chains":chains,
        "signable":all(c["signable"] for c in chains) if chains else True,
        "signed":decision=="已复核","decision":decision,
        "current_change":primary["current_change"] if primary else None,
        "inherited_terms":primary["inherited_terms"] if primary else [],
        "inherited_text":primary["inherited_text"] if primary else "",
        "effective_interval":primary["effective_interval"] if primary else _interval(None,None),
        "unsigned_remaining":{
            "scope":remaining_scope,
            "quantity":remaining_qty,"amount":remaining_amount,
            "unit_price":primary["unit_price"] if primary else None,
            "cap":primary["cap"] if primary else None,
            "used":primary["used_quantity"] if primary else None,
            "pending_amount":str(pending_total),
            "difference_total":str(sum((_dec(c["difference_total"]) for c in chains),ZERO)),
            "signed":decision=="已复核",
            "by_chain":[{"chain":c["chain"],"item":c["item"],"cap":c["cap"],
                         "used":c["used_quantity"],"quantity":c["unsigned_remaining_quantity"],
                         "amount":c["unsigned_remaining_amount"]} for c in chains],
        },
    }
    result["result_id"]=hashlib.sha256(_canonical(chains).encode()).hexdigest()[:20]
    result["measurement_id"]=result["result_id"]
    return result
