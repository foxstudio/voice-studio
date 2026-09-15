import { describe, expect, it } from 'vitest';
import type { EngineSpeaker } from '$lib/api/types';
import {
	EMPTY_DOUBAO_FILTERS,
	buildDoubaoCatalogFacets,
	buildDoubaoContextualFacets,
	buildQuickSpeakers,
	doubaoCatalogTabCounts,
	filterDoubaoSpeakers,
	mergeRecentIds,
	normalizeGender
} from './doubao-voice-catalog';

function speaker(id: string, name: string, overrides: Partial<EngineSpeaker> = {}): EngineSpeaker {
	return { speaker_id: id, name, label: name, gender: '', description: '', ...overrides };
}

const speakers = [
	speaker('vivi', 'Vivi 2.0', { gender: '女', age: '青年', languages: [{ code: 'zh-cn' }], emotions: [{ value: 'happy', label: '开心' }], special_labels: ['豆包同款'], categories: ['视频配音'] }),
	speaker('yunzhou', '云舟 2.0', { gender: 'male', age: '青年', languages: ['zh-cn'], special_labels: ['抖音同款'], categories: ['知识旁白'] }),
	speaker('xiaotian', '小天 2.0', { gender: 'M', age: '少年', languages: ['zh-cn'], categories: ['角色'] }),
	speaker('pei', '佩奇猪 2.0', { gender: 'female', age: '儿童', languages: ['zh-cn'], emotions: ['angry'], categories: ['角色'] })
];

