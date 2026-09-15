import { render } from 'svelte/server';
import { describe, expect, it } from 'vitest';
import ReferenceAudioRangeEditor from './ReferenceAudioRangeEditor.svelte';

const baseProps = {
	sourceUrl: '/api/voices/files/source/audio',
	durationMs: 10_000,
	startMs: 1_000,
	endMs: 7_000
};

describe('shared reference audio range editor', () => {
	it('keeps the original custom-voice controls and ASR action semantics', () => {
		const { body } = render(ReferenceAudioRangeEditor, {
			props: {
				...baseProps,
				purposeLabel: '自定义音色',
				statusDirtyLabel: '待重新识别',
				applyAriaLabel: '使用选区并识别台词',
				applyTooltip: '使用当前选区作为样音，并用 ASR 识别台词',
				showRegister: true,
				clearLabel: '清除参考音频'
			}
		});

		expect(body).toContain('custom-voice-trimmer');
		expect(body).toContain('自定义音色裁切时间轴');
		expect(body).toContain('使用选区并识别台词');
		expect(body).toContain('注册为音色');
		expect(body).toContain('清除参考音频');
		expect(body).toContain('从当前指针播放到出点');
		expect(body).toContain('开启循环播放');
		expect(body).toContain('滚轮缩放，Shift 加滚轮横向移动');
		expect(body).toContain('aria-keyshortcuts="Space I O = -"');
		expect(body).toContain('tabindex="0"');
	});

	it('accepts a managed waveform source without changing the audio playback source', () => {
		const { body } = render(ReferenceAudioRangeEditor, {
			props: {
				...baseProps,
				waveformUrl: '/api/voices/files/source/waveform'
			}
		});

		expect(body).toContain('src="/api/voices/files/source/audio"');
		expect(body).toContain('custom-voice-waveform');
	});

	it('uses the same editor for emotion clips without exposing ASR or registration', () => {
		const { body } = render(ReferenceAudioRangeEditor, {
			props: {
				...baseProps,
				purposeLabel: '情绪参考',
				applyAriaLabel: '使用这个情绪片段',
				applyTooltip: '裁切当前选区作为独立情绪参考，不运行 ASR'
			}
		});

		expect(body).toContain('custom-voice-trimmer');
		expect(body).toContain('情绪参考裁切时间轴');
		expect(body).toContain('使用这个情绪片段');
		expect(body).toContain('不运行 ASR');
		expect(body).not.toContain('注册为音色');
	});
});
