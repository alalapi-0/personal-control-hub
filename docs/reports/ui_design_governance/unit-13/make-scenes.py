"""Compile the shared synthetic fixture into editable Figma section descriptions."""
import json
from pathlib import Path

ROOT = Path(__file__).parent
F = json.loads((ROOT/'fixture.json').read_text())
sections = []
frames = []
current = None

def section(page, label):
    global current
    current = {'page': page, 'label': label, 'ops': []}
    sections.append(current)

def add(key, parent=None, **kw):
    op = {'key': key, **kw}
    if parent: op['parent'] = parent
    current['ops'].append(op)
    return key

def text(key, parent, value, w, style='Body', color='text', **kw):
    return add(key, parent, type='text', text=value, w=w, style=style, color=color, **kw)

def button(key, parent, value, variant='secondary', **kw):
    return add(key, parent, type='button', text=value, variant=variant, **kw)

def shell(d, view, mobile, index):
    w,h=(390,844) if mobile else (1440,900)
    pad=24 if mobile else 32
    inner=1056 if d=='C' and not mobile else w-pad*2
    k=f'{d}/{view}/{"mobile" if mobile else "desktop"}'
    section(d, k+' / frame')
    add(k,w=w,h=h,fill='canvas',pad=pad,gap=8 if view=='compare' else (16 if mobile else 24),clip=True,scroll=True,center=d=='C' and not mobile,x=200+index*1640,y=1140 if mobile else 100,direction=d)
    frames.append({'key':k,'direction':d,'view':view,'mobile':mobile,'w':w,'h':h})
    section(d,k+' / navigation')
    nav=add(k+'/nav',k,axis='HORIZONTAL',w=inner,h=48,gap=8,center=True)
    text(nav+'/brand',nav,'Hub' if mobile else '◉  PERSONAL HUB',112 if mobile else 260,'Label')
    button(nav+'/projects',nav,'项目','primary' if view in ('overview','detail') else 'quiet',w=96 if mobile else 112)
    button(nav+'/designs',nav,'设计','primary' if view in ('designs','compare') else 'quiet',w=96 if mobile else 112)
    if not mobile: text(nav+'/notice',nav,F['notice'],540,'Caption','muted')
    # Heading belongs to the navigation section.
    titles={'overview':'项目','detail':'Atlas 素材索引','designs':'设计','compare':'Personal Control Hub'}
    style='Title' if mobile else ('Serif' if d=='C' else 'Display')
    text(k+'/title',k,titles[view],inner,style)
    notes={'overview':'4 个项目 · 1 项来源需关注','detail':'进行中 · 工具 · 今天 09:42 更新','designs':'比较具体版本，再留下决定。','compare':'项目与设计 · v1 · 待选择'}
    text(k+'/subtitle',k,notes[view]+(' · 示例数据' if mobile else ''),inner,'Caption' if mobile else 'Body','muted')
    return k,inner

def row(k,parent,p,comp,w):
    return add(k,parent,type='instance',component=comp,w=w,values={'Name':p['name'],'Status':p['status'],'Next':p['next'],'Updated':p['type']+' · '+p['time']+' · '+p['source']})

def info(key,parent,label,value,w,fill=None):
    box=add(key,parent,w=w,fill=fill,gap=8,pad=16 if fill else None,radius=bool(fill))
    tw=w-32 if fill else w
    text(key+'/label',box,label,tw,'Label')
    text(key+'/value',box,value,tw)
    return box

