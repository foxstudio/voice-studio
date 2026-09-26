import { isolatedBrowserOptions } from './isolated_browser_options.mjs';
import assert from 'node:assert/strict';
import { chromium } from '../frontend/node_modules/playwright/index.mjs';
const base=process.env.RECOVERY_URL;
const {project_id: project}=JSON.parse(process.env.RECOVERY_EXPECTED);
assert.ok(/^http:\/\/127\.0\.0\.1:\d+$/.test(base) && ![5173,18000].includes(Number(new URL(base).port)));
const browser=await chromium.launch(isolatedBrowserOptions({channel:'chrome',headless:true}));
try {
  const page=await browser.newPage({viewport:{width:1440,height:1000}});
  const errors=[];page.on('pageerror',e=>errors.push(e.message));
  const prefix=`${base}/api/projects/${project}/video-localization`;
  const request=async(path,data,method='POST')=>{
    const response=await page.request.fetch(prefix+path,{method:data===undefined?'GET':method,data});
    assert.ok(response.ok(),await response.text());return response.json();
  };
  const waitForAccepted=async groupId=>{
    const deadline=Date.now()+40000;
    let group;
    do {
      const run=await request('/dubbing/production-run');
      group=run.groups.find(item=>item.group_id===groupId);
      if(group?.stage==='accepted')return group;
      // A neighbor edit can leave the saved take awaiting gap processing;
      // acceptance is still expected without another TTS request.
      await page.waitForTimeout(250);
    } while(Date.now()<deadline);
    assert.equal(group?.stage,'accepted',JSON.stringify(group));
    return group;
  };
  await request('/localized-subtitles/subtitle/edit',{text:'风格变了',tts_text:'风格变了'},'PATCH');
  const snapshot=await request('/dubbing/snapshot');
  const plan=await request('/dubbing/plan',{
    source_revision:snapshot.source_revision,
    semantic_units:snapshot.semantic_units.map(unit=>({...unit,speech_policy:'translate'})),
    boundaries:snapshot.boundaries.map(boundary=>({...boundary,semantic_relation:'break'})),
  });
  assert.ok(plan.groups.length>=2);
  const group=plan.groups[0];
  await page.goto(`${base}/video-localization?project_id=${project}`);
  await page.locator('.inspector-mode-tabs').waitFor();
  // Normal finalization commits atomically: the first managed pass must reach
  // accepted without any semantic-boundary review round trip.
  await request('/dubbing/production-run/execute',{scope:'single_group',group_id:group.group_id,review_mode:'full'});
  const firstAccepted=await waitForAccepted(group.group_id);
  assert.ok(firstAccepted.formal_clip_ids.length>0);
  const acceptedBefore=await request('');
  for(const clipId of firstAccepted.formal_clip_ids) {
    assert.ok(acceptedBefore.timeline_clips.some(clip=>clip.clip_id===clipId&&clip.track_id==='dub'));
  }
  const generatedBefore=await (await page.request.get(`${base}/api/__content_acceptance/state`)).json();
  const identityBefore=acceptedBefore.tts_tasks.map(t=>[t.generation_task_id,t.result_id]);
  assert.ok(identityBefore.some(([,resultId])=>resultId),'accepted run must expose a stable TTS identity');
  // Edit a neighboring subtitle, then recover the already saved candidate.
  const other=plan.groups[1].subtitle_ids[0];
  await request(`/localized-subtitles/${other}/edit`,{text:'另一句已修改',tts_text:'另一句已修改'},'PATCH');
  const afterEditRun=await request('/dubbing/production-run');
  const afterEditGroup=afterEditRun.groups.find(item=>item.group_id===group.group_id);
  assert.ok(afterEditGroup,'the accepted group must remain visible after a neighbor edit');
  await request('/dubbing/production-run/execute',{scope:'single_group',group_id:group.group_id,review_mode:'full'});
  const resumed=await waitForAccepted(group.group_id);
  assert.ok(resumed.formal_clip_ids.length>0);
  const acceptedAfter=await request('');
  const generatedAfter=await (await page.request.get(`${base}/api/__content_acceptance/state`)).json();
  assert.equal(generatedAfter.generated,generatedBefore.generated,'recovering the saved candidate must not synthesize again');
  assert.deepEqual(acceptedAfter.tts_tasks.map(t=>[t.generation_task_id,t.result_id]),identityBefore,'stable candidate identity must survive neighbor rebase');
  for(const clipId of resumed.formal_clip_ids) {
    assert.ok(acceptedAfter.timeline_clips.some(clip=>clip.clip_id===clipId&&clip.track_id==='dub'));
  }
  await page.reload();
  for(const clipId of resumed.formal_clip_ids) await page.locator(`[data-audio-clip-id="${clipId}"]`).first().waitFor();
  await page.locator('.inspector-mode-tabs').getByRole('button',{name:'任务',exact:true}).click();
  await page.locator('.task-center').waitFor();
  for(const row of await page.locator('.task-center .history-summary').all()) {
    if(await row.getAttribute('aria-expanded')==='false')await row.click();
  }
  await page.locator('.task-center').getByText('风格变了',{exact:false}).first().waitFor();
  assert.match(await page.locator('.task-center').innerText(),/风格变了/);
  const refreshedDraft=await request('');
  assert.deepEqual(refreshedDraft.timeline_clips,acceptedAfter.timeline_clips);
  const refreshedRun=await request('/dubbing/production-run');
  assert.equal(refreshedRun.groups.find(item=>item.group_id===group.group_id)?.stage,'accepted');
  assert.deepEqual(errors,[]);
  console.log('PASS: normal pass reaches accepted atomically; neighbor edit re-recovers the saved candidate with stable identity, no new TTS, SPA history, timeline and refresh retained.');
} finally {await browser.close();}
