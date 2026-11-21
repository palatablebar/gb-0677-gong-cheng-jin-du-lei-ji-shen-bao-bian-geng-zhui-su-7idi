import csv,io,json
FIELDS=("claim_id","period","item","claimed_change","applied_change","change_chain",
        "effective_interval","inherited_text","cap","cap_source","reversal_rule",
        "reversal_rule_source","used_quantity","remaining_quantity","increment_qty",
        "unit_price","gross_amount","expected","difference","status")
def build_archive(run):
    stream=io.StringIO(newline="");writer=csv.writer(stream)
    writer.writerow(["run_id","input_sha256","rule_version","kind","source_lines","reason","evidence"]+list(FIELDS))
    meta=run["manifest"]
    for row in run["stages"][-1]["rows"]:
        writer.writerow([run["run_id"],meta["input_sha256"],meta["rule_version"],"accepted",
                         json.dumps(row.get("_sources",[])),row.get("status",""),
                         json.dumps(row,ensure_ascii=False,default=str)]
                        +[row.get(field,"") for field in FIELDS])
    for row in run["stages"][-1].get("cumulative_quarantine",[]):
        writer.writerow([run["run_id"],meta["input_sha256"],meta["rule_version"],
                         "quarantine",json.dumps(row.get("_sources",[])),
                         row.get("reason",""),json.dumps(row,ensure_ascii=False,default=str)]
                        +[row.get(field,"") for field in FIELDS])
    measurement=run.get("measurement") or {}
    for chain in measurement.get("chains",[]):
        writer.writerow([run["run_id"],meta["input_sha256"],meta["rule_version"],
                         "measurement",json.dumps({}),"",
                         json.dumps(chain,ensure_ascii=False,default=str)]
                        +[chain.get(field,"") for field in FIELDS])
    return "﻿"+stream.getvalue()