for d in 'ABC':
 for mobile in (False,True):
    k,w=shell(d,'overview',mobile,0)
    section(d,k+' / filters')
    bar=add(k+'/filters',k,axis='VERTICAL' if mobile else 'HORIZONTAL',w=w,gap=12)
    search=add(bar+'/searchbox',bar,w=w if mobile else min(640,w-360),h=44,fill='surface',pad=12,stroke='muted',radius=True)
    text(search+'/text',search,'搜索项目…',w-24 if mobile else min(616,w-384),'Body','muted')
    actions=add(bar+'/controls',bar,axis='HORIZONTAL',w=w if mobile else 336,gap=12)
    button(actions+'/filter',actions,'全部状态','secondary',w=180)
    button(actions+'/refresh',actions,'刷新全部','secondary',w=144)
    section(d,k+' / source feedback')
    text(k+'/notice',k,'3 项已更新 · Archive 来源离线，保留昨天快照。',w,'Caption','warning')
    section(d,k+' / project list')
    if d=='B' and not mobile:
        body=add(k+'/body',k,axis='HORIZONTAL',w=w,gap=24)
        listing=add(k+'/list',body,w=380,gap=12)
    else:
        listing=add(k+'/list',k,w=w,gap=8 if d=='A' else 12)
    comp='ProjectRow/Mobile' if mobile else 'ProjectRow/'+d
    for i,p in enumerate(F['projects']):
        row(k+f'/project-{i}',listing,p,comp,380 if d=='B' and not mobile else w)
    if d=='B' and not mobile:
        section(d,k+' / selected project preview')
        pane=add(k+'/preview',body,w=w-404,fill='surface',pad=32,gap=24,radius=True)
        text(pane+'/eyebrow',pane,'当前项目 / 工具',w-468,'Caption','muted')
        text(pane+'/title',pane,'Atlas 素材索引',w-468,'Title')
        info(pane+'/next',pane,'下一步',F['detail']['next'],w-468)
        info(pane+'/source',pane,'来源与关系',F['detail']['source']+'\n'+F['detail']['relationship'],w-468)
        button(pane+'/open',pane,'查看项目详情','primary')
    section(d,k+' / footnote')
    text(k+'/footer',k,'只读管理摘要 · 不更改项目中的工作内容。',w,'Caption','muted')

    k,w=shell(d,'detail',mobile,1)
    section(d,k+' / detail layout')
    body=add(k+'/body',k,axis='VERTICAL' if mobile or d=='C' else 'HORIZONTAL',w=w,gap=24)
    mainw=w if mobile or d=='C' else (944 if d=='A' else 896)
    main=add(k+'/main',body,w=mainw,gap=24)
    info(main+'/next',main,'下一步',F['detail']['next'],mainw,'surface')
    section(d,k+' / blockers and design')
    info(main+'/blocker',main,'当前情况','进行中 · 无阻塞\n原始状态 active，对应“进行中”。',mainw)
    info(main+'/design',main,'设计进度','尚无方案。生成后可在“设计”中比较具体版本。',mainw,'tint')
    section(d,k+' / evidence')
    side=add(k+'/evidence',body,w=w if mobile or d=='C' else w-mainw-24,gap=16,pad=16,fill='surface',radius=True)
    sw=(w if mobile or d=='C' else w-mainw-24)-32
    info(side+'/source',side,'权威来源',F['detail']['source']+'\n今天 09:42 · 本次读取成功',sw)
    button(side+'/refresh',side,'刷新此项目','secondary')
    section(d,k+' / relations')
    info(side+'/relation',side,'关系与依据',F['detail']['relationship']+'\n依据：'+F['detail']['relationship_basis'],sw)
    button(side+'/diagnostics',side,'诊断信息  ›','quiet',w=min(180,sw))
    section(d,k+' / detail footer')
    text(k+'/footer',k,'示例数据 · 刷新只读取获准来源，不改项目状态。',w,'Caption','muted')

    k,w=shell(d,'designs',mobile,2)
    section(d,k+' / design queue')
    cards=add(k+'/cards',k,axis='VERTICAL' if mobile or d=='C' else 'HORIZONTAL',w=w,gap=24)
    cw=w if mobile or d=='C' else (w-24)/2
    add(cards+'/hub',cards,type='instance',component='DesignCard',w=cw,values={'Name':'Personal Control Hub','Status':'3 个方案 · v1 · 待选择','Next':'项目总览、详情、设计比较 · 桌面与手机','Updated':'示例设计 · 当前无图形界面'})
    add(cards+'/atlas',cards,type='instance',component='DesignCard',w=cw,values={'Name':'Atlas 素材索引','Status':'尚无方案','Next':'已有项目管理摘要；设计候选尚未生成。','Updated':'示例数据 · 等待首个方案'})
    section(d,k+' / review entry')
    button(k+'/compare',k,'比较 Hub 方案','primary',w=w if mobile else 220)
    text(k+'/note',k,'选择绑定具体版本。改选与撤回保留历史；不会自动改动代码。',w,'Body','muted')

    k,w=shell(d,'compare',mobile,3)
    section(d,k+' / direction tabs')
    tabs=add(k+'/tabs',k,axis='HORIZONTAL',w=w,gap=8)
    for x,name in zip('ABC',['清晰编辑台','专注画布','柔和工作室']):
        button(tabs+'/'+x,tabs,x+' '+({'A':'编辑台','B':'画布','C':'工作室'}[x] if mobile else name),'primary' if x==d else 'secondary',w=106 if mobile else 180)
    section(d,k+' / comparison toolbar')
    toolbar=add(k+'/tools',k,axis='HORIZONTAL',w=w,gap=8)
    button(toolbar+'/original',toolbar,'原版','secondary',w=92)
    button(toolbar+'/candidate',toolbar,'候选','primary',w=92)
    button(toolbar+'/zoom',toolbar,'放大画布','secondary',w=132)
    if not mobile: button(toolbar+'/mobile',toolbar,'手机视图','secondary',w=132)
    section(d,k+' / comparison canvas')
    body=add(k+'/body',k,axis='HORIZONTAL' if d=='B' and not mobile else 'VERTICAL',w=w,gap=24)
    cw=w if mobile else (w-304 if d=='B' else w)
    canvases=add(k+'/canvases',body,axis='VERTICAL' if mobile else 'HORIZONTAL',w=cw,gap=16)
    paneW=cw if mobile else (cw-16)/2
    if not mobile:
        baseline=add(k+'/baseline',canvases,w=paneW,h=320,fill='surface',pad=24,gap=24,stroke='border')
        text(baseline+'/label',baseline,'原版 / 新建界面',paneW-48,'Label')
        text(baseline+'/empty',baseline,'当前无图形界面',paneW-48,'Title')
        text(baseline+'/note',baseline,'三个候选依据同一 Hub 功能规格。\n暂无历史界面可比较。',paneW-48)
    candidate=add(k+'/candidate',canvases,w=paneW,h=440 if mobile else None,fill='surface',gap=8,stroke='border',clip=True,scroll=mobile,center=True)
    text(candidate+'/label',candidate,('候选 '+d+' · v1 · 示例数据'),paneW,'Label')
    add(candidate+'/preview',candidate,type='preview',source=f'{d}/overview/{"mobile" if mobile else "desktop"}',w=paneW if mobile else min(paneW,450))
    section(d,k+' / decision')
    dw=280 if d=='B' and not mobile else w
    wide=not mobile and d!='B'
    decision=add(k+'/decision',body,w=dw,fill='surface',pad=16,gap=12,radius=True)
    text(decision+'/label',decision,'决定此版本 · 反馈（可选）',dw-32,'Label')
    inputbox=add(decision+'/feedback',decision,w=dw-32,fill='canvas',pad=12,stroke='muted',radius=True)
    text(inputbox+'/input',inputbox,F['review']['feedback'],dw-56)
    actionrow=add(decision+'/actionrow',decision,w=dw-32,gap=8,axis='HORIZONTAL' if wide else 'VERTICAL')
    button(decision+'/choose',actionrow,'选择此版本','primary',w=dw-32 if mobile or d=='B' else 180)
    action=add(decision+'/actions',actionrow,axis='HORIZONTAL' if dw>330 else 'VERTICAL',w=296 if wide else dw-32,gap=8)
    button(action+'/revise',action,'需要修改','secondary',w=144)
    button(action+'/defer',action,'暂不决定','quiet',w=128)
    section(d,k+' / history and authority')
    text(k+'/authority',k,'本次仅保存设计选择；实施授权单独记录。',w,'Caption','muted')
    footer=add(k+'/history',k,w=w,gap=8,axis='VERTICAL' if mobile else 'HORIZONTAL')
    button(footer+'/record',footer,'暂无选择记录','quiet',w=180)
    button(footer+'/export',footer,'导出设计材料','secondary',w=180)
    button(footer+'/figma',footer,'Figma 原稿','quiet',w=160)
    button(footer+'/states',footer,'状态与恢复','secondary',w=160)
    button(footer+'/text200',footer,'200% 文字','secondary',w=160)
    if mobile: button(footer+'/desktop',footer,'桌面视图','secondary',w=160)

 # All required states are editable designs; no real business effects.
 for mobile in (False,True):
    k,w=shell(d,'overview',mobile,4)
    # Unique identity for this additional sheet while preserving the shell helper.
    for s in sections[-2:]:
        s['label']=s['label'].replace('/overview/','/states/')
        for op in s['ops']:
            op['key']=op['key'].replace('/overview/','/states/')
            if 'parent' in op:op['parent']=op['parent'].replace('/overview/','/states/')
            if op['key'].endswith('/title'):op['text']='状态与恢复'
            if op['key'].endswith('/subtitle'):op['text']='示例数据 · 关键状态与恢复路径'
    frames[-1]['key']=frames[-1]['key'].replace('/overview/','/states/');frames[-1]['view']='states'
    k=k.replace('/overview/','/states/')
    states=[('加载 / 保存中', '正在保存本次决定，请稍候。'), ('没有匹配的项目', '没有找到符合当前筛选条件的项目。试试清除筛选条件。'), ('来源缺失 / 离线', '当前无法读取来源。已保留上次成功快照；连接来源后可重试。'), ('Figma 离线', '暂时无法连接 Figma。可继续查看本地快照并填写反馈。'), ('版本过期', '此版本已有更新。查看最新方案后再选择。'), ('权限 / 项目占用', '实施暂不可用：当前无授权或项目正在使用。仍可查看方案和保存反馈。'), ('保存冲突 / 失败', '决定未保存：版本已更新或连接中断。你的反馈仍在本页，可核对最新决定后重试。'), ('导出失败', '导出未完成：材料缺失或版本已更新。请核对当前版本后重试。'), ('撤回 / 改选', '示例状态：当前已选择方案 A v1。撤回将保留历史，已实施的内容保持原状。')]
    section(d,k+' / states layout')
    grid=add(k+'/grid',k,w=w,gap=12)
    for i,(label,msg) in enumerate(states):
        section(d,k+f' / state {i}')
        info(k+f'/state-{i}',grid,label,msg,w,'surface')
        box=k+f'/state-{i}'
        if i==0: button(box+'/disabled',box,'正在保存…','disabled',w=min(240,w-32))
        elif i==1: button(box+'/empty',box,'清除筛选条件','secondary',w=min(260,w-32))
        elif i in (2,3): button(box+'/retry',box,'重试读取','secondary',w=min(240,w-32),focus=i==2)
        elif i==4: button(box+'/latest',box,'查看最新版本','primary',w=min(220,w-32))
        elif i==5: button(box+'/disabled',box,'实施暂不可用','disabled',w=min(240,w-32))
        elif i==6:
            field=add(box+'/draft',box,w=w-32,fill='canvas',pad=12,stroke='muted',radius=True)
            text(field+'/text',field,F['review']['feedback']+'\n本地草稿已保留。',w-56)
            button(box+'/resolve',box,'查看最新决定','secondary',w=min(240,w-32))
        elif i==7: button(box+'/retry',box,'重试此版本导出','secondary',w=min(240,w-32))
        else: button(box+'/withdraw',box,'撤回选择','secondary',w=min(220,w-32))
    section(d,k+' / accessibility')
    info(k+'/a11y',grid,'键盘与减少动态','Tab 顺序按阅读顺序；焦点为 2px 实线。弹层关闭回到入口。减少动态时取消过渡，状态反馈保留。所有状态同时提供文字。',w,'tint')
    button(k+'/back',k,'返回候选比较','primary',w=342 if mobile else 220,index=0)

