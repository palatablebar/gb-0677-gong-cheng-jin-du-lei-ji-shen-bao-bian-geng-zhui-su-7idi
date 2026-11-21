"""变更链解析：补充变更只改单价，未改条款向原变更继承。

术语：
- 变更链（chain）以最早的原变更为根；补充变更通过 parent/supplement_to
  指针挂到原变更上。
- 补充变更必须重述单价与生效起点；数量上限（cap）、冲回规则（reversal）、
  适用清单范围（items）、区间终止（end）未重述时向原变更继承。
- 生效区间为 [effective, end)，end 为空表示不限。每笔申报按 period 在其
  所属变更链内选择当时已批且生效的版本，因此补充变更不会整份替代历史单价，
  也不会因为独立成新单而丢掉原变更的前期剩余额度。
"""
from datetime import datetime

from app.errors import DomainError
from app.values import number

PARENT_KEYS=("supplements","supplement_to","supplements_change","supersedes",
             "superseded","replaces","replacement_of","base","base_change",
             "parent","parent_change","extends","extend","inherits","based_on",
             "original","original_change")
START_KEYS=("effective","effective_from","effective_start","valid_from","start","from")
END_KEYS=("end","ends","effective_end","effective_to","valid_until","until","to","expires")
PRICE_KEYS=("price","unit_price","rate")
CAP_KEYS=("cap","quantity_cap","qty_cap","max_qty","max_quantity","quantity_limit","limit","quota")
REVERSAL_KEYS=("reversal","reversal_rule","reversal_policy","reverse_rule")
ITEM_KEYS=("item","items")
REPLACEMENT_KEYS=("mode","replacement","replace","full_replacement","full_replace","independent")
INHERITABLE=("cap","reversal","items","end")
FIELD_KEYS={"cap":CAP_KEYS,"reversal":REVERSAL_KEYS,"items":ITEM_KEYS,"end":END_KEYS}
TERM_LABELS={"cap":"数量上限","reversal":"冲回规则","items":"适用清单","end":"区间终止"}


def term_text(terms):
    return "、".join(TERM_LABELS.get(x,x) for x in terms)


def _first(raw,keys):
    for key in keys:
        if key in raw and raw[key] not in (None,""): return raw[key]
    return None


def _stated(raw,field):
    return any(key in raw and raw[key] not in (None,"") for key in FIELD_KEYS[field])


def _as_map(defs):
    if defs is None: return {}
    if isinstance(defs,list):
        out={}
        for entry in defs:
            if not isinstance(entry,dict): raise DomainError("BAD_CHANGES","变更定义需要对象")
            cid=str(entry.get("id") or entry.get("change_id") or "")
            if not cid: raise DomainError("BAD_CHANGES","变更定义缺少编号")
            out[cid]={k:v for k,v in entry.items() if k not in {"id","change_id"}}
        return out
    if not isinstance(defs,dict): raise DomainError("BAD_CHANGES","变更定义需要对象")
    return {str(k):v for k,v in defs.items()}


def definitions(params):
    """取出变更定义；supplements 里的条目并入同一张表并标注父级。

    支持三种写法：
    - changes 里直接写 supplement_to/parent；
    - supplements 平铺 {"S1": {...}}（需自带父级）；
    - supplements 按父级分组 {"C1": {"S1": {...}}}。
    """
    params=params or {}
    base=_as_map(params.get("changes"))
    supplements=params.get("supplements")
    if not isinstance(supplements,dict): return base
    for key,value in supplements.items():
        if not isinstance(value,dict): continue
        if _first(value,PRICE_KEYS) is not None:
            base[str(key)]=value
        else:
            for cid,raw in value.items():
                if isinstance(raw,dict): base[str(cid)]={**raw,"supplement_to":str(key)}
    return base


