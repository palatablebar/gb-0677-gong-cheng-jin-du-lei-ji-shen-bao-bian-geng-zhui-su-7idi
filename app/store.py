import json
import sqlite3
from app.config import DATA_DIR
from app.errors import DomainError


def connect():
    DATA_DIR.mkdir(parents=True,exist_ok=True)
    db=sqlite3.connect(DATA_DIR/"records.db",timeout=20)
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("CREATE TABLE IF NOT EXISTS runs (id TEXT PRIMARY KEY,payload TEXT NOT NULL,version INTEGER NOT NULL DEFAULT 1,decision TEXT NOT NULL DEFAULT '待复核')")
    # 签认结果单列保存：发布失败时不改动旧版，重试同一结果只认一条，原申报不被覆盖。
    db.execute("""CREATE TABLE IF NOT EXISTS publications (
        result_id TEXT PRIMARY KEY,
        run_id TEXT NOT NULL,
        signature TEXT NOT NULL,
        measurement TEXT NOT NULL,
        decision TEXT NOT NULL DEFAULT '待复核',
        version INTEGER NOT NULL DEFAULT 1,
        created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
        updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
    )""")
    db.execute("CREATE INDEX IF NOT EXISTS publications_run ON publications(run_id)")
    db.commit()
    return db


def save(run_id,payload):
    with connect() as db:
        db.execute("INSERT OR IGNORE INTO runs(id,payload) VALUES (?,?)",(run_id,json.dumps(payload,ensure_ascii=False)))
    return get(run_id)


def get(run_id):
    with connect() as db: row=db.execute("SELECT payload,version,decision FROM runs WHERE id=?",(run_id,)).fetchone()
    if not row: raise DomainError("NOT_FOUND","批次不存在",404)
    payload={**json.loads(row[0]),"version":row[1],"decision":row[2]}
    if payload.get("stages"):
        # 优先复用入库时封存的计量结果，规则升级后重读也不会改写已发布内容。
        measurement=dict(payload.get("measurement") or {})
        if not measurement.get("chains"):
            from app.measurement import summarize
            measurement=summarize(payload["stages"][-1]["rows"],row[2])
        measurement["unmatched"]=len(payload["stages"][-1].get("cumulative_quarantine",[]))
        measurement.pop("published",None);measurement.pop("publication_version",None)
        published=publication(measurement.get("result_id",""))
        measurement["published"]=bool(published)
        measurement["publication_version"]=published["version"] if published else None
        if published:
            measurement["signed"]=published["decision"]=="已复核"
            measurement["decision"]=published["decision"]
        payload["measurement"]=measurement
    return payload


def latest():
    with connect() as db: row=db.execute("SELECT id FROM runs ORDER BY rowid DESC LIMIT 1").fetchone()
    return get(row[0]) if row else None


def history():
    with connect() as db: return [{"run_id":i,"version":v,"decision":d} for i,v,d in db.execute("SELECT id,version,decision FROM runs ORDER BY rowid DESC")]


def review(run_id,version,decision):
    if decision not in {"已复核","退回复核"}: raise DomainError("BAD_DECISION","复核状态无效")
    with connect() as db:
        cursor=db.execute("UPDATE runs SET version=version+1,decision=? WHERE id=? AND version=?",(decision,run_id,version))
        if cursor.rowcount!=1:
            if not db.execute("SELECT 1 FROM runs WHERE id=?",(run_id,)).fetchone(): raise DomainError("NOT_FOUND","批次不存在",404)
            raise DomainError("STALE_VERSION","版本已更新，请刷新",409)
    return get(run_id)


def _row(record):
    return {"result_id":record[0],"run_id":record[1],"signature":record[2],
            "measurement":json.loads(record[3]),"decision":record[4],
            "version":record[5],"created_at":record[6],"updated_at":record[7]}


def publication(result_id):
    with connect() as db:
        row=db.execute("SELECT result_id,run_id,signature,measurement,decision,version,created_at,updated_at FROM publications WHERE result_id=?",(result_id,)).fetchone()
    return _row(row) if row else None


def publications_for(run_id):
    with connect() as db:
        return [_row(row) for row in db.execute(
            "SELECT result_id,run_id,signature,measurement,decision,version,created_at,updated_at FROM publications WHERE run_id=? ORDER BY created_at,rowid",
            (run_id,))]


def publish(run_id,signature,measurement):
    """原子发布本期计量结果。

    - 同一 result_id 只在首次发布时写入，重试返回原记录，不重复计量；
    - 旧的签认结果保持不动，发布失败不会回退或覆盖上次成功版本；
    - 原申报覆盖后无法恢复：签名不一致视为对已发布结果的改写，直接 409，
      不删除、不更新任何既有记录。
    """
    if not isinstance(measurement,dict): raise DomainError("BAD_MEASUREMENT","计量结果需要对象")
    result_id=str(measurement.get("result_id") or "")
    if not result_id: raise DomainError("BAD_MEASUREMENT","计量结果缺少唯一编号")
    signature=str(signature or "")
    current=publication(result_id)
    if current:
        # 同一结果编号、同一内容只认一条；重复提交不同运行批次也不新增记录。
        if current["signature"]==signature:
            return {**current,"created":False,"idempotent":True}
        raise DomainError("PUBLISH_CONFLICT","同一结果编号的计量内容已变化，不能改写已发布结果",409)
    with connect() as db:
        try:
            db.execute("INSERT INTO publications(result_id,run_id,signature,measurement,decision,version) VALUES (?,?,?,?,?,?)",
                       (result_id,run_id,signature,json.dumps(measurement,ensure_ascii=False,default=str),"待复核",1))
        except sqlite3.IntegrityError:
            existing=db.execute("SELECT result_id,run_id,signature,measurement,decision,version,created_at,updated_at FROM publications WHERE result_id=?",(result_id,)).fetchone()
            if existing and existing[2]==signature:
                return {**_row(existing),"created":False,"idempotent":True}
            raise DomainError("PUBLISH_CONFLICT","同一结果编号的计量内容已变化，不能改写已发布结果",409)
    return {**publication(result_id),"created":True,"idempotent":False}


def sign(result_id,version,decision="已复核"):
    if decision not in {"已复核","退回复核"}: raise DomainError("BAD_DECISION","复核状态无效")
    with connect() as db:
        cursor=db.execute("UPDATE publications SET decision=?,version=version+1,updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE result_id=? AND version=? AND decision<>?",
                          (decision,result_id,version,"已复核"))
        if cursor.rowcount!=1:
            row=db.execute("SELECT decision FROM publications WHERE result_id=?",(result_id,)).fetchone()
            if not row: raise DomainError("NOT_FOUND","计量结果未发布",404)
            if row[0]=="已复核": raise DomainError("ALREADY_SIGNED","本期结果已签认，不得改写",409)
            raise DomainError("STALE_VERSION","版本已更新，请刷新",409)
    return publication(result_id)
