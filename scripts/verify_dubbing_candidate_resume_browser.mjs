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
  // No managed auto-production button exists; use its public entry, then
  // inspect the actual SPA task/history surfaces and persisted result.
  await request('/dubbing/production-run/execute',{scope:'single_group',group_id:group.group_id,review_mode:'full'});
  let run;
  const deadline=Date.now()+35000;
  do {
    run=await request('/dubbing/production-run');
    if(run.groups[0].stage==='needs_semantic_review')break;
    await page.waitForTimeout(250);
  } while(Date.now()<deadline);
  assert.equal(run.groups[0].stage,'needs_semantic_review',JSON.stringify(run.groups[0]));
  const before=await request('');
  const workflow=before.tts_tasks.find(task=>task.result_id);
  assert.ok(workflow);
  const generatedBefore=await (await page.request.get(`${base}/api/__content_acceptance/state`)).json();
  const other=plan.groups[1].subtitle_ids[0];
  await request(`/localized-subtitles/${other}/edit`,{text:'另一句已修改',tts_text:'另一句已修改'},'PATCH');
  run=await request('/dubbing/production-run');
  assert.equal(run.groups[0].stage,'needs_gap_processing');
  assert.ok(run.groups[0].candidate_ids.includes(workflow.result_id));
  assert.equal(run.groups[0].passed_candidate_id,null);
  await request('/dubbing/production-run/execute',{scope:'single_group',group_id:group.group_id,review_mode:'full'});
  const after=await request('');
  const generatedAfter=await (await page.request.get(`${base}/api/__content_acceptance/state`)).json();
  assert.equal(generatedAfter.generated,generatedBefore.generated,'resume must not synthesize again');
  assert.deepEqual(after.tts_tasks.map(t=>[t.generation_task_id,t.result_id]),before.tts_tasks.map(t=>[t.generation_task_id,t.result_id]));
  const current=await request('/dubbing/production-run');
  const resumedGroup=current.groups.find(item=>item.group_id===group.group_id)??current.groups[0];
  assert.equal(resumedGroup.stage,'needs_semantic_review');
  const stagedReport=[...after.dubbing_production.candidate_reports].reverse().find(report=>
    resumedGroup.candidate_ids.includes(report.candidate_id)&&report.semantic_boundary_audit?.status==='pending_agent');
  assert.ok(stagedReport,JSON.stringify({group:resumedGroup,reports:after.dubbing_production.candidate_reports}));
  const candidateId=stagedReport.candidate_id;
  const audit=await request(`/dubbing/candidates/${encodeURIComponent(candidateId)}/semantic-boundaries`);
  assert.equal(audit.status,'pending_agent');
  const reviewed=await request(`/dubbing/candidates/${encodeURIComponent(candidateId)}/semantic-boundaries/review`,{
    schema_version:'dubbing-candidate-review-command-v2',source_revision:audit.source_revision,
    plan_revision:audit.plan_revision,candidate_id:candidateId,audio_sha256:audit.audio_sha256,
    candidate_evidence_fingerprint:audit.candidate_evidence_fingerprint,
    candidate_clip_projection_fingerprint:audit.candidate_clip_projection_fingerprint,
    semantic_boundary_reviews:audit.boundaries.map(boundary=>({
      boundary_id:boundary.boundary_id,semantic_role:'continuous_phrase',disposition:'acceptable',
      reason:'固定续跑验收已完成逐边界处置',
    })),
  });
  assert.equal(reviewed.semantic_boundary_audit.status,'accepted');
  await request('/dubbing/production-run/execute',{scope:'single_group',group_id:resumedGroup.group_id,review_mode:'full'});
  let acceptedGroup;
  const acceptanceDeadline=Date.now()+35000;
  do {
    const acceptedRun=await request('/dubbing/production-run');
    acceptedGroup=acceptedRun.groups.find(item=>item.group_id===resumedGroup.group_id);
    if(acceptedGroup?.stage==='accepted')break;
    await page.waitForTimeout(250);
  } while(Date.now()<acceptanceDeadline);
  assert.equal(acceptedGroup?.stage,'accepted',JSON.stringify(acceptedGroup));
  assert.ok(acceptedGroup.formal_clip_ids.length>0);
  const acceptedDraft=await request('');
  for(const clipId of acceptedGroup.formal_clip_ids) {
    assert.ok(acceptedDraft.timeline_clips.some(clip=>clip.clip_id===clipId&&clip.track_id==='dub'));
  }
  const generatedAfterAdoption=await (await page.request.get(`${base}/api/__content_acceptance/state`)).json();
  assert.equal(generatedAfterAdoption.generated,generatedBefore.generated,'review and adoption must reuse the old candidate');
  assert.deepEqual(acceptedDraft.tts_tasks.map(t=>[t.generation_task_id,t.result_id]),before.tts_tasks.map(t=>[t.generation_task_id,t.result_id]));
  await page.reload();
  for(const clipId of acceptedGroup.formal_clip_ids) await page.locator(`[data-audio-clip-id="${clipId}"]`).first().waitFor();
  await page.locator('.inspector-mode-tabs').getByRole('button',{name:'任务',exact:true}).click();
  await page.locator('.task-center').waitFor();
  await page.locator('.task-center').getByText('风格变了',{exact:false}).first().waitFor();
  for(const row of await page.locator('.task-center .history-summary').all()) {
    if(await row.getAttribute('aria-expanded')==='false')await row.click();
  }
  await page.locator('.task-center').getByText('风格变了',{exact:false}).first().waitFor();
  assert.match(await page.locator('.task-center').innerText(),/风格变了/);
  const refreshedDraft=await request('');
  assert.deepEqual(refreshedDraft.timeline_clips,acceptedDraft.timeline_clips);
  const refreshedRun=await request('/dubbing/production-run');
  assert.equal(refreshedRun.groups.find(item=>item.group_id===resumedGroup.group_id)?.stage,'accepted');
  assert.deepEqual(errors,[]);
  console.log('PASS: generated audio survives neighboring text rebase; old candidate resumes through semantic review and formal adoption without new TTS; SPA history, timeline and refresh retained.');
} finally {await browser.close();}