def month(value):
    text=str(value).strip()
    if len(text)!=7: raise DomainError("CHANGE_NOT_EFFECTIVE","变更生效月份需要YYYY-MM")
    try:
        parsed=datetime.strptime(text,"%Y-%m")
    except ValueError:
        raise DomainError("CHANGE_NOT_EFFECTIVE","变更生效月份需要YYYY-MM")
    if parsed.strftime("%Y-%m")!=text: raise DomainError("CHANGE_NOT_EFFECTIVE","变更生效月份需要YYYY-MM")
    return text


def _approved(raw,parent):
    if "approved" in raw:
        value=raw.get("approved")
        if isinstance(value,bool): return value
        return str(value).strip().lower() in {"true","1","yes","approved","已批","已批准","批准"}
    status=str(raw.get("status","")).strip().lower()
    if status in {"approved","已批","已批准","批准"}: return True
    if status in {"draft","pending","待批","待审批","未批"}: return False
    # 审批状态不属于可继承条款：补充变更必须自行明确已批，否则不得参与计价。
    return False


def _items(raw,parent):
    value=_first(raw,ITEM_KEYS)
    if value is None:
        return (parent["items"],parent["id"]) if parent and parent["items"] else (None,None)
    values=value if isinstance(value,(list,tuple,set)) else [value]
    return frozenset(str(v) for v in values),None


def _reversal(raw,parent):
    value=_first(raw,REVERSAL_KEYS)
    if value is not None:
        if isinstance(value,bool): return ("approved" if value else "not_required"),None
        return str(value).strip().lower(),None
    if parent: return parent["reversal"],parent["reversal_source"]
    return "approved",None


def _replacement_requested(raw):
    for key in REPLACEMENT_KEYS:
        value=raw.get(key)
        if isinstance(value,bool):
            if value: return True
            continue
        if isinstance(value,str) and value.strip().lower() in {"replace","replacement","full","whole","independent","new","整份替代","独立新单"}:
            return True
    return False


def build(defs):
    """解析变更定义，返回 {versions, errors}。错误按变更编号隔离，不拖垮其他链。"""
    mapping=_as_map(defs)
    versions={}
    errors={}
    resolving=[]

    def resolve(cid):
        if cid in versions: return versions[cid]
        if cid in errors:
            code,detail=errors[cid]; raise DomainError(code,detail)
        if cid in resolving: raise DomainError("CHANGE_CYCLE","补充变更引用形成闭环")
        raw=mapping.get(cid)
        if not isinstance(raw,dict): raise DomainError("UNKNOWN_REFERENCE","变更定义不存在")
        resolving.append(cid)
        try:
            parent_id=_first(raw,PARENT_KEYS)
            parent=None
            if parent_id is not None:
                parent_id=str(parent_id)
                if parent_id not in mapping:
                    raise DomainError("UNKNOWN_REFERENCE",f"补充变更引用的原变更不存在: {parent_id}")
                parent=resolve(parent_id)
            approved=_approved(raw,parent)
            start_raw=_first(raw,START_KEYS)
            if start_raw is None:
                if parent is None: raise DomainError("CHANGE_NOT_EFFECTIVE","原变更缺少生效月份")
                start=parent["effective"]; start_source=parent["id"]
            else:
                start=month(start_raw); start_source=cid
            end_raw=_first(raw,END_KEYS)
            if end_raw is not None: end=month(end_raw); end_source=cid
            elif parent and parent["end"] and parent["end"]>start:
                end=parent["end"]; end_source=parent["end_source"]
            else: end=None; end_source=None
            if end is not None and end<=start:
                raise DomainError("BAD_EFFECTIVE_INTERVAL","变更生效区间为空")
            price_raw=_first(raw,PRICE_KEYS)
            if price_raw is None: raise DomainError("MISSING_PRICE","变更必须重述单价")
            price=number(price_raw)
            if price<0: raise DomainError("BAD_PRICE","单价不能为负")
            cap_raw=_first(raw,CAP_KEYS)
            if cap_raw is not None:
                cap=number(cap_raw)
                if cap<0: raise DomainError("BAD_CAP","数量上限不能为负")
                cap_source=cid
            elif parent: cap=parent["cap"]; cap_source=parent["cap_source"]
            else: cap=None; cap_source=None
            reversal,reversal_source=_reversal(raw,parent)
            items,items_source=_items(raw,parent)
            inherited=[]
            if parent:
                for field in INHERITABLE:
                    if _stated(raw,field): continue
                    if field=="end" and end is None: continue
                    if field=="items" and items is None: continue
                    if field=="cap" and cap is None: continue
                    if field=="reversal" and reversal in (None,""): continue
                    inherited.append(field)
            replacement=_replacement_requested(raw)
            item={"id":cid,"parent":parent["id"] if parent else None,
                  "chain":parent["chain"] if parent else cid,
                  "depth":(parent["depth"]+1) if parent else 0,
                  "approved":approved,"effective":start,"effective_source":start_source,
                  "end":end,"end_source":end_source,"price":str(price),
                  "cap":str(cap) if cap is not None else None,"cap_source":cap_source,
                  "reversal":reversal,"reversal_source":reversal_source or cid,
                  "items":items,"items_source":items_source,
                  "inherited":inherited,"replacement_requested":replacement}
            versions[cid]=item
            return item
        finally:
            resolving.pop()

    for cid in mapping:
        if cid in versions or cid in errors: continue
        try: resolve(cid)
        except DomainError as error: errors[cid]=(error.code,error.message)
    changed=True
    while changed:
        changed=False
        for cid,raw in mapping.items():
            if cid in errors or not isinstance(raw,dict): continue
            parent_id=_first(raw,PARENT_KEYS)
            if parent_id is not None and str(parent_id) in errors:
                errors[cid]=errors[str(parent_id)];changed=True
    return {"versions":versions,"errors":errors,"definitions":mapping}


