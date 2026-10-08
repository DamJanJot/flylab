// Run with the bundled Playwright on NODE_PATH and an already running lab_server.
const { chromium } = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const url = process.env.LAB_URL || 'http://127.0.0.1:8767';
const output = path.resolve('work/lab-stage7');
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));

(async () => {
  const browser = await chromium.launch({executablePath:'C:/Program Files/Google/Chrome/Application/chrome.exe',headless:true});
  const page = await browser.newPage({viewport:{width:1440,height:1050}});
  const errors=[];page.on('pageerror',error=>errors.push(error.message));
  const result={};
  try {
    await page.goto(url);
    await page.waitForFunction(()=>document.querySelector('#record-workspace').getAttribute('aria-busy')==='false' && !document.querySelector('#play').disabled);
    await page.waitForFunction(()=>document.querySelector('#video').readyState>=2);
    result.initial_title=await page.locator('#record-title').innerText();
    assert.equal(result.initial_title,'Przeszkoda');
    assert.equal(await page.locator('#neurons tr:first-child td:last-child').innerText(),'--');
    await page.getByRole('button',{name:'Odtworz',exact:true}).click();
    await page.waitForFunction(()=>Number(document.querySelector('#timeline').value)>=8);
    await page.getByRole('button',{name:'Pauza odtwarzania',exact:true}).click();
    const frozen=await page.locator('#timeline').inputValue();await sleep(300);
    assert.equal(await page.locator('#timeline').inputValue(),frozen);
    await page.locator('#timeline').fill('40');
    await page.waitForFunction(()=>Math.abs(document.querySelector('#video').currentTime-4)<.03 && !document.querySelector('#video').seeking);
    assert.equal(await page.locator('#time-label').innerText(),'0.40 / 0.80 s');
    assert.match(await page.locator('#voltage-time').innerText(),/0.40/);
    result.desktop_pixels=await page.evaluate(()=>{
      return Object.fromEntries(['camera','trajectory','spikes','inputs','commands','retina'].map(id=>{
        const c=document.getElementById(id),pixels=c.getContext('2d').getImageData(0,0,c.width,c.height).data;
        let min=255,max=0,opaque=0;for(let i=0;i<pixels.length;i+=4){if(pixels[i+3]){min=Math.min(min,pixels[i]);max=Math.max(max,pixels[i]);opaque++;}}
        return [id,{range:max-min,opaque}];
      }));
    });
    for(const stats of Object.values(result.desktop_pixels)){assert(stats.range>20);assert(stats.opaque>500);}
    const at40=await page.locator('#camera').screenshot();
    await page.screenshot({path:path.join(output,'desktop.png'),fullPage:true});
    await page.getByRole('button',{name:'Do poczatku',exact:true}).click();
    await page.waitForFunction(()=>document.querySelector('#video').currentTime<.01&&!document.querySelector('#video').seeking);
    const at0=await page.locator('#camera').screenshot();assert(!at0.equals(at40));result.camera_moving=true;
    await page.getByRole('button',{name:'Nastepna probka',exact:true}).click();
    assert.equal(await page.locator('#time-label').innerText(),'0.01 / 0.80 s');
    await page.getByRole('button',{name:'Kamera z gory',exact:true}).click();
    assert.equal(await page.locator('#camera').getAttribute('width'),'640');
    await page.getByRole('button',{name:'Obie kamery',exact:true}).click();
    await page.getByRole('button',{name:'Wyniki serii',exact:true}).click();
    assert.equal(await page.locator('#summary-table tbody tr').count(),14);
    result.obstacle_summary=await page.locator('#summary-table tbody tr').filter({hasText:'Przeszkoda'}).allTextContents();
    assert(result.obstacle_summary.some(text=>text.includes('0/3')));
    await page.getByRole('button',{name:'Zapisy',exact:true}).click();
    await page.setViewportSize({width:390,height:844});
    await page.evaluate(()=>document.querySelector('#archive-panel').open=false);
    await page.locator('#timeline').fill('40');
    await page.waitForFunction(()=>!document.querySelector('#video').seeking);
    result.mobile_overflow=await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth);
    assert.equal(result.mobile_overflow,false);
    await page.screenshot({path:path.join(output,'mobile.png'),fullPage:true});
    await page.setViewportSize({width:1440,height:1050});
    await page.getByRole('button',{name:'Nowa proba',exact:true}).click();
    await page.locator('#experiment-scenario').selectOption('left');
    await page.getByRole('button',{name:'Uruchom probe',exact:true}).click();
    await page.waitForFunction(()=>!document.querySelector('#pause-job').disabled);
    await page.getByRole('button',{name:'Pauza',exact:true}).click();
    await page.waitForFunction(()=>document.querySelector('#job-badge').textContent==='Wstrzymano',{},{timeout:60000});
    const paused=await page.locator('#job-progress').getAttribute('value');await sleep(700);
    assert.equal(await page.locator('#job-progress').getAttribute('value'),paused);
    result.simulation_pause_frozen=true;
    await page.screenshot({path:path.join(output,'experiment-paused.png'),fullPage:true});
    await page.getByRole('button',{name:'Wznow',exact:true}).click();
    await page.waitForFunction(()=>document.querySelector('#job-badge').textContent==='Zakonczono',{},{timeout:90000});
    result.completed_job=await (await page.request.get(url+'/api/job')).json();
    assert.equal(result.completed_job.state,'completed');
    const recording=await (await page.request.get(url+'/api/recordings/'+result.completed_job.recording_id)).json();
    const reference=await (await page.request.get(url+'/api/recordings/archive_left-connected-seed42')).json();
    for(const key of ['spike_indices','spike_times_s','position_mm','applied_command','input_rates_hz'])assert.deepEqual(recording.recording[key],reference.recording[key]);
    result.paused_simulation_matches_archive=true;
    await page.getByRole('button',{name:'Otworz wynik',exact:true}).click();
    await page.waitForFunction(()=>document.querySelector('#record-title').textContent==='Lewy bodziec' && document.querySelector('#record-workspace').getAttribute('aria-busy')==='false');
    assert.match(await page.locator('#record-source').innerText(),/ETAP 7/);
    await page.getByRole('button',{name:'Nowa proba',exact:true}).click();
    await page.getByRole('button',{name:'Uruchom probe',exact:true}).click();
    await page.waitForFunction(()=>!document.querySelector('#cancel-job').disabled);
    await page.getByRole('button',{name:'Przerwij',exact:true}).click();
    await page.waitForFunction(()=>document.querySelector('#job-badge').textContent==='Przerwano',{},{timeout:60000});
    result.cancelled_job=await (await page.request.get(url+'/api/job')).json();
    const failedRecord=await page.request.get(url+'/api/recordings/'+result.cancelled_job.id);
    assert.equal(failedRecord.status(),404);
    result.cancelled_not_in_catalog=true;
    await page.getByRole('button',{name:'Zapisy',exact:true}).click();
    await page.evaluate(()=>document.querySelector('#archive-panel').open=true);
    const catalog=await (await page.request.get(url+'/api/catalog')).json();
    const candidate=catalog.find(r=>r.scenario==='neutral'&&r.condition==='connected'&&!r.has_video&&!r.repeat);
    if(candidate){
      await page.locator('#search').fill('neutralna');
      await page.locator('.record-item').filter({hasText:`Podlaczony / seed ${candidate.seed}`}).first().click();
      await page.waitForFunction(()=>!document.querySelector('#no-video').hidden);
      await page.getByRole('button',{name:'Renderuj zapis',exact:true}).click();
      await page.waitForFunction(()=>document.querySelector('#job-badge').textContent==='Zakonczono',{},{timeout:90000});
      await page.waitForFunction(()=>document.querySelector('#no-video').hidden && document.querySelector('#video').readyState>=2,{},{timeout:20000});
      result.derived_render=true;
    } else result.derived_render='already present; not recomputed';
    result.errors=errors;assert.deepEqual(errors,[]);
    result.status='passed';
    console.log(JSON.stringify(result,null,2));
  } finally {
    fs.writeFileSync(path.join(output,'browser-verification.json'),JSON.stringify(result,null,2));
    await browser.close();
  }
})().catch(error=>{console.error(error);process.exitCode=1;});
