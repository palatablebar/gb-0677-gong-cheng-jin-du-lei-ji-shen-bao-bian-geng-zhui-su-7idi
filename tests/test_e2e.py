import csv,io
from app.pipeline import execute,load_sample
from app import store
def test_full_sample_flow(client):
    r=client.post("/api/runs",json={"sample":"dirty.csv"});assert r.status_code==200
    p=r.get_json();assert len(p["stages"])==7
    assert p["stages"][5]["metrics"]["balanced"] is True
def test_idempotent_real_input(sample):
    a=execute(sample);b=execute(sample)
    assert a["run_id"]==b["run_id"] and len(store.history())==1
def test_stale_review_409(client):
    "TRAP-26 陈旧复核不得覆盖新版本"
    p=client.post("/api/runs",json={"sample":"dirty.csv"}).get_json();u="/api/runs/"+p["run_id"]+"/review"
    assert client.post(u,json={"version":1,"decision":"已复核"}).status_code==200
    assert client.post(u,json={"version":1,"decision":"退回复核"}).status_code==409
def test_export_has_manifest_and_quarantine(client):
    p=client.post("/api/runs",json={"sample":"dirty.csv"}).get_json()
    r=client.get("/api/runs/"+p["run_id"]+"/archive")
    rows=list(csv.reader(io.StringIO(r.data.decode("utf-8-sig"))))
    assert rows[0][1]=="input_sha256" and any(x[3]=="quarantine" for x in rows[1:])
def test_failed_import_is_atomic(client):
    assert client.post("/api/runs",json={"csv":"bad"}).status_code==422
    assert store.history()==[]
def test_module_entrances(client):
    from app.config import MODULES
    for slug,name in MODULES:
        assert client.get("/api/"+slug).status_code==200
        assert client.get("/"+slug).status_code==200
def test_missing_run_404(client): assert client.get("/api/runs/missing").status_code==404


def publish(client,run_id): return client.post(f"/api/runs/{run_id}/publish",json={})
def test_publish_is_idempotent_for_same_chain(client):
    "同一变更链重试发布不能重复计量"
    p=client.post("/api/runs",json={"sample":"dirty.csv"}).get_json()
    first=publish(client,p["run_id"]).get_json()
    second=publish(client,p["run_id"]).get_json()
    assert first["created"] is True and second["created"] is False
    assert first["result_id"]==second["result_id"] and second["version"]==1
    assert len(client.get(f"/api/runs/{p['run_id']}/publications").get_json()["publications"])==1
def test_publish_conflict_keeps_old_version(client):
    "内容变化不得改写已发布结果，旧版保留"
    p=client.post("/api/runs",json={"sample":"dirty.csv"}).get_json()
    record=publish(client,p["run_id"]).get_json()
    tampered={**record["measurement"],"result_id":record["result_id"],"unsigned_remaining":{"quantity":"0"}}
    from app import store
    import pytest
    from app.errors import DomainError
    with pytest.raises(DomainError) as error:
        store.publish(p["run_id"],"different-signature",tampered)
    assert error.value.status==409 and error.value.code=="PUBLISH_CONFLICT"
    kept=store.publication(record["result_id"])
    assert kept["version"]==1 and kept["decision"]=="待复核"
    assert len(store.publications_for(p["run_id"]))==1
def test_failed_publish_keeps_previous(client):
    "发布失败后继续保留旧版"
    p=client.post("/api/runs",json={"sample":"dirty.csv"}).get_json()
    ok=publish(client,p["run_id"]).get_json()
    assert client.post("/api/runs/missing/publish",json={}).status_code==404
    assert client.post(f"/api/runs/{p['run_id']}/publish",json={"unexpected":1}).status_code==200
    current=client.get(f"/api/runs/{p['run_id']}/publications").get_json()["publications"]
    assert [x["result_id"] for x in current]==[ok["result_id"]]
def test_sign_once_and_stale_sign(client):
    p=client.post("/api/runs",json={"sample":"dirty.csv"}).get_json()
    published=publish(client,p["run_id"]).get_json();rid=published["result_id"]
    assert client.post(f"/api/publications/{rid}/sign",json={"version":1,"decision":"已复核"}).status_code==200
    again=client.post(f"/api/publications/{rid}/sign",json={"version":1,"decision":"退回复核"})
    assert again.status_code==409 and again.get_json()["code"]=="ALREADY_SIGNED"
    assert client.post(f"/api/publications/{rid}/sign",json={"version":2,"decision":"退回复核"}).status_code==409
def test_republish_after_sign_does_not_duplicate_or_reset(client):
    p=client.post("/api/runs",json={"sample":"dirty.csv"}).get_json()
    published=publish(client,p["run_id"]).get_json();rid=published["result_id"]
    client.post(f"/api/publications/{rid}/sign",json={"version":1,"decision":"已复核"})
    again=publish(client,p["run_id"]).get_json()
    assert again["created"] is False and again["decision"]=="已复核" and again["version"]==2
    assert len(client.get(f"/api/runs/{p['run_id']}/publications").get_json()["publications"])==1
def test_original_claim_rows_are_never_overwritten(client):
    "原申报覆盖后无法恢复的情况不得出现：发布只增记录，不改写批次与源行"
    p=client.post("/api/runs",json={"sample":"dirty.csv"}).get_json()
    before=client.get(f"/api/runs/{p['run_id']}").get_json()
    publish(client,p["run_id"])
    after=client.get(f"/api/runs/{p['run_id']}").get_json()
    assert after["stages"]==before["stages"]
    assert after["manifest"]["input_sha256"]==before["manifest"]["input_sha256"]
    assert client.get(f"/api/runs/{p['run_id']}/archive").status_code==200
def test_archive_includes_measurement_and_basis(client):
    p=client.post("/api/runs",json={"sample":"dirty.csv"}).get_json()
    publish(client,p["run_id"])
    text=client.get(f"/api/runs/{p['run_id']}/archive").data.decode("utf-8-sig")
    header=text.splitlines()[0].split(",")
    for field in ("applied_change","effective_interval","inherited_text","cap_source","remaining_quantity"):
        assert field in header
    assert any(line.startswith(p["run_id"]) and ",measurement," in line for line in text.splitlines())
def test_published_measurement_is_reported_on_run(client):
    p=client.post("/api/runs",json={"sample":"dirty.csv"}).get_json()
    first=publish(client,p["run_id"]).get_json()
    detail=client.get(f"/api/runs/{p['run_id']}").get_json()
    assert detail["measurement"]["published"] is True
    assert detail["measurement"]["result_id"]==first["result_id"]


def test_same_chain_reimport_does_not_duplicate_result(client):
    "同一变更链换批次重算（内容相同）也只认一条结果"
    from app import store
    a=client.post("/api/runs",json={"sample":"clean.csv"}).get_json()
    b=client.post("/api/runs",json={"csv":open("sample_data/clean.csv",encoding="utf-8-sig").read()}).get_json()
    first=client.post(f"/api/runs/{a['run_id']}/publish",json={}).get_json()
    second=client.post(f"/api/runs/{b['run_id']}/publish",json={}).get_json()
    assert first["result_id"]==second["result_id"]
    assert second["created"] is False and second["version"]==1
    assert len(store.publications_for(a["run_id"]))==1