def validate(defs):
    catalog=build(defs)
    if catalog["errors"]:
        cid=sorted(catalog["errors"])[0];code,detail=catalog["errors"][cid]
        raise DomainError(code,f"{cid}: {detail}")
    return catalog


def _scope_ok(version,item):
    scope=version["items"]
    return scope is None or (item is not None and str(item) in scope)


def applicable(catalog,change_id,period,item=None):
    """为某笔申报裁决适用版本。

    返回 (version, None) 或 (None, (code,detail))。同一链条内按 period 选择
    已批且处于 [effective,end) 的生效起点最晚版本：补充变更自其生效月份起
    接管，此前周期仍按原变更计价，不整份替代历史单价。申报行引用的变更必须
    已批且覆盖该清单；引用版本被更新的补充变更取代时，按现行版本计价并保留
    申报来源，便于逐笔追溯。
    """
    cid=str(change_id) if change_id is not None else ""
    if cid in catalog["errors"]:
        code,detail=catalog["errors"][cid];return None,(code,detail)
    referenced=catalog["versions"].get(cid)
    if not referenced: return None,("UNAPPROVED_CHANGE","变更尚未批准")
    if not referenced["approved"]: return None,("UNAPPROVED_CHANGE","变更尚未批准")
    if not _scope_ok(referenced,item):
        return None,("CHANGE_ITEM_MISMATCH",f"变更{referenced['id']}不适用于清单{item}")
    if not period or period<referenced["effective"]:
        # 引用的版本本身尚未生效，不能拿链上旧版本替它计价。
        return None,("CHANGE_NOT_EFFECTIVE","变更未生效")
    root=referenced["chain"]
    candidates=[v for v in catalog["versions"].values()
                if v["chain"]==root and v["approved"] and _scope_ok(v,item)]
    try:
        active=[v for v in candidates if v["effective"]<=period and (v["end"] is None or period<v["end"])]
    except TypeError:
        return None,("CHANGE_NOT_EFFECTIVE","变更未生效")
    if not active:
        if any(v["effective"]<=period for v in candidates):
            return None,("CHANGE_EXPIRED","变更已超出生效区间")
        return None,("CHANGE_NOT_EFFECTIVE","变更未生效")
    latest_start=max(v["effective"] for v in active)
    current=[v for v in active if v["effective"]==latest_start]
    if len(current)>1:
        current=[v for v in current if v["depth"]==max(x["depth"] for x in current)]
        if len(current)>1:
            ids="/".join(sorted(v["id"] for v in current))
            return None,("CHANGE_AMBIGUOUS",f"同一生效起点存在多个补充变更: {ids}")
    return current[0],None


