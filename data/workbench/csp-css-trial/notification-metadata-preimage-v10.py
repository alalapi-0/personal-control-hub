"""Fixed, secret-free metadata about rejected provider notifications."""
import hashlib
import json

from .service_contract import ServiceError

FIELDS=('threadId','turnId','thread','turn','status','account','rateLimits','tokenUsage','message','warning','serverName')
OUTCOMES=frozenset({'validated','schema_rejected'})


def inventory(schemas):
    schema=(schemas or {}).get('ServerNotification',{})
    rows=schema.get('oneOf',[schema])
    return frozenset(name for row in rows for name in row.get('properties',{}).get('method',{}).get('enum',[])
        if type(name)is str)


def json_type(value):
    if value is None:return 'null'
    if type(value)is bool:return 'boolean'
    if type(value)in (int,float):return 'number'
    if type(value)is str:return 'string'
    if type(value)is dict:return 'object'
    if type(value)is list:return 'array'
    return 'other'


def binding(params,kind,expected):
    direct=kind+'Id';nested=params.get(kind)
    present=direct in params or (type(nested)is dict and 'id'in nested)
    actual=params.get(direct)if direct in params else nested.get('id')if type(nested)is dict else None
    applicable=type(expected)is str
    matches=applicable and present and type(actual)is str and actual==expected
    return {'present':present,'matches':matches,'wrong':applicable and present and not matches,
        'not_applicable':not applicable or not present}


def observation(value,schemas,allowed,*,outcome,expected_thread=None,expected_turn=None):
    if outcome not in OUTCOMES:raise ServiceError('NOTIFICATION_METADATA_OUTCOME_REJECTED')
    names=inventory(schemas);method=value.get('method');known=type(method)is str and method in names
    category='allowlisted_known'if known and method in allowed else'rejected_known'if known else'unregistered'
    body=value.get('params');params=body if type(body)is dict else{}
    shape={'body_type':json_type(body),'fields':{name:{'present':name in params,
        'type':json_type(params[name])if name in params else'absent'}for name in FIELDS}}
    projection={'method_category':category,'method_name':method if known else'unregistered',
        'validation_outcome':outcome,'binding':{'thread':binding(params,'thread',expected_thread),
        'turn':binding(params,'turn',expected_turn)},'shape':shape}
    # Only the fixed projection is hashed. No raw strings, keys, credential
    # values, numeric values or body-length-derived data enter this digest.
    encoded=json.dumps(projection,sort_keys=True,separators=(',',':')).encode()
    try:
        size=len(json.dumps(body,ensure_ascii=False,allow_nan=False).encode())
        band='empty'if body in (None,{},[])else'1_256'if size<=256 else'257_1024'if size<=1024 else'over_1024'
    except (TypeError,ValueError):band='unavailable'
    return {**projection,'projection_digest':hashlib.sha256(encoded).hexdigest(),'body_size_band':band,
        'digest_scope':'fixed schema-safe projection; body size excluded','raw_body_retained':False,
        'raw_body_or_credential_hash_retained':False}