describe('doubao voice catalog filtering', () => {
	it('treats the featured tab as official special-label voices instead of all voices', () => {
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

	it('keeps the official-special tab empty when the catalog has no special labels', () => {
		const unlabeledRecommendations = [
			speaker('new', '新品音色', { normal_labels: ['新品'] }),
			speaker('plain', '普通音色')
		];
		expect(filterDoubaoSpeakers(unlabeledRecommendations, { ...EMPTY_DOUBAO_FILTERS }, 'recommended', [], [])).toHaveLength(0);
		expect(doubaoCatalogTabCounts(unlabeledRecommendations, [], []).recommended).toBe(0);
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

describe('doubao official catalog metadata', () => {
	it('builds filter options only from values that exist in the official catalog', () => {
		const facets = buildDoubaoCatalogFacets(speakers);
		expect(facets.genders).toEqual([
			{ value: 'F', label: '女声', count: 2 },
			{ value: 'M', label: '男声', count: 2 }
		]);
		expect(facets.ages).toEqual([
			{ value: '青年', label: '青年', count: 2 },
			{ value: '少年', label: '少年', count: 1 },
			{ value: '儿童', label: '儿童', count: 1 }
		]);
		expect(facets.languages).toEqual([{ value: 'zh-cn', label: 'zh-cn', count: 4 }]);
		expect(facets.emotions).toEqual([
			{ value: 'happy', label: 'happy', count: 1 },
			{ value: 'angry', label: 'angry', count: 1 }
		]);
		expect(facets.categories.some((item) => item.value === '抖音同款')).toBe(false);
		expect(facets.specialLabels).toEqual([
			{ value: '豆包同款', label: '豆包同款', count: 1 },
			{ value: '抖音同款', label: '抖音同款', count: 1 }
		]);
		expect(facets.categories.every((item) => item.count > 0)).toBe(true);
	});

	it('counts a duplicated official value only once per speaker', () => {
		const facets = buildDoubaoCatalogFacets([
			speaker('duplicate', 'Duplicate', { categories: ['有声阅读', '有声阅读'], special_labels: ['豆包同款', '豆包同款'] })
		]);
		expect(facets.categories).toEqual([{ value: '有声阅读', label: '有声阅读', count: 1 }]);
		expect(facets.specialLabels).toEqual([{ value: '豆包同款', label: '豆包同款', count: 1 }]);
	});

	it('counts each catalog tab against speakers that actually exist', () => {
		expect(doubaoCatalogTabCounts(speakers, ['missing', 'pei', 'pei', 'vivi'], ['missing', 'xiaotian'])).toEqual({
			recommended: 2,
			favorites: 2,
			recent: 1,
			all: 4
		});
	});

	it('counts facet options inside the active tab instead of the full catalog', () => {
		const contextualSpeakers = [
			speaker('featured-old', '精选长辈', { gender: 'F', age: '老年', languages: ['zh'], categories: ['有声阅读'], special_labels: ['豆包同款'] }),
			speaker('featured-young', '精选青年', { gender: 'M', age: '青年', languages: ['zh'], categories: ['视频配音'], special_labels: ['抖音同款'] }),
			speaker('regular-old', '普通长辈', { gender: 'F', age: '老年', languages: ['zh'], categories: ['有声阅读'] }),
			speaker('regular-young', '普通青年', { gender: 'M', age: '青年', languages: ['en'], categories: ['教学场景'] })
		];

		const recommended = buildDoubaoContextualFacets(contextualSpeakers, { ...EMPTY_DOUBAO_FILTERS }, 'recommended', [], []);
		const all = buildDoubaoContextualFacets(contextualSpeakers, { ...EMPTY_DOUBAO_FILTERS }, 'all', [], []);

		expect(recommended.totals.ages).toBe(2);
		expect(recommended.options.ages).toEqual([
			{ value: '老年', label: '老年', count: 1 },
			{ value: '青年', label: '青年', count: 1 }
		]);
		expect(all.totals.ages).toBe(4);
		expect(all.options.ages).toEqual([
			{ value: '老年', label: '老年', count: 2 },
			{ value: '青年', label: '青年', count: 2 }
		]);
	});

	it('lets each facet exclude itself while respecting the other selected filters', () => {
		const contextualSpeakers = [
			speaker('featured-old', '精选长辈', { gender: 'F', age: '老年', languages: ['zh'], categories: ['有声阅读'], special_labels: ['豆包同款'] }),
			speaker('featured-young', '精选青年', { gender: 'M', age: '青年', languages: ['zh'], categories: ['视频配音'], special_labels: ['抖音同款'] }),
			speaker('regular-old', '普通长辈', { gender: 'F', age: '老年', languages: ['zh'], categories: ['有声阅读'] })
		];
		const context = buildDoubaoContextualFacets(
			contextualSpeakers,
			{ ...EMPTY_DOUBAO_FILTERS, age: '老年' },
			'recommended',
			[],
			[]
		);

		expect(context.totals.ages).toBe(2);
		expect(context.options.ages.map((item) => [item.value, item.count])).toEqual([['老年', 1], ['青年', 1]]);
		expect(context.totals.genders).toBe(1);
		expect(context.options.genders).toEqual([{ value: 'F', label: '女声', count: 1 }]);
		expect(context.totals.languages).toBe(1);
		expect(context.options.languages).toEqual([{ value: 'zh', label: 'zh', count: 1 }]);
		expect(context.totals.categories).toBe(1);
		expect(context.options.categories).toEqual([{ value: '有声阅读', label: '有声阅读', count: 1 }]);
		expect(context.totals.specialLabels).toBe(1);
		expect(context.options.specialLabels).toEqual([{ value: '豆包同款', label: '豆包同款', count: 1 }]);
	});

	it('keeps a selected zero-result option visible instead of silently clearing it', () => {
		const context = buildDoubaoContextualFacets(
			speakers,
			{ ...EMPTY_DOUBAO_FILTERS, query: 'Vivi', age: '儿童' },
			'all',
			[],
			[]
		);

		expect(context.totals.ages).toBe(1);
		expect(context.options.ages).toEqual([
			{ value: '青年', label: '青年', count: 1 },
			{ value: '儿童', label: '儿童', count: 0 }
		]);
	});

	it('counts favorites, recents and search results within their active context', () => {
		const favoriteContext = buildDoubaoContextualFacets(
			speakers,
			{ ...EMPTY_DOUBAO_FILTERS },
			'favorites',
			['pei', 'vivi'],
			[]
		);
		const recentSearchContext = buildDoubaoContextualFacets(
			speakers,
			{ ...EMPTY_DOUBAO_FILTERS, query: '云舟' },
			'recent',
			[],
			['xiaotian', 'yunzhou']
		);

		expect(favoriteContext.totals.ages).toBe(2);
		expect(favoriteContext.options.ages).toEqual([
			{ value: '儿童', label: '儿童', count: 1 },
			{ value: '青年', label: '青年', count: 1 }
		]);
		expect(recentSearchContext.totals.genders).toBe(1);
		expect(recentSearchContext.options.genders).toEqual([{ value: 'M', label: '男声', count: 1 }]);
	});
});

it('normalizes official and local gender labels', () => {
	expect(normalizeGender('女声')).toBe('F');
	expect(normalizeGender('male')).toBe('M');
	expect(normalizeGender('')).toBe('U');
});