def interval(version):
    return f"{version['effective']}~{version['end'] or '不限'}"


def row_fields(version,claimed):
    """写入申报行的追溯字段。未改条款保留原变更来源，供审计逐笔追原依据。"""
    inherited=sorted(version["inherited"])
    if version["replacement_requested"] and inherited:
        replacement_note="已申请整份替代，未重述条款仍向原变更继承"
    elif version["replacement_requested"]:
        replacement_note="已申请整份替代，且已重述全部条款"
    else:
        replacement_note=""
    return {
        "claimed_change":str(claimed) if claimed is not None else "",
        "approved_change":version["id"],
        "applied_change":version["id"],
        "change_chain":version["chain"],
        "change_root":version["chain"],
        "change_effective":version["effective"],
        "change_end":version["end"],
        "effective_interval":interval(version),
        "inherited_terms":inherited,
        "inherited_text":term_text(inherited),
        "cap":version["cap"],
        "quantity_cap":version["cap"],
        "cap_source":version["cap_source"],
        "reversal_rule":version["reversal"],
        "reversal_rule_source":version["reversal_source"],
        "replacement_requested":version["replacement_requested"],
        "replacement_note":replacement_note,
        "change_basis":{
            "claimed_change":str(claimed) if claimed is not None else "",
            "applied_change":version["id"],
            "chain":version["chain"],
            "parent":version["parent"],
            "price_source":version["id"],
            "effective":version["effective"],
            "end":version["end"],
            "inherited_terms":inherited,
            "cap_source":version["cap_source"],
            "reversal_rule_source":version["reversal_source"],
        },
    }


def metrics(catalog,rows):
    chains={}
    for row in rows:
        chain=row.get("change_chain") or row.get("approved_change")
        if not chain: continue
        entry=chains.setdefault(chain,{"chain":chain,"applied_changes":set(),
                                      "inherited_terms":set(),"cap":None,
                                      "cap_source":None,"reversal_rule":None,
                                      "effective_from":None,"effective_to":None})
        entry["applied_changes"].add(row.get("applied_change") or row.get("approved_change"))
        entry["inherited_terms"].update(row.get("inherited_terms",[]))
        start=row.get("change_effective")
        if start and (entry["effective_from"] is None or start<entry["effective_from"]): entry["effective_from"]=start
        end=row.get("change_end")
        if end is not None and (entry["effective_to"] is None or end>entry["effective_to"]): entry["effective_to"]=end
        if row.get("cap") is not None:
            entry["cap"]=row["cap"];entry["cap_source"]=row.get("cap_source")
        entry["reversal_rule"]=row.get("reversal_rule")
    summary=[]
    for entry in sorted(chains.values(),key=lambda x:x["chain"]):
        entry=dict(entry)
        entry["applied_changes"]=sorted(x for x in entry["applied_changes"] if x)
        entry["inherited_terms"]=sorted(entry["inherited_terms"])
        entry["inherited_text"]=term_text(entry["inherited_terms"])
        entry["effective_interval"]=f"{entry['effective_from'] or '—'}~{entry['effective_to'] or '不限'}"
        summary.append(entry)
    return {"chains":summary,
            "inherited_terms":sorted(set(x for c in summary for x in c["inherited_terms"])),
            "inherited_text":term_text(sorted(set(x for c in summary for x in c["inherited_terms"]))),
            "config_errors":[{"change":cid,"reason":code,"detail":detail}
                             for cid,(code,detail) in sorted(catalog["errors"].items())]}
