import { computeTooltipPosition, TooltipSession } from '$lib/components/shared/hover-tooltip';

const SELECTOR = '.text-pop, .meta-pop, .desc-pop, .description-pop.has-tooltip, .param-tip, [data-tooltip]';

let container: HTMLDivElement | null = null;
let currentTarget: HTMLElement | null = null;
let fadeTimer: ReturnType<typeof setTimeout> | null = null;
let initialized = false;

function tooltipText(target: HTMLElement) {
	return target.dataset.tooltip || target.dataset.text || '';
}

function ensureContainer() {
	if (container) return container;
	container = document.createElement('div');
	container.id = 'tooltip-container';
	container.setAttribute('role', 'tooltip');
	document.body.appendChild(container);
	container.addEventListener('pointerenter', () => session.enterLayer());
	container.addEventListener('pointerleave', () => session.leaveLayer());
	return container;
}

function position(target: HTMLElement) {
	if (!container || container.style.display === 'none') return;
	const placement = computeTooltipPosition(
		target.getBoundingClientRect(),
		{ width: container.offsetWidth, height: container.offsetHeight },
		{ width: window.innerWidth, height: window.innerHeight }
	);
	container.dataset.placement = placement.placement;
	container.style.left = `${placement.left}px`;
	container.style.top = `${placement.top}px`;
}

function show(target: HTMLElement) {
	const text = tooltipText(target).trim();
	if (!text) return;
	if (fadeTimer) clearTimeout(fadeTimer);
	currentTarget?.removeAttribute('aria-describedby');
	currentTarget = target;
	const layer = ensureContainer();
	layer.textContent = text;
	layer.style.display = 'block';
	layer.style.opacity = '0';
	position(target);
	target.setAttribute('aria-describedby', layer.id);
	requestAnimationFrame(() => {
		if (currentTarget === target) layer.style.opacity = '1';
	});
}

function hide() {
	currentTarget?.removeAttribute('aria-describedby');
	currentTarget = null;
	if (!container) return;
	container.style.opacity = '0';
	fadeTimer = setTimeout(() => {
		if (container && !currentTarget) container.style.display = 'none';
	}, 100);
}

const session = new TooltipSession<HTMLElement>({ show, hide });

function tooltipTarget(event: Event) {
	return (event.target as Element | null)?.closest<HTMLElement>(SELECTOR) ?? null;
}

function onMouseOver(event: MouseEvent) {
	const target = tooltipTarget(event);
	if (!target || !tooltipText(target).trim()) return;
	if (target.contains(event.relatedTarget as Node | null)) return;
	session.enterTarget(target);
}

function onMouseOut(event: MouseEvent) {
	const target = tooltipTarget(event);
	if (!target || target.contains(event.relatedTarget as Node | null)) return;
	session.leaveTarget(target);
}

function onPointerDown(event: PointerEvent) {
	const target = event.target as Node | null;
	if (target && (currentTarget?.contains(target) || container?.contains(target))) return;
	session.close();
}

function onKeyDown(event: KeyboardEvent) {
	if (event.key === 'Escape') session.close();
}

function onViewportChange() {
	if (currentTarget) position(currentTarget);
}

export function initTooltips() {
	if (typeof window === 'undefined' || initialized) return;
	initialized = true;
	document.addEventListener('mouseover', onMouseOver);
	document.addEventListener('mouseout', onMouseOut);
	document.addEventListener('pointerdown', onPointerDown, true);
	document.addEventListener('keydown', onKeyDown, true);
	window.addEventListener('resize', onViewportChange);
	window.addEventListener('scroll', onViewportChange, true);
}
