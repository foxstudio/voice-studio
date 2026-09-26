import { isolatedBrowserOptions } from './isolated_browser_options.mjs';
import assert from 'node:assert/strict';
import { existsSync } from 'node:fs';
import { resolve } from 'node:path';
import { pathToFileURL } from 'node:url';
const base=process.env.RECOVERY_URL, expected=JSON.parse(process.env.RECOVERY_EXPECTED);
assert.ok(/^http:\/\/127\.0\.0\.1:\d+$/.test(base) && ![5173,18000].includes(Number(new URL(base).port)));
const playwright=await import(pathToFileURL(resolve('frontend/node_modules/playwright/index.js')).href);
const chromium=playwright.chromium??playwright.default?.chromium;
const browser=await chromium.launch(isolatedBrowserOptions(existsSync('/Applications/Google Chrome.app/Contents/MacOS/Google Chrome')?{channel:'chrome'}:{}));
try {
 const page=await browser.newPage({viewport:{width:1440,height:1200}}); page.setDefaultTimeout(15000); const errors=[]; page.on('pageerror',e=>errors.push(e.message));
 await page.goto(`${base}/video-localization?project_id=${expected.project_id}`);
 const open=async()=>{
  await page.locator('.inspector-mode-tabs').getByRole('button',{name:'配音',exact:true}).click();
  await page.getByText('生成检查与分段恢复',{exact:true}).click();
  await page.getByLabel('配音组',{exact:true}).selectOption(expected.group_id);
 };
 // A valid but oversized candidate is Agent work, not a generation failure.
 const tasks = await (await page.request.get(`${base}/api/projects/${expected.project_id}/video-localization/tts/tasks`)).json();
 const latest = tasks.find(task => task.required_action === 'resolve_capacity');
 assert.ok(latest, JSON.stringify(tasks));
 assert.equal(latest.status, 'needs_attention');
 assert.equal(latest.stages.find(stage => stage.kind === 'generation').status, 'success');
 assert.notEqual(latest.stages.find(stage => stage.kind === 'placement').status, 'failed');
 await page.locator('.inspector-mode-tabs').getByRole('button', { name: '任务', exact: true }).click();
 await page.locator('.history-summary').first().waitFor();
 for (const row of await page.locator('.history-summary').all()) { if (await row.getAttribute('aria-expanded') === 'false') await row.click(); }
 await page.getByText('等待处理时间窗容量', { exact: true }).first().waitFor().catch(async error => { console.error(await page.locator('.task-center').innerText()); throw error; });
 await page.reload();
 await page.locator('.inspector-mode-tabs').getByRole('button', { name: '任务', exact: true }).click();
 await page.locator('.history-summary').first().waitFor();
 for (const row of await page.locator('.history-summary').all()) { if (await row.getAttribute('aria-expanded') === 'false') await row.click(); }
 await page.getByText('等待处理时间窗容量', { exact: true }).first().waitFor().catch(async error => { console.error(await page.locator('.task-center').innerText()); throw error; });
 console.log('page loaded'); await open(); console.log('panel opened'); await page.getByRole('button',{name:'检查可用时长',exact:true}).click();
 await page.locator('.recovery-panel [role="status"]').waitFor();
 await page.getByText('整组重试仍失败：按语义分段',{exact:true}).click();
 await page.getByLabel('分段文本',{exact:true}).fill('风格变了\n角色也变了');
 await page.getByLabel('分段理由',{exact:true}).fill('两个完整语义短语');
 const response=page.waitForResponse(r=>r.url().endsWith('/dubbing/recovery')&&r.request().method()==='POST');
 await page.getByRole('button',{name:'提交或接回分段恢复',exact:true}).click();
 const queued=await response; assert.ok(queued.ok(),await queued.text());
 const receipt=await queued.json();
 const decision=queued.request().postDataJSON();
 const deadline=Date.now()+35000;
 let state;
 do {state=await (await page.request.get(`${base}/api/__content_acceptance/state`)).json(); if(state.generated>=expected.generated_before+2)break; await page.waitForTimeout(200);}while(Date.now()<deadline);
 assert.equal(state.generated,expected.generated_before+2);
 let parent;
 do {parent=await (await page.request.get(`${base}/api/longform/${receipt.task_id}`)).json(); if(['success','failed','cancelled'].includes(parent.status))break; await page.waitForTimeout(200);}while(Date.now()<deadline);
 assert.equal(parent.status,'failed',JSON.stringify(parent));
 assert.ok(parent.segments.every(part=>part.result_id && part.attempts===1));
 await page.request.post(`${base}/api/__content_acceptance/mode`,{data:{mode:'good'}});
 const resumed=await page.request.post(`${base}/api/projects/${expected.project_id}/video-localization/dubbing/recovery`,{data:decision});
 assert.ok(resumed.ok(),await resumed.text());
 const resumeDeadline=Date.now()+20000;
 do {parent=await (await page.request.get(`${base}/api/longform/${receipt.task_id}`)).json(); if(['success','failed','cancelled'].includes(parent.status))break; await page.waitForTimeout(200);}while(Date.now()<resumeDeadline);
 assert.equal(parent.status,'success',JSON.stringify(parent));
 assert.ok(parent.segments.every(part=>part.attempts===1));
 assert.equal(parent.segments.length,2);
 assert.ok(parent.segments.every(part=>part.status==='success' && part.result_id));
 assert.ok(parent.export_id);
 // Normal finalization now commits atomically. The recovered group must reach
 // accepted on its actual persisted clips without posting a semantic-boundary
 // review or inventing an accepted audit label.
 const acceptanceDeadline=Date.now()+40000;
 let progress;
 do {
  const run=await (await page.request.get(`${base}/api/projects/${expected.project_id}/video-localization/dubbing/production-run`)).json();
  progress=run.groups.find(group=>group.group_id===expected.group_id);
  if(progress?.stage==='accepted')break;
  await page.waitForTimeout(200);
 } while(Date.now()<acceptanceDeadline);
 assert.equal(progress?.stage,'accepted',JSON.stringify(progress));
 assert.ok(progress.formal_clip_ids.length>0);
 const acceptedDraft=await (await page.request.get(`${base}/api/projects/${expected.project_id}/video-localization`)).json();
 const actualAcceptedClip=acceptedDraft.timeline_clips.find(clip=>clip.clip_id===progress.formal_clip_ids[0]&&clip.track_id==='dub');
 assert.ok(actualAcceptedClip,JSON.stringify(progress));
 const candidateId=String(actualAcceptedClip.candidate_id||actualAcceptedClip.result_id||'');
 assert.ok(candidateId,'accepted clip must expose its candidate identity');
 const replay=await page.request.post(`${base}/api/projects/${expected.project_id}/video-localization/dubbing/recovery`,{data:decision});
 assert.ok([200,409].includes(replay.status()),await replay.text());
 const replayBody=await replay.json();
 if(replay.status()===409) assert.equal(replayBody.error?.code ?? replayBody.code,'DUBBING_RECOVERY_NOT_REQUIRED');
 const replayRun=await (await page.request.get(`${base}/api/projects/${expected.project_id}/video-localization/dubbing/production-run`)).json();
 assert.equal(replayRun.groups.find(group=>group.group_id===expected.group_id)?.stage,'accepted',JSON.stringify(replayRun));
 // Evidence refresh must preserve the saved edit and avoid model work. It binds
 // the actual clip projection fingerprint; a missing exposure is reported by
 // field name instead of fabricating an accepted audit.
 const beforeRefresh=acceptedDraft;
 const currentReport=beforeRefresh.dubbing_production.candidate_reports.find(item=>item.candidate_id===candidateId);
 const currentInput=beforeRefresh.dubbing_production.candidate_inputs.find(item=>item.candidate_id===candidateId);
 const actualClip=beforeRefresh.timeline_clips.find(item=>item.candidate_id===candidateId && item.track_id==='dub');
 assert.ok(currentReport && currentInput && actualClip,JSON.stringify({candidateId,reports:beforeRefresh.dubbing_production.candidate_reports}));
 const revision=await (await page.request.get(`${base}/api/projects/${expected.project_id}/video-localization/workspace-revision`)).json();
 const providerBefore=await (await page.request.get(`${base}/api/__content_acceptance/state`)).json();
 const projectionFingerprint=currentReport.staged_candidate_projection?.candidate_clip_projection_fingerprint
  ?? actualClip.timeline_edit_gate?.candidate_clip_projection_fingerprint;
 assert.ok(projectionFingerprint,'current-projection needs an exposed candidate_clip_projection_fingerprint on candidate_reports[].staged_candidate_projection or timeline_clips[].timeline_edit_gate');
 const refreshedEvidence=await page.request.post(`${base}/api/projects/${expected.project_id}/video-localization/dubbing/candidates/${encodeURIComponent(candidateId)}/current-projection`,{data:{
  source_revision:currentInput.source_revision,plan_revision:currentInput.plan_revision,
  group_id:expected.group_id,candidate_id:candidateId,result_id:actualClip.result_id,
  expected_repository_revision:Number(revision.revision),
  candidate_clip_projection_fingerprint:projectionFingerprint,
 }});
 assert.ok(refreshedEvidence.ok(),await refreshedEvidence.text());
 const refreshedReport=await refreshedEvidence.json();
 assert.equal(refreshedReport.candidate_id,candidateId);
 const afterRefresh=await (await page.request.get(`${base}/api/projects/${expected.project_id}/video-localization`)).json();
 assert.deepEqual(afterRefresh.timeline_clips,beforeRefresh.timeline_clips);
 const providerAfter=await (await page.request.get(`${base}/api/__content_acceptance/state`)).json();
 assert.equal(providerAfter.generated,providerBefore.generated);
 assert.equal(providerAfter.asr_calls,providerBefore.asr_calls);
 console.log(JSON.stringify({merged_status:parent.status,placement_status:progress.stage,replay_status:replay.status()}));
 assert.equal((await (await page.request.get(`${base}/api/__content_acceptance/state`)).json()).generated,state.generated);

 await page.reload(); await open();
 for(const clipId of progress.formal_clip_ids) await page.locator(`[data-audio-clip-id="${clipId}"]`).first().waitFor();
 assert.deepEqual((await (await page.request.get(`${base}/api/projects/${expected.project_id}/video-localization`)).json()).timeline_clips,beforeRefresh.timeline_clips);
 await page.getByText('整组重试仍失败：按语义分段',{exact:true}).click();
 assert.equal(await page.getByLabel('分段文本',{exact:true}).inputValue(),'风格变了\n角色也变了');
 assert.deepEqual(errors,[]);
 console.log(JSON.stringify({recovery:'passed',refresh_decision:'passed',generated:state.generated}));
} finally {await browser.close();}
