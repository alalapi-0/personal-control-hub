"""Bounded nonsecret text input policy. No provider text enters denial records."""
import copy
from .service_contract import ServiceError
from .workbench_store import canonical

METHOD='item/tool/requestUserInput'

def _text(value,limit):
    return type(value)is str and bool(value.strip()) and '\x00'not in value and len(value)<=limit

def input_policy(request,thread_id,turn_id):
    deny={'decisions':['decline'],'reason':'input_unsupported'}
    if type(request)is not dict or set(request)!={'id','method','params'} or request['method']!=METHOD:return deny
    if type(request['id'])not in (str,int):return deny
    p=request['params']
    if type(p)is not dict or set(p)!={'threadId','turnId','itemId','isBlocking','questions'}:return deny
    if p['threadId']!=thread_id or p['turnId']!=turn_id or not _text(p['itemId'],200) or type(p['isBlocking'])is not bool:return deny
    qs=p['questions']
    if type(qs)is not list or not 1<=len(qs)<=8:return deny
    seen=set()
    for q in qs:
        if type(q)is not dict or not {'id','header','question'}<=q.keys() or set(q)-{'id','header','question','options','isOther','isSecret'}:return deny
        # Secret rejection happens before retaining or copying any text.
        if q.get('isSecret',False)is not False:return deny
        if 'isOther'in q and type(q['isOther'])is not bool:return deny
        if not all(_text(q[k],lim)for k,lim in (('id',100),('header',100),('question',2000)))or q['id']in seen:return deny
        seen.add(q['id']);options=q.get('options')
        if options is not None:
            if type(options)is not list or not 1<=len(options)<=16:return deny
            labels=set()
            for option in options:
                if type(option)is not dict or set(option)!={'label','description'} or not _text(option['label'],200)or not _text(option['description'],1000)or option['label']in labels:return deny
                labels.add(option['label'])
    if len(canonical(qs))>8192:return deny
    return {'decisions':['decline','answer'],'reason':'bounded_local_text_input','questions':copy.deepcopy(qs),'isBlocking':p['isBlocking']}

def validate_answers(questions,answers):
    def reject():raise ServiceError('TASK_INPUT_ANSWER_REJECTED',status=409)
    if type(answers)is not dict or set(answers)!={q['id']for q in questions}:reject()
    for q in questions:
        value=answers[q['id']]
        if type(value)is not dict or set(value)!={'answers'}or type(value['answers'])is not list or len(value['answers'])!=1:reject()
        text=value['answers'][0]
        if not _text(text,8000):reject()
        if q.get('options')and not q.get('isOther',False)and text not in {o['label']for o in q['options']}:reject()
    if len(canonical(answers))>8192:reject()
    return copy.deepcopy(answers)
