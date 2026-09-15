#!/usr/bin/env node
import { pathToFileURL } from 'node:url';
import { existsSync } from 'node:fs';
import { resolve } from 'node:path';

const baseUrl = process.env.WEBUI_URL || 'http://127.0.0.1:5173';

async function main() {
  let chromium;
  try {
    const mod = await import('playwright');
    chromium = mod.chromium ?? mod.default?.chromium;
  } catch {
    const frontendPlaywright = resolve('frontend/node_modules/playwright/index.js');
    if (!existsSync(frontendPlaywright)) {
      console.log('SKIP: 未安装 playwright。安装后可运行：cd frontend && pnpm add -D playwright');
      return;
    }
    const mod = await import(pathToFileURL(frontendPlaywright).href);
    chromium = mod.chromium ?? mod.default?.chromium;
  }
  if (!chromium) throw new Error('Playwright chromium is not available');

  const browser = await chromium.launch();
  const page = await browser.newPage();
  try {
    await page.goto(`${baseUrl}/generate`, { waitUntil: 'networkidle' });
    await page.getByRole('heading', { name: '单条生成' }).waitFor();
    await page.getByText('参数模板').waitFor();
    await page.getByText('默认自然旁白').waitFor();
    await page.getByLabel('引擎').click();
    await page.getByText('IndexTTS v1.5', { exact: false }).count().then((count) => {
      if (count !== 0) throw new Error('普通生成入口不应默认显示 IndexTTS v1.5');
    });

    await page.goto(`${baseUrl}/voice-library`, { waitUntil: 'networkidle' });
    await page.getByRole('heading', { name: '音色库' }).waitFor();
    await page.getByText('官方参考音色（可导入）').waitFor();
    if (await page.getByText('官方女声候选 - 清晰旁白').count() < 1) {
      throw new Error('音色库应显示已导入或可导入的官方女声候选');
    }

    await page.goto(`${baseUrl}/engine-hub`, { waitUntil: 'networkidle' });
    await page.getByRole('heading', { name: '引擎中心' }).waitFor();
    await page.getByText('音频诊断参考音色').waitFor();
    console.log('PASS: WebUI 冒烟测试通过');
  } finally {
    await browser.close();
  }
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