for d in 'ABC':
    k=f'{d}/original/mobile'
    section(d,k+' / original source')
    add(k,w=390,h=844,fill='canvas',pad=24,gap=24,direction=d,scroll=True,clip=True,x=8400,y=1140)
    frames.append({'key':k,'direction':d,'view':'original','mobile':True,'w':390,'h':844})
    text(k+'/title',k,'原版 / 新建界面',342,'Title')
    text(k+'/empty',k,'当前无图形界面',342,'Title')
    text(k+'/source',k,'示例设计 · 三个方案按同一 Hub 功能规格新建；暂无旧界面截图。',342)
    button(k+'/back',k,'返回候选比较','primary',w=342)
    k=f'{d}/text200/mobile'
    section(d,k+' / text at 200 percent')
    add(k,w=390,h=844,fill='canvas',pad=24,gap=24,direction=d,scroll=True,clip=True,x=10040,y=1140)
    frames.append({'key':k,'direction':d,'view':'text200','mobile':True,'w':390,'h':844})
    text(k+'/label',k,'示例数据 · 200% 文字',342,'Label',fontScale=2)
    button(k+'/projects',k,'项目','secondary',w=342,fontScale=2,focus=True)
    button(k+'/designs',k,'设计','primary',w=342,fontScale=2)
    p=F['projects'][1]
    text(k+'/name',k,p['name'],342,'Body',fontScale=2)
    text(k+'/next',k,'下一步：'+p['next'],342,'Body',fontScale=2)
    text(k+'/source',k,p['status']+' · '+p['source']+' · '+p['time'],342,'Body',fontScale=2)
    for x,name in zip('ABC',['编辑台','画布','工作室']):
        button(k+'/direction-'+x,k,x+' '+name,'primary' if x==d else 'secondary',w=342,fontScale=2)
    feedback=add(k+'/feedback',k,w=342,fill='surface',pad=12,stroke='muted',radius=True)
    text(feedback+'/label',feedback,'反馈（可选）',318,'Label',fontScale=2)
    text(feedback+'/draft',feedback,'请保留完整下一步与来源说明。长文本应自然换行、单列重排并可滚动阅读。',318,'Body',fontScale=2)
    button(k+'/choose',k,'选择此版本','primary',w=342,fontScale=2)
    text(k+'/authority',k,'本次仅保存设计选择；实施授权单独记录。',342,'Body',fontScale=2)
    button(k+'/back',k,'返回比较','primary',w=342,fontScale=2)

# State sheets are component galleries, with a return action instead of app navigation.
for s in sections:
    s['ops']=[o for o in s['ops'] if not ('/states/' in o['key'] and '/nav' in o['key'])]
# An editable HUG wrapper exposes the complete large-text content for export.
for d in 'ABC':
    k=f'{d}/text200/mobile'
    s=next(s for s in sections if any(o['key']==k for o in s['ops']))
    for o in s['ops']:
        if o.get('parent')==k:o['parent']=k+'/content'
    s['ops'].insert(1,{'key':k+'/content','parent':k,'w':342,'gap':24,'fill':'canvas','index':0})

assert len({o['key'] for s in sections for o in s['ops']})==sum(len(s['ops']) for s in sections)
(ROOT/'scene-source.json').write_text(json.dumps({'fixture':'fixture.json','frames':frames,'sections':sections},ensure_ascii=False,indent=2)+'\n')
print(json.dumps({'frames':len(frames),'sections':len(sections),'nodes':sum(len(s['ops']) for s in sections)}))
