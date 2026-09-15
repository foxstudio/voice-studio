import { describe, expect, it, vi } from 'vitest';

import { PreviewMediaRegistry } from './preview-media-registry';

function audioStub() {
	return {
		dataset: { stallRecoveryPending: '1' },
		load: vi.fn(),
		pause: vi.fn(),
		removeAttribute: vi.fn()
	} as unknown as HTMLAudioElement;
}

describe('preview media registry', () => {
	it('releases every resource owned by an unmounted dubbing element', () => {
		const registry = new PreviewMediaRegistry();
		const audio = audioStub();
		const cleanup = vi.fn();
		const source = { disconnect: vi.fn() } as unknown as MediaElementAudioSourceNode;
		const gain = { disconnect: vi.fn() } as unknown as GainNode;
		const timer = setTimeout(() => undefined, 10_000);

		registry.registerDub('clip-a', audio);
		registry.setAudioGraph(audio, source, gain);
		registry.markPlayPending(audio);
		registry.markFetchPending(audio);
		registry.setReadyRecovery(audio, cleanup);
		registry.setStallCheck(audio, timer);
		registry.setRuntimeStall(audio, { mediaTime: 1, timelineMs: 1_000 });
		registry.releaseDub('clip-a', audio);

		expect(registry.dubAudio('clip-a')).toBeUndefined();
		expect(registry.isReleased(audio)).toBe(true);
		expect(registry.gainFor(audio)).toBeUndefined();
		expect(registry.isPlayPending(audio)).toBe(false);
		expect(registry.isFetchPending(audio)).toBe(false);
		expect(registry.runtimeStall(audio)).toBeUndefined();
		expect(cleanup).toHaveBeenCalledOnce();
		expect(gain.disconnect).toHaveBeenCalledOnce();
		expect(source.disconnect).toHaveBeenCalledOnce();
		expect(audio.pause).toHaveBeenCalledOnce();
		expect(audio.removeAttribute).toHaveBeenCalledWith('src');
		expect(audio.load).toHaveBeenCalledOnce();
		expect(audio.dataset.stallRecoveryPending).toBeUndefined();
	});

	it('releases an older element when the same clip key is registered again', () => {
		const registry = new PreviewMediaRegistry();
		const first = audioStub();
		const second = audioStub();

		registry.registerDub('clip-a', first);
		registry.registerDub('clip-a', second);

		expect(registry.dubAudio('clip-a')).toBe(second);
		expect(first.pause).toHaveBeenCalledOnce();
		expect(first.load).toHaveBeenCalledOnce();
	});

	it('keeps the active stall marker until the registered check is cleared', () => {
		const registry = new PreviewMediaRegistry();
		const audio = audioStub();
		const timer = setTimeout(() => undefined, 10_000);

		registry.setStallCheck(audio, timer);

		expect(audio.dataset.stallRecoveryPending).toBe('1');
		expect(registry.hasStallCheck(audio)).toBe(true);

		registry.clearStallCheck(audio);

		expect(audio.dataset.stallRecoveryPending).toBeUndefined();
		expect(registry.hasStallCheck(audio)).toBe(false);
	});

	it('resets pending runtime state without releasing registered elements or gains', () => {
		const registry = new PreviewMediaRegistry();
		const audio = audioStub();
		const cleanup = vi.fn();
		const source = { disconnect: vi.fn() } as unknown as MediaElementAudioSourceNode;
		const gain = { disconnect: vi.fn() } as unknown as GainNode;
		const timer = setTimeout(() => undefined, 10_000);

		registry.registerFixed(audio);
		registry.setAudioGraph(audio, source, gain);
		registry.markPlayPending(audio);
		registry.markFetchPending(audio);
		registry.setDesiredRange(audio, 2, 4);
		registry.setReadyRecovery(audio, cleanup);
		registry.setStallCheck(audio, timer);
		registry.setRuntimeStall(audio, { mediaTime: 2, timelineMs: 2_000 });

		registry.resetRuntime();

		expect(registry.isPlayPending(audio)).toBe(false);
		expect(registry.isFetchPending(audio)).toBe(false);
		expect(registry.desiredTime(audio)).toBeUndefined();
		expect(registry.desiredEndTime(audio)).toBeUndefined();
		expect(registry.runtimeStall(audio)).toBeUndefined();
		expect(cleanup).toHaveBeenCalledOnce();
		expect(audio.pause).not.toHaveBeenCalled();
		expect(audio.removeAttribute).not.toHaveBeenCalled();
		expect(gain.disconnect).not.toHaveBeenCalled();
		expect(source.disconnect).not.toHaveBeenCalled();
	});

	it('releases fixed elements when they unmount', () => {
		const registry = new PreviewMediaRegistry();
		const audio = audioStub();
		const source = { disconnect: vi.fn() } as unknown as MediaElementAudioSourceNode;
		const gain = { disconnect: vi.fn() } as unknown as GainNode;

		registry.registerFixed(audio);
		registry.setAudioGraph(audio, source, gain);
		expect(registry.allAudio()).toEqual([audio]);
		registry.releaseFixed(audio);

		expect(audio.pause).toHaveBeenCalledOnce();
		expect(audio.removeAttribute).toHaveBeenCalledWith('src');
		expect(audio.load).toHaveBeenCalledOnce();
		expect(gain.disconnect).toHaveBeenCalledOnce();
		expect(source.disconnect).toHaveBeenCalledOnce();
	});

	it('cleans up the same element again after it is explicitly re-registered', () => {
		const registry = new PreviewMediaRegistry();
		const audio = audioStub();

		registry.registerDub('clip-a', audio);
		expect(registry.isReleased(audio)).toBe(false);
		registry.releaseDub('clip-a', audio);
		registry.registerDub('clip-a', audio);
		registry.releaseDub('clip-a', audio);

		expect(audio.pause).toHaveBeenCalledTimes(2);
		expect(audio.removeAttribute).toHaveBeenCalledTimes(2);
		expect(audio.load).toHaveBeenCalledTimes(2);
	});

	it('disposes all registered dubbing elements and gain nodes idempotently', () => {
		const registry = new PreviewMediaRegistry();
		const audio = audioStub();
		const source = { disconnect: vi.fn() } as unknown as MediaElementAudioSourceNode;
		const gain = { disconnect: vi.fn() } as unknown as GainNode;

		registry.registerDub('clip-a', audio);
		registry.setAudioGraph(audio, source, gain);
		registry.dispose();
		registry.dispose();

		expect(registry.dubAudio('clip-a')).toBeUndefined();
		expect(audio.pause).toHaveBeenCalledOnce();
		expect(gain.disconnect).toHaveBeenCalledOnce();
		expect(source.disconnect).toHaveBeenCalledOnce();
	});
});
