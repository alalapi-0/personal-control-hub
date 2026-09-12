async (page) => {
  const unit = '/Users/alalapi/PycharmProjects/personal-control-hub/docs/reports/ui_design_governance/unit-15/';
  const origin = 'http://127.0.0.1:50008'; // Isolated fixture-runtime.json for this acceptance run.
  const checks = {};
  const ensure = (value, label) => { if (!value) throw new Error(label); checks[label] = true; };
  const design = id => origin+'/#designs/fixture-project/'+id;
  const ready = () => page.getByRole('heading',{name:'记录设计决定',exact:true}).waitFor();
  const snapshot = () => page.evaluate(async()=> (await import('/assets/common.js')).api('/api/designs'));
  const events = async n => { await page.waitForFunction(async n => (await (await import('/assets/common.js')).api('/api/designs')).history.length === n, n); };
  const history = n => page.getByText('决定历史（'+n+'）',{exact:true}).waitFor();
  const go = async id => { await page.goto(design(id)); await ready(); };
  const errors = [], requests = [];
  const consoleListener = message => { if (['error','warning'].includes(message.type())) errors.push(message.type()+': '+message.text()); };
  const responseListener = response => requests.push({method:response.request().method(),url:response.url(),status:response.status()});
  page.on('console',consoleListener); page.on('response',responseListener);
  try {
    await page.setViewportSize({width:1440,height:900});
    await go('fixture-candidate');
    ensure((await snapshot()).history.length === 0, 'fresh isolated history');
    await page.getByLabel('反馈与理由').fill('保留布局，柔化材质。隔离浏览器验收。');
    await page.reload(); await ready();
    ensure((await page.getByLabel('反馈与理由').inputValue()).includes('柔化材质'), 'draft survives reload');
    await page.getByRole('button',{name:'请求修改',exact:true}).click(); await history(1);
    ensure(await page.evaluate(()=>document.activeElement!==document.body), 'decision rerender keeps meaningful focus');
    checks.normal_console_before_faults = [...errors];
    ensure(errors.length===0, 'normal flow console clean');
    let committedRequest;
    await page.route('**/api/designs/decisions', async route => {
      committedRequest=route.request().postDataJSON().request_id;
      const response=await route.fetch();
      ensure(response.status()===200,'injected lost response follows actual commit');
      await route.abort();
    }, {times:1});
    await page.getByRole('button',{name:'选择此设计',exact:true}).click();
    await page.getByRole('heading',{name:'有一项结果不确定的操作',exact:true}).waitFor();
    const pendingBefore=await page.evaluate(()=>localStorage.getItem('hub-design-pending-command'));
    await go('fixture-alternative');
    ensure(await page.getByRole('button',{name:'选择此设计',exact:true}).isDisabled(), 'other candidate cannot overwrite pending command');
    ensure(await page.evaluate(()=>localStorage.getItem('hub-design-pending-command'))===pendingBefore,'exact pending survives cross candidate navigation');
    await page.getByRole('button',{name:'按原请求重试',exact:true}).click(); await events(2);
    await page.waitForFunction(()=>!localStorage.getItem('hub-design-pending-command'));
    ensure((await snapshot()).history.filter(x=>x.event.request_id===committedRequest).length===1, 'retry produces no duplicate event');
    ensure(!((await page.locator('main').innerText()).includes('当前：已选择')), 'same scope alternative is not falsely selected');
    ensure(await page.getByRole('button',{name:'撤回决定',exact:true}).isDisabled(),'other candidate cannot withdraw selected candidate');
    await page.goto(origin+'/#designs/fixture-project');
    await page.locator('.project-row').first().waitFor();
    ensure(await page.locator('.project-row').count()===2,'candidate list shows one latest revision per identity');
    await go('fixture-candidate');
    await page.getByRole('button',{name:'稍后决定',exact:true}).click(); await history(3);
    await page.getByRole('button',{name:'撤回决定',exact:true}).click(); await history(4);
    await page.getByRole('button',{name:'生成校验导出',exact:true}).click();
    await page.getByRole('link',{name:'下载校验后的 ZIP',exact:true}).waitFor();
    const [download]=await Promise.all([page.waitForEvent('download'),page.getByRole('link',{name:'下载校验后的 ZIP',exact:true}).click()]);
    await download.saveAs(unit+'fixture-browser-export.zip');
    checks.export_sha256=(await page.locator('#export-result').textContent()).match(/[a-f0-9]{64}/)[0];
    await page.reload(); await ready(); await history(4);
    ensure((await snapshot()).store_classification==='synthetic_fixture','fixture remains classified mock');
    checks.final_history=(await snapshot()).history.length;
    checks.expected_fault_console=errors.slice(checks.normal_console_before_faults.length);
    ensure(requests.every(r=>r.url.startsWith(origin+'/')),'all requests remain same local fixture origin');
    checks.network=requests;
    return {status:'PASS',classification:'synthetic_fixture',checks};
  } finally {
    page.off('console',consoleListener); page.off('response',responseListener);
    await page.unrouteAll({behavior:'wait'});
  }
}
