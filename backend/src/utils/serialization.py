import hashlib, json


def checksum(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def dataset_dict(d):
    return {'id':d.id, 'name':d.name, 'source':d.source, 'rows':len(d.records), 'checksum':d.checksum, 'created_at':d.created_at}


def attempt_dict(a, job=None):
    data = {k: getattr(a, k) for k in ['id','job_id','attempt_no','status','worker','created_at',
                                       'started_at','finished_at','error','cancel_reason',
                                       'takeover_of','takeover_reason']}
    data['lease_expires_at'] = job.lease_expires_at if (job is not None and job.attempt_no == a.attempt_no and job.status == 'running') else None
    data['current'] = job is not None and job.attempt_no == a.attempt_no
    return data


def job_dict(j, attempts=None):
    data = {k: getattr(j, k) for k in ['id','name','dataset_id','params','status','result','error',
                                       'approved','created_at','finished_at','attempt_no',
                                       'cancel_reason','lease_owner','lease_expires_at']}
    data['attempts'] = [attempt_dict(a, j) for a in (attempts or [])]
    return data


def report_dict(r):
    return {k:getattr(r,k) for k in ['id','name','job_id','destination','interval_seconds','next_run','enabled']}


def delivery_dict(d):
    return {k:getattr(d,k) for k in ['id','report_id','created_at','checksum','filename','destination']}
