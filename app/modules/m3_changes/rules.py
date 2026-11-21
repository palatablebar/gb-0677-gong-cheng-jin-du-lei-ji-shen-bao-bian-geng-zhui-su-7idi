from app import changes
from app.reasons import reject
from app.errors import DomainError
CHANGES={"C0":{"approved":True,"effective":"2026-01","price":"100.00"},"C1":{"approved":True,"effective":"2026-11","price":"120.00"},"DRAFT":{"approved":False,"effective":"2026-10","price":"140.00"}}
def evaluate(rows,context):
    params=(context or {}).get("parameters",{})
    definitions=changes.definitions(params) if ("changes" in params or "supplements" in params) else CHANGES
    try: catalog=changes.build(definitions)
    except DomainError as error:
        raise DomainError(error.code,f"变更定义无效: {error.message}")
    good=[];bad=[]
    for row in rows:
        version,error=changes.applicable(catalog,row.get("change_id"),row.get("period",""),row.get("item"))
        if error:
            code,detail=error;bad.append(reject(row,code,detail));continue
        good.append({**row,**changes.row_fields(version,row.get("change_id")),"unit_price":version["price"]})
    summary=changes.metrics(catalog,good)
    return {"rows":good,"quarantine":bad,"metrics":{"accepted":len(good),"versions":sorted(set(r["approved_change"] for r in good)),**summary}}
