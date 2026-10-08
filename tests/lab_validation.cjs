// Read-only browser verification against the actual stage-8 API and saved plots.
const {chromium}=require('playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const url=process.env.LAB_URL||'http://127.0.0.1:8767';
const output=path.resolve('work/lab-stage8');

(async()=>{
  fs.mkdirSync(output,{recursive:true});
  const browser=await chromium.launch({executablePath:'C:/Program Files/Google/Chrome/Application/chrome.exe',headless:true});
  const page=await browser.newPage({viewport:{width:1440,height:1000}});
  const errors=[];page.on('pageerror',error=>errors.push(error.message));
  const checks={};
  try {
    await page.goto(url);
    await page.waitForFunction(()=>!document.querySelector('#play').disabled);
    await page.getByRole('button',{name:'Walidacja',exact:true}).click();
    await page.waitForFunction(()=>!document.querySelector('#refresh-validation').disabled && !document.querySelector('#validation-status').textContent.includes('Wczytywanie'));
    const response=await page.request.get(url+'/api/validation');
    assert.equal(response.status(),200);
    const data=await response.json();checks.status=data.status;
    assert(await page.locator('#validation-status').innerText());
    if(process.env.EXPECT_STAGE8==='passed') assert.equal(data.status,'passed');
    if(data.summary){
      assert.equal(data.completed,data.planned);
      assert.equal(data.planned,68);
      assert.equal(await page.locator('#validation-gain').innerText(),data.summary.selection.gain.toFixed(2));
      assert.equal(await page.locator('#validation-table tbody tr').count(),3);
      assert.equal(await page.locator('#ablation-table tbody tr').count(),6);
      assert.match(await page.locator('#validation-scope').innerText(),/Kalibracja biologiczna: niewykonana/);
      await page.waitForFunction(()=>['calibration-plot','ablation-plot'].every(id=>{const img=document.getElementById(id);return img.complete&&img.naturalWidth>100;}));
      checks.plots=await page.evaluate(()=>['calibration-plot','ablation-plot'].map(id=>{
        const img=document.getElementById(id),c=document.createElement('canvas');c.width=img.naturalWidth;c.height=img.naturalHeight;
        const ctx=c.getContext('2d');ctx.drawImage(img,0,0);
        const p=ctx.getImageData(0,0,c.width,c.height).data;
        let dark=0,colored=0;for(let i=0;i<p.length;i+=4){if(p[i]<150&&p[i+1]<150&&p[i+2]<150)dark++;if(Math.max(p[i],p[i+1],p[i+2])-Math.min(p[i],p[i+1],p[i+2])>35)colored++;}
        return {id,width:c.width,height:c.height,dark,colored};
      }));
      for(const plot of checks.plots){assert(plot.dark>1000);assert(plot.colored>1000);}
      const downloadUrl=new URL(await page.locator('#download-validation').getAttribute('href'),url).href;
      const downloaded=await (await page.request.get(downloadUrl)).json();
      assert.deepEqual(downloaded.summary.selection,data.summary.selection);
      checks.download_matches=true;
    } else {
      assert(await page.locator('#validation-content').isHidden());
      assert(await page.locator('#download-validation').isHidden());
      checks.pending_does_not_claim_results=true;
    }
    checks.layouts=[];
    for(const width of [320,390,768,1440,1920]){
      await page.setViewportSize({width,height:1000});
      for(const view of ['recordings','experiment','results','validation']){
        await page.locator(`[data-view="${view}"]`).click();
        await page.waitForTimeout(100);
        const overflow=await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth);
        assert(!overflow,`${view} overflow at ${width}`);
        checks.layouts.push({width,view,overflow});
      }
      if(width===390||width===1440) await page.screenshot({path:path.join(output,`${data.summary?'complete':'pending'}-${width}.png`),fullPage:true});
    }
    // Corrupt-report HTTP responses must hide all previously displayed results.
    await page.route('**/api/validation',route=>route.fulfill({status:422,contentType:'application/json',body:JSON.stringify({error:'Integrity test'})}));
    await page.getByRole('button',{name:'Odswiez wyniki',exact:true}).click();
    await page.waitForFunction(()=>document.querySelector('#validation-status').textContent.includes('Integrity test'));
    assert(await page.locator('#validation-content').isHidden());
    await page.unroute('**/api/validation');
    await page.getByRole('button',{name:'Odswiez wyniki',exact:true}).click();
    await page.waitForFunction(()=>!document.querySelector('#refresh-validation').disabled);
    checks.failed_integrity_hides_results=true;
    assert.deepEqual(errors,[]);checks.browser_errors=errors;
    fs.writeFileSync(path.join(output,`${data.summary?'complete':'pending'}-verification.json`),JSON.stringify(checks,null,2));
    console.log(JSON.stringify(checks,null,2));
  } finally {await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
