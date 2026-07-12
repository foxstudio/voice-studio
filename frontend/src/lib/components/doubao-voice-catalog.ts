import type { EngineSpeaker } from '$lib/api/types';

export type DoubaoCatalogTab = 'recommended' | 'favorites' | 'recent' | 'all';

export type DoubaoVoiceFilters = {
	query: string;
	gender: string;
	age: string;
	language: string;
	emotion: string;
	category: string;
};

export const EMPTY_DOUBAO_FILTERS: DoubaoVoiceFilters = {
	query: '',
	gender: 'all',
	age: 'all',
	language: 'all',
	emotion: 'all',
	category: 'all'
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
	const recommended = speakers.filter(isOfficiallyRecommended);
	const source = tab === 'recommended' && recommended.length ? recommended : speakers;
	const filtered = source.filter((speaker) => {
		if (tab === 'favorites' && !favoriteSet.has(speaker.speaker_id)) return false;
		if (tab === 'recent' && !recentSet.has(speaker.speaker_id)) return false;
		if (filters.gender !== 'all' && normalizeGender(speaker.gender) !== filters.gender) return false;
		if (filters.age !== 'all' && speaker.age !== filters.age) return false;
		if (filters.language !== 'all' && !(speaker.languages ?? []).some((item) => languageCode(item) === filters.language)) return false;
		if (filters.emotion !== 'all' && !(speaker.emotions ?? []).some((item) => emotionValue(item) === filters.emotion)) return false;
		if (filters.category !== 'all' && !speakerCategories(speaker).includes(filters.category)) return false;
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
	const recommendedIds = speakers.filter(isOfficiallyRecommended).map((speaker) => speaker.speaker_id);
	const orderedIds = uniqueStrings([selectedId, ...favoriteIds, ...recentIds, ...recommendedIds, ...speakers.map((speaker) => speaker.speaker_id)]);
	return orderedIds.map((id) => byId.get(id)).filter((speaker): speaker is EngineSpeaker => Boolean(speaker)).slice(0, limit);
}

export function isOfficiallyRecommended(speaker: EngineSpeaker): boolean {
	const labels = [...(speaker.normal_labels ?? []), ...(speaker.special_labels ?? [])].join(' ');
	return /(热门|推荐|抖音同款)/.test(labels);
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
