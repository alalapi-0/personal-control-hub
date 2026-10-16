// Bounded observations of Root's disposable browser and this journey's fixture.
// These are process samples, not synthetic host metrics or capacity estimates.
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {execFileSync} from 'node:child_process';
import path from 'node:path';

export function ownedSampler(evidenceRoot){
  assert.ok(path.isAbsolute(evidenceRoot));
  const resourceFile=process.env.PCH_BROWSER_RESOURCES;
  assert.ok(resourceFile&&path.isAbsolute(resourceFile));
  const resources=JSON.parse(readFileSync(resourceFile,'utf8'));
  assert.ok(process.env.PCH_TASK_ID&&resources.task===process.env.PCH_TASK_ID);
  assert.equal(Number(new URL(process.argv[2]).port),resources.cdp_port);
  const fixture=JSON.parse(readFileSync(path.join(evidenceRoot,'active-fixture.json'),'utf8'));
  const targets=[{role:'fixture',pid:fixture.pid,ticks:Number(fixture.start_ticks)},
    {role:'browser_main',pid:resources.browser_pid,ticks:resources.browser_ticks}];
  const hz=Number(execFileSync('getconf',['CLK_TCK'],{encoding:'utf8',timeout:1000}).trim());
  assert.ok(Number.isSafeInteger(hz)&&hz>0);
  const samples=[];
  function readProcess(target){
    assert.ok(Number.isSafeInteger(target.pid)&&target.pid>0);
    const stat=readFileSync('/proc/'+target.pid+'/stat','utf8').split(') ').at(-1).trim().split(/\s+/);
    assert.equal(Number(stat[19]),target.ticks,'PID start identity changed');
    assert.notEqual(stat[0],'Z','Owned sampled process must still be running');
    const status=readFileSync('/proc/'+target.pid+'/status','utf8');
    const kb=key=>Number(status.match(new RegExp('^'+key+':\\s+(\\d+) kB$','m'))?.[1]);
    const rssKiB=kb('VmRSS'),highWaterKiB=kb('VmHWM');
    assert.ok(Number.isFinite(rssKiB)&&Number.isFinite(highWaterKiB));
    return {role:target.role,pid:target.pid,start_ticks:target.ticks,
      cpuSeconds:(Number(stat[11])+Number(stat[12]))/hz,rssKiB,highWaterKiB,
      threads:Number(status.match(/^Threads:\s+(\d+)$/m)?.[1])};
  }
  return {
    sample(label,requestCount){
      assert.ok(typeof label==='string'&&label.length>0&&label.length<=160);
      assert.ok(Number.isSafeInteger(requestCount)&&requestCount>=0);
      const current={label,monotonicMs:performance.now(),requestCount,processes:targets.map(readProcess)};
      const previous=samples.at(-1);
      if(previous){
        current.intervalSeconds=(current.monotonicMs-previous.monotonicMs)/1000;
        assert.ok(current.intervalSeconds>0&&requestCount>=previous.requestCount);
        current.requestsSincePrevious=requestCount-previous.requestCount;
        current.requestsPerSecond=current.requestsSincePrevious/current.intervalSeconds;
        for(const process of current.processes){
          const old=previous.processes.find(p=>p.role===process.role);
          process.cpuSecondsSincePrevious=process.cpuSeconds-old.cpuSeconds;
          process.cpuPercentOfOneCore=100*process.cpuSecondsSincePrevious/current.intervalSeconds;
          process.rssDeltaKiB=process.rssKiB-old.rssKiB;
        }
      }
      samples.push(current);return current;
    },
    finish(){
      assert.ok(samples.length>=2,'Before/after observations are required');
      return {classification:'synthetic_fixture',clockTicksPerSecond:hz,
        coverage:'Exact fixture PID and browser main PID only. Chrome renderer/GPU/utility child processes are excluded; not total browser usage or a capacity benchmark.',
        samples};
    },
  };
}
