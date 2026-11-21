from decimal import Decimal
from app.values import number
from app.reasons import reject
from app.errors import DomainError
EVIDENCE_RULES={"approved","required","per_claim","evidence","需冲回凭证"}


def _rule(value,fallback="approved"):
    text=str(value or fallback).strip().lower()
    return text if text else fallback


def evaluate(rows,context):
    """累计还原并核算变更链额度。

    数量上限属于整条变更链：补充变更未重述上限时继承原变更的上限，前期在原
    变更下已申报的累计量继续占用额度，不会因为补充变更独立成新单而重新计满。
    """
    good=[];bad=[];overrides=(context or {}).get("parameters",{}).get("previous_revisions",{})
    used={};caps={};seen=set()
    ordered=sorted(rows,key=lambda x:(x["period"],x["item"],x["claim_id"]))
    for row in ordered:
        key=(row["item"],row["period"])
        if key in seen:
            bad.append(reject(row,"DUPLICATE_PERIOD","同清单同周期重复申报"));continue
        seen.add(key)
        prev=number(overrides.get(row["claim_id"],row["previous_qty"]));current=number(row["cumulative_qty"])
        if prev<0:raise DomainError("BAD_PREVIOUS","前审累计量不能为负")
        incremental=current-prev
        rule=_rule(row.get("reversal_rule"),"approved")
        if incremental<0 and rule in EVIDENCE_RULES and row.get("reversal")!="approved":
            bad.append(reject(row,"REVERSAL_REQUIRED","负进度缺少已批冲回凭证"));continue
        chain=row.get("change_chain") or row.get("approved_change")
        bucketed=(chain,row["item"])
        cap_text=row.get("quantity_cap")
        cap=number(cap_text) if cap_text is not None else None
        used_before=used.get(bucketed,Decimal(0))
        if prev>used_before: used_before=prev
        if cap is not None and current>cap:
            bad.append(reject(row,"CAP_EXCEEDED",
                f"变更链{chain}清单{row['item']}数量上限{cap}，已用{used_before}，剩{cap-used_before}，本次累计{current}"));continue
        after=current
        used[bucketed]=after
        remaining=None if cap is None else str(cap-after)
        if cap is not None:
            caps[bucketed]={"chain":chain,"item":row["item"],"cap":str(cap),
                            "cap_source":row.get("cap_source"),
                            "used":str(after),"remaining":remaining,
                            "remaining_quantity":remaining}
        good.append({**row,"approved_previous":str(prev),"increment_qty":str(incremental),
                     "gross_amount":str(incremental*number(row["unit_price"])),
                     "used_quantity":str(after),"remaining_quantity":remaining,
                     "cap_remaining":remaining,"cap_source":row.get("cap_source"),
                     "reversal_rule":rule})
    return {"rows":good,"quarantine":bad,"metrics":{"accepted":len(good),
        "negative_claims":sum(number(r["increment_qty"])<0 for r in good),
        "caps":[caps[k] for k in sorted(caps,key=lambda x:(str(x[0]),str(x[1])))],
        "cap_remaining_total":str(sum((number(c["remaining"]) for c in caps.values()),Decimal(0))) if caps else None}}
