import { describe, expect, it } from 'vitest';
import type { EngineSpeaker } from '$lib/api/types';
import {
	EMPTY_DOUBAO_FILTERS,
	buildQuickSpeakers,
	filterDoubaoSpeakers,
	mergeRecentIds,
	normalizeGender
} from './doubao-voice-catalog';

function speaker(id: string, name: string, overrides: Partial<EngineSpeaker> = {}): EngineSpeaker {
	return { speaker_id: id, name, label: name, gender: '', description: '', ...overrides };
}

const speakers = [
	speaker('vivi', 'Vivi 2.0', { gender: '女', age: '青年', languages: [{ code: 'zh-cn' }], emotions: [{ value: 'happy', label: '开心' }], normal_labels: ['热门'], categories: ['视频配音'] }),
	speaker('yunzhou', '云舟 2.0', { gender: 'male', age: '青年', languages: ['zh-cn'], special_labels: ['抖音同款'], categories: ['知识旁白'] }),
	speaker('xiaotian', '小天 2.0', { gender: 'M', age: '少年', languages: ['zh-cn'], categories: ['角色'] }),
	speaker('pei', '佩奇猪 2.0', { gender: 'female', age: '儿童', languages: ['zh-cn'], emotions: ['angry'], categories: ['角色'] })
];

describe('doubao voice catalog filtering', () => {
	it('treats recommended as official hot/recommended labels instead of all voices', () => {
		const result = filterDoubaoSpeakers(speakers, { ...EMPTY_DOUBAO_FILTERS }, 'recommended', [], []);
		expect(result.map((item) => item.speaker_id)).toEqual(['vivi', 'yunzhou']);
	});

	it('searches metadata and combines structured filters', () => {
		const result = filterDoubaoSpeakers(speakers, {
			...EMPTY_DOUBAO_FILTERS,
			query: '开心',
			gender: 'F',
			language: 'zh-cn',
			category: '视频配音'
		}, 'all', [], []);
		expect(result.map((item) => item.speaker_id)).toEqual(['vivi']);
	});

	it('orders favorites and recents by persisted preference order', () => {
		expect(filterDoubaoSpeakers(speakers, { ...EMPTY_DOUBAO_FILTERS }, 'favorites', ['pei', 'vivi'], []).map((item) => item.speaker_id)).toEqual(['pei', 'vivi']);
		expect(filterDoubaoSpeakers(speakers, { ...EMPTY_DOUBAO_FILTERS }, 'recent', [], ['xiaotian', 'yunzhou']).map((item) => item.speaker_id)).toEqual(['xiaotian', 'yunzhou']);
	});
});

describe('doubao quick voices', () => {
	it('prioritizes selected, favorites, successful recents, official hot, then fallback and caps at six', () => {
		const expanded = [...speakers, speaker('extra-1', 'Extra 1'), speaker('extra-2', 'Extra 2'), speaker('extra-3', 'Extra 3')];
		const result = buildQuickSpeakers(expanded, 'xiaotian', ['pei'], ['extra-2'], 6);
		expect(result.map((item) => item.speaker_id)).toEqual(['xiaotian', 'pei', 'extra-2', 'vivi', 'yunzhou', 'extra-1']);
	});

	it('keeps only successful recent ids and removes duplicates', () => {
		expect(mergeRecentIds(['old', 'same'], ['new', 'same'], 3)).toEqual(['new', 'same', 'old']);
	});
});

it('normalizes official and local gender labels', () => {
	expect(normalizeGender('女声')).toBe('F');
	expect(normalizeGender('male')).toBe('M');
	expect(normalizeGender('')).toBe('U');
});
