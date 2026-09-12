async page => {
  const origin='http://127.0.0.1:63078', dir='/Users/alalapi/PycharmProjects/personal-control-hub/docs/reports/ui_design_governance/unit-16';
  const errors=[],network=[],failed=[];
  const onError=e=>errors.push(e.message),onConsole=m=>{if(m.type()==='error')errors.push(m.text());},onResponse=r=>network.push({url:r.url(),method:r.request().method(),status:r.status()}),onFailed=r=>failed.push({url:r.url(),error:r.failure()?.errorText});
  page.on('pageerror',onError);page.on('console',onConsole);page.on('response',onResponse);page.on('requestfailed',onFailed);
  try {
    await page.setViewportSize({width:1440,height:1000});await page.goto(origin+'/#projects');await page.waitForSelector('.project-row',{timeout:10000});
    const data=await page.evaluate(async()=>{const {api}=await import('/assets/common.js');return api('/api/projects');});
    if(data.total!==24||data.head.sequence!==130)throw Error('Project count/head differs');
    const exceptions=data.projects.filter(p=>p.declared.hub_connection_exception).map(p=>p.project_id).sort();
    const expected=['desktop-downloads-scripts','desktop-magnet','manga-localizer','pycharm-misc-project'];
    if(JSON.stringify(exceptions)!==JSON.stringify(expected))throw Error('Exception membership differs');
    const proof={status:'PASS',head:data.head,total:24,exceptions,desktop:[],mobile:[],screenshots:[]};
    await page.goto(origin+'/#projects/story-faceless-utopia');await page.reload();
    await page.waitForFunction(()=>document.querySelector('#main h1')?.textContent==='Story Faceless Utopia'&&!document.querySelector('#main')?.hasAttribute('aria-busy'),null,{timeout:10000});
    const relatedNames=data.projects.filter(p=>['novel-continuation-agent','zarathustra-adaptation'].includes(p.project_id)).map(p=>p.name);
    for(const name of relatedNames){if(await page.getByRole('link',{name,exact:true}).count()!==1)throw Error('Deep-link relation display name missing '+name);}
    proof.deep_link_relation_names=relatedNames;
    await page.evaluate(()=>{location.hash='#projects';});await page.waitForSelector('.project-row',{timeout:10000});
    async function shot(name){await page.screenshot({path:dir+'/'+name,fullPage:true});proof.screenshots.push(name);}
    await shot('projects-desktop.png');
    for(const width of [1440,390]){
      await page.setViewportSize({width,height:width===390?844:1000});
      for(const p of data.projects){
        await page.evaluate(id=>{location.hash='#projects/'+encodeURIComponent(id);},p.project_id);
        await page.waitForFunction(name=>document.querySelector('#main h1')?.textContent===name&&!document.querySelector('#main')?.hasAttribute('aria-busy'),p.name,{timeout:10000});
        const state=await page.evaluate(()=>({overflow:document.documentElement.scrollWidth>innerWidth,diagnostic:JSON.parse(document.querySelector('#main details pre').textContent),text:document.querySelector('#main').innerText,disabled:[...document.querySelectorAll('#main button')].find(b=>b.textContent==='刷新此项目')?.disabled}));
        if(state.overflow)throw Error('Horizontal overflow '+p.project_id+' '+width);
        if(JSON.stringify(state.diagnostic.provenance)!==JSON.stringify(p.provenance))throw Error('UI provenance differs '+p.project_id);
        if(JSON.stringify(state.diagnostic.errors)!==JSON.stringify(p.errors))throw Error('UI errors differ '+p.project_id);
        if(JSON.stringify(state.diagnostic.source)!==JSON.stringify(p.source))throw Error('UI source differs '+p.project_id);
        if(JSON.stringify(state.diagnostic.relations)!==JSON.stringify(p.relations))throw Error('UI relations differ '+p.project_id);
        if(p.declared.hub_connection_exception&&!state.text.includes('已授权的管理例外'))throw Error('Exception absent');
        if(p.project_id==='light-novel'&&!state.text.includes('业务状态仍未知'))throw Error('Operational-only explanation absent');
        if(p.relations.relations.length&&!state.text.includes('待确认提议'))throw Error('Relation proposal label absent');
        proof[width===390?'mobile':'desktop'].push({project_id:p.project_id,provenance_source_error_relation_parity:true,no_horizontal_overflow:true,exception_visible:!!p.declared.hub_connection_exception});
        if(['light-novel','story-faceless-utopia'].includes(p.project_id))await shot(p.project_id+(width===390?'-mobile.png':'-desktop.png'));
        if(width===390&&p.project_id==='manga-localizer')await shot('exception-mobile.png');
      }
    }
    await page.evaluate(()=>{location.hash='#projects';});await page.waitForSelector('.project-row',{timeout:10000});await shot('projects-mobile.png');
    if(await page.locator('.project-row').count()!==24)throw Error('Mobile list incomplete');
    await page.locator('#fresh-filter').selectOption('attention');if(await page.locator('.project-row').count()!==1)throw Error('Attention filter should show LightNovel diagnostic only');
    await page.locator('#fresh-filter').selectOption('fresh');if(await page.locator('.project-row').count()!==20)throw Error('Fresh filter incomplete');
    await page.locator('#fresh-filter').selectOption('');
    await page.setViewportSize({width:1440,height:1000});
    proof.filters={attention:1,fresh:20,all:24};proof.console_errors=errors;proof.network_failed=failed;proof.network=network;
    if(errors.length||failed.length||network.some(x=>x.method!=='GET'||x.status!==200||!x.url.startsWith(origin+'/')))throw Error('Unexpected console/network effects');
    return proof;
  }finally{page.off('pageerror',onError);page.off('console',onConsole);page.off('response',onResponse);page.off('requestfailed',onFailed);}
}
