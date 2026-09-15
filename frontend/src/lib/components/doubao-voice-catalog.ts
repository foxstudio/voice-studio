import type { EngineSpeaker } from '$lib/api/types';

export type DoubaoCatalogTab = 'recommended' | 'favorites' | 'recent' | 'all';

export type DoubaoFacetOption = {
	value: string;
	label: string;
	count: number;
};

export type DoubaoCatalogFacets = {
	genders: DoubaoFacetOption[];
	ages: DoubaoFacetOption[];
	languages: DoubaoFacetOption[];
	emotions: DoubaoFacetOption[];
	categories: DoubaoFacetOption[];
	specialLabels: DoubaoFacetOption[];
};

export type DoubaoCatalogTabCounts = Record<DoubaoCatalogTab, number>;

export type DoubaoCatalogFacetTotals = {
	genders: number;
	ages: number;
	languages: number;
	emotions: number;
	categories: number;
	specialLabels: number;
};

export type DoubaoContextualFacets = {
	options: DoubaoCatalogFacets;
	totals: DoubaoCatalogFacetTotals;
};

export type DoubaoVoiceFilters = {
	query: string;
	gender: string;
	age: string;
	language: string;
	emotion: string;
	category: string;
	specialLabel: string;
};

export const EMPTY_DOUBAO_FILTERS: DoubaoVoiceFilters = {
	query: '',
	gender: 'all',
	age: 'all',
	language: 'all',
	emotion: 'all',
	category: 'all',
	specialLabel: 'all'
};

export function languageCode(item: NonNullable<EngineSpeaker['languages']>[number]): string {
	return typeof item === 'string' ? item : String(item.code || item.language || '').trim();
}

export function emotionValue(item: NonNullable<EngineSpeaker['emotions']>[number]): string {
	return typeof item === 'string' ? item : String(item.value || item.label || '').trim();
}

export function speakerCategories(speaker: EngineSpeaker): string[] {
	return uniqueStrings([...(speaker.categories ?? []), ...(speaker.normal_labels ?? []), ...(speaker.special_labels ?? [])]);
}

export function speakerSearchText(speaker: EngineSpeaker): string {
	return [
		speaker.speaker_id,
		speaker.name,
		speaker.label,
		speaker.description,
		speaker.gender,
		speaker.age,
		...(speaker.languages ?? []).map(languageCode),
		...(speaker.emotions ?? []).flatMap((item) => typeof item === 'string' ? [item] : [item.value, item.label].filter((value): value is string => Boolean(value))),
		...speakerCategories(speaker)
	].filter(Boolean).join(' ').toLowerCase();
}

export function filterDoubaoSpeakers(
	speakers: EngineSpeaker[],
	filters: DoubaoVoiceFilters,
	tab: DoubaoCatalogTab,
	favoriteIds: string[],
	recentIds: string[]
): EngineSpeaker[] {
	const favoriteSet = new Set(favoriteIds);
	const recentSet = new Set(recentIds);
	const query = filters.query.trim().toLowerCase();
	const recommended = speakers.filter(hasOfficialSpecialLabel);
	const source = tab === 'recommended' ? recommended : speakers;
	const filtered = source.filter((speaker) => {
		if (tab === 'favorites' && !favoriteSet.has(speaker.speaker_id)) return false;
		if (tab === 'recent' && !recentSet.has(speaker.speaker_id)) return false;
		if (filters.gender !== 'all' && normalizeGender(speaker.gender) !== filters.gender) return false;
		if (filters.age !== 'all' && speaker.age !== filters.age) return false;
		if (filters.language !== 'all' && !(speaker.languages ?? []).some((item) => languageCode(item) === filters.language)) return false;
		if (filters.emotion !== 'all' && !(speaker.emotions ?? []).some((item) => emotionValue(item) === filters.emotion)) return false;
		if (filters.category !== 'all' && !(speaker.categories ?? []).includes(filters.category)) return false;
		if (filters.specialLabel !== 'all' && !(speaker.special_labels ?? []).includes(filters.specialLabel)) return false;
		return !query || speakerSearchText(speaker).includes(query);
	});
	if (tab === 'favorites') return orderByIds(filtered, favoriteIds);
	if (tab === 'recent') return orderByIds(filtered, recentIds);
	return filtered;
}

export function buildQuickSpeakers(
	speakers: EngineSpeaker[],
	selectedId: string,
	favoriteIds: string[],
	recentIds: string[],
	limit = 6
): EngineSpeaker[] {
	const byId = new Map(speakers.map((speaker) => [speaker.speaker_id, speaker]));
	const recommendedIds = speakers.filter(hasOfficialSpecialLabel).map((speaker) => speaker.speaker_id);
	const orderedIds = uniqueStrings([selectedId, ...favoriteIds, ...recentIds, ...recommendedIds, ...speakers.map((speaker) => speaker.speaker_id)]);
	return orderedIds.map((id) => byId.get(id)).filter((speaker): speaker is EngineSpeaker => Boolean(speaker)).slice(0, limit);
}

export function buildDoubaoCatalogFacets(speakers: EngineSpeaker[]): DoubaoCatalogFacets {
	return {
		genders: countValues(speakers.map((speaker) => normalizeGender(speaker.gender)).filter((value) => value !== 'U'), (value) => value === 'F' ? '女声' : '男声'),
		ages: countValues(speakers.map((speaker) => speaker.age || '')),
		languages: countValues(speakers.flatMap((speaker) => uniqueStrings((speaker.languages ?? []).map(languageCode)))),
		emotions: countValues(speakers.flatMap((speaker) => uniqueStrings((speaker.emotions ?? []).map(emotionValue)))),
		categories: countValues(speakers.flatMap((speaker) => uniqueStrings(speaker.categories ?? []))),
		specialLabels: countValues(speakers.flatMap((speaker) => uniqueStrings(speaker.special_labels ?? [])))
	};
}

