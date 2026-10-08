const {chromium}=require('playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs');
(async()=>{
  const browser=await chromium.launch({executablePath:'C:/Program Files/Google/Chrome/Application/chrome.exe',headless:true});
  const page=await browser.newPage();const checks=[];
  try{
    await page.goto(process.env.LAB_URL||'http://127.0.0.1:8767');
    await page.waitForFunction(()=>!document.querySelector('#play').disabled&&document.querySelector('#video').readyState>=2);
    for(const width of [320,390,768,1920]){
      await page.setViewportSize({width,height:1000});
      for(const view of ['experiment','results','recordings']){
        await page.locator(`[data-view="${view}"]`).click();
        await page.waitForTimeout(150);
        const overflow=await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth);
        assert(!overflow,`${view} overflow at ${width}`);
        checks.push({width,view,overflow});
      }
    }
    await page.locator('#timeline').fill('80');
    assert.match(await page.locator('#camera-time').innerText(),/0.79.*ostatnia klatka/);
    assert.match(await page.locator('#command-time').innerText(),/zakonczony/);
    await page.locator('#timeline').fill('40');
    await page.waitForFunction(()=>!document.querySelector('#video').seeking);
    await page.screenshot({path:'work/lab-stage7/wide-desktop.png',fullPage:true});
    await page.setViewportSize({width:390,height:844});
    await page.locator('[data-view="experiment"]').click();
    await page.locator('#experiment-condition').selectOption('stimulate_right');
    assert(await page.locator('#experiment-scenario').isDisabled());
    assert.equal(await page.locator('#experiment-scenario').inputValue(),'neutral');
    await page.screenshot({path:'work/lab-stage7/mobile-experiment.png',fullPage:true});
    await page.locator('[data-view="recordings"]').click();
    await page.waitForFunction(()=>document.querySelector('#spikes').width>0);
    const pixels=await page.locator('#spikes').evaluate(c=>c.getContext('2d').getImageData(0,0,c.width,c.height).data.some(v=>v));
    assert(pixels,'Charts remain nonblank after hidden-view resize');
    console.log(JSON.stringify({status:'passed',checks,terminal_clock:true,hidden_resize:true},null,2));
    fs.writeFileSync('work/lab-stage7/layout-verification.json',JSON.stringify({status:'passed',checks,terminal_clock:true,hidden_resize:true},null,2));
  }finally{await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