export function buildDoubaoContextualFacets(
	speakers: EngineSpeaker[],
	filters: DoubaoVoiceFilters,
	tab: DoubaoCatalogTab,
	favoriteIds: string[],
	recentIds: string[]
): DoubaoContextualFacets {
	const catalogFacets = buildDoubaoCatalogFacets(speakers);
	const genderSpeakers = filterDoubaoSpeakers(speakers, { ...filters, gender: 'all' }, tab, favoriteIds, recentIds);
	const ageSpeakers = filterDoubaoSpeakers(speakers, { ...filters, age: 'all' }, tab, favoriteIds, recentIds);
	const languageSpeakers = filterDoubaoSpeakers(speakers, { ...filters, language: 'all' }, tab, favoriteIds, recentIds);
	const emotionSpeakers = filterDoubaoSpeakers(speakers, { ...filters, emotion: 'all' }, tab, favoriteIds, recentIds);
	const categorySpeakers = filterDoubaoSpeakers(speakers, { ...filters, category: 'all' }, tab, favoriteIds, recentIds);
	const specialLabelSpeakers = filterDoubaoSpeakers(speakers, { ...filters, specialLabel: 'all' }, tab, favoriteIds, recentIds);

	return {
		options: {
			genders: retainSelectedFacet(buildDoubaoCatalogFacets(genderSpeakers).genders, catalogFacets.genders, filters.gender),
			ages: retainSelectedFacet(buildDoubaoCatalogFacets(ageSpeakers).ages, catalogFacets.ages, filters.age),
			languages: retainSelectedFacet(buildDoubaoCatalogFacets(languageSpeakers).languages, catalogFacets.languages, filters.language),
			emotions: retainSelectedFacet(buildDoubaoCatalogFacets(emotionSpeakers).emotions, catalogFacets.emotions, filters.emotion),
			categories: retainSelectedFacet(buildDoubaoCatalogFacets(categorySpeakers).categories, catalogFacets.categories, filters.category),
			specialLabels: retainSelectedFacet(buildDoubaoCatalogFacets(specialLabelSpeakers).specialLabels, catalogFacets.specialLabels, filters.specialLabel)
		},
		totals: {
			genders: uniqueSpeakerCount(genderSpeakers),
			ages: uniqueSpeakerCount(ageSpeakers),
			languages: uniqueSpeakerCount(languageSpeakers),
			emotions: uniqueSpeakerCount(emotionSpeakers),
			categories: uniqueSpeakerCount(categorySpeakers),
			specialLabels: uniqueSpeakerCount(specialLabelSpeakers)
		}
	};
}

export function doubaoCatalogTabCounts(
	speakers: EngineSpeaker[],
	favoriteIds: string[],
	recentIds: string[]
): DoubaoCatalogTabCounts {
	const speakerIds = new Set(speakers.map((speaker) => speaker.speaker_id));
	const recommendedIds = new Set(speakers.filter(hasOfficialSpecialLabel).map((speaker) => speaker.speaker_id));
	return {
		recommended: recommendedIds.size,
		favorites: uniqueStrings(favoriteIds).filter((id) => speakerIds.has(id)).length,
		recent: uniqueStrings(recentIds).filter((id) => speakerIds.has(id)).length,
		all: speakerIds.size
	};
}

export function hasOfficialSpecialLabel(speaker: EngineSpeaker): boolean {
	return (speaker.special_labels?.length ?? 0) > 0;
}

export function mergeRecentIds(current: string[], incoming: string[], limit = 12): string[] {
	return uniqueStrings([...incoming, ...current]).slice(0, limit);
}

export function normalizeGender(value: string): string {
	const normalized = String(value || '').trim().toLowerCase();
	if (['f', 'female', '女', '女声'].includes(normalized)) return 'F';
	if (['m', 'male', '男', '男声'].includes(normalized)) return 'M';
	return 'U';
}

export function uniqueStrings(values: Array<string | null | undefined>): string[] {
	return [...new Set(values.map((value) => String(value || '').trim()).filter(Boolean))];
}

function orderByIds(speakers: EngineSpeaker[], ids: string[]): EngineSpeaker[] {
	const rank = new Map(ids.map((id, index) => [id, index]));
	return [...speakers].sort((left, right) => (rank.get(left.speaker_id) ?? Number.MAX_SAFE_INTEGER) - (rank.get(right.speaker_id) ?? Number.MAX_SAFE_INTEGER));
}

function countValues(values: string[], labelFor: (value: string) => string = (value) => value): DoubaoFacetOption[] {
	const counts = new Map<string, number>();
	for (const value of values) {
		const normalized = String(value || '').trim();
		if (!normalized) continue;
		counts.set(normalized, (counts.get(normalized) ?? 0) + 1);
	}
	return [...counts.entries()].map(([value, count]) => ({ value, label: labelFor(value), count }));
}

function uniqueSpeakerCount(speakers: EngineSpeaker[]): number {
	return new Set(speakers.map((speaker) => speaker.speaker_id)).size;
}

function retainSelectedFacet(options: DoubaoFacetOption[], catalogOptions: DoubaoFacetOption[], selected: string): DoubaoFacetOption[] {
	if (selected === 'all' || options.some((option) => option.value === selected)) return options;
	const selectedOption = catalogOptions.find((option) => option.value === selected);
	if (!selectedOption) return options;
	return [...options, { ...selectedOption, count: 0 }];
}
