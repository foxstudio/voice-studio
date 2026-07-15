import type { Action } from 'svelte/action';

export const TOOLTIP_SHOW_DELAY_MS = 1000;
export const TOOLTIP_HIDE_DELAY_MS = 200;

type TimerHandle = ReturnType<typeof setTimeout>;

type TooltipSessionCallbacks<T> = {
	show: (target: T) => void;
	hide: () => void;
};

type TooltipSessionOptions = {
	showDelay?: number;
	hideDelay?: number;
	setTimer?: (callback: () => void, delay: number) => TimerHandle;
	clearTimer?: (timer: TimerHandle) => void;
};

export class TooltipSession<T> {
	private activeTarget: T | null = null;
	private pendingTarget: T | null = null;
	private engagedTarget: T | null = null;
	private layerEngaged = false;
	private warmed = false;
	private showTimer: TimerHandle | null = null;
	private hideTimer: TimerHandle | null = null;
	private readonly showDelay: number;
	private readonly hideDelay: number;
	private readonly setTimer: (callback: () => void, delay: number) => TimerHandle;
	private readonly clearTimer: (timer: TimerHandle) => void;

	constructor(
		private readonly callbacks: TooltipSessionCallbacks<T>,
		options: TooltipSessionOptions = {}
	) {
		this.showDelay = options.showDelay ?? TOOLTIP_SHOW_DELAY_MS;
		this.hideDelay = options.hideDelay ?? TOOLTIP_HIDE_DELAY_MS;
		this.setTimer = options.setTimer ?? ((callback, delay) => setTimeout(callback, delay));
		this.clearTimer = options.clearTimer ?? ((timer) => clearTimeout(timer));
	}

	enterTarget(target: T) {
		this.engagedTarget = target;
		this.clearHideTimer();
		if (this.warmed) {
			this.showNow(target);
			return;
		}
		if (this.pendingTarget === target) return;
		this.clearShowTimer();
		this.pendingTarget = target;
		this.showTimer = this.setTimer(() => {
			this.showTimer = null;
			if (this.engagedTarget === target) this.showNow(target);
		}, this.showDelay);
	}

	leaveTarget(target: T) {
		if (this.engagedTarget === target) this.engagedTarget = null;
		if (this.pendingTarget === target) {
			this.pendingTarget = null;
			this.clearShowTimer();
		}
		this.scheduleHide();
	}

	enterLayer() {
		this.layerEngaged = true;
		this.clearHideTimer();
	}

	leaveLayer() {
		this.layerEngaged = false;
		this.scheduleHide();
	}

	close() {
		this.clearShowTimer();
		this.clearHideTimer();
		this.activeTarget = null;
		this.pendingTarget = null;
		this.engagedTarget = null;
		this.layerEngaged = false;
		this.warmed = false;
		this.callbacks.hide();
	}

	isActive(target: T) {
		return this.activeTarget === target;
	}

	private showNow(target: T) {
		this.clearShowTimer();
		this.pendingTarget = null;
		this.activeTarget = target;
		this.warmed = true;
		this.callbacks.show(target);
	}

	private scheduleHide() {
		if (this.engagedTarget || this.layerEngaged || (!this.activeTarget && !this.pendingTarget)) return;
		this.clearHideTimer();
		this.hideTimer = this.setTimer(() => {
			this.hideTimer = null;
			if (!this.engagedTarget && !this.layerEngaged) this.close();
		}, this.hideDelay);
	}

	private clearShowTimer() {
		if (this.showTimer === null) return;
		this.clearTimer(this.showTimer);
		this.showTimer = null;
	}

	private clearHideTimer() {
		if (this.hideTimer === null) return;
		this.clearTimer(this.hideTimer);
		this.hideTimer = null;
	}
}

export type TooltipPlacement = 'top' | 'bottom' | 'left' | 'right';

export type TooltipRect = {
	top: number;
	right: number;
	bottom: number;
	left: number;
	width: number;
	height: number;
};

export function computeTooltipPosition(
	target: TooltipRect,
	overlay: { width: number; height: number },
	viewport: { width: number; height: number },
	gap = 8,
	padding = 8
) {
	const available: Record<TooltipPlacement, number> = {
		top: target.top - padding,
		bottom: viewport.height - target.bottom - padding,
		right: viewport.width - target.right - padding,
		left: target.left - padding
	};
	const required: Record<TooltipPlacement, number> = {
		top: overlay.height + gap,
		bottom: overlay.height + gap,
		right: overlay.width + gap,
		left: overlay.width + gap
	};
	const order: TooltipPlacement[] = ['top', 'bottom', 'right', 'left'];
	const placement = order.find((candidate) => available[candidate] >= required[candidate])
		?? order.reduce((best, candidate) => available[candidate] > available[best] ? candidate : best, 'top');

	let left = target.left + target.width / 2 - overlay.width / 2;
	let top = target.top - overlay.height - gap;
	if (placement === 'bottom') top = target.bottom + gap;
	if (placement === 'left') {
		left = target.left - overlay.width - gap;
		top = target.top + target.height / 2 - overlay.height / 2;
	}
	if (placement === 'right') {
		left = target.right + gap;
		top = target.top + target.height / 2 - overlay.height / 2;
	}

	return {
		placement,
		left: Math.round(Math.max(padding, Math.min(left, viewport.width - overlay.width - padding))),
		top: Math.round(Math.max(padding, Math.min(top, viewport.height - overlay.height - padding)))
	};
}

type TooltipValue = string | null | undefined;

let manager: TooltipManager | null = null;

class TooltipManager {
	private readonly layer: HTMLDivElement;
	private readonly content = new WeakMap<HTMLElement, string>();
	private readonly session: TooltipSession<HTMLElement>;
	private activeTarget: HTMLElement | null = null;
	private fadeTimer: TimerHandle | null = null;

	constructor() {
		this.layer = document.createElement('div');
		this.layer.id = 'shared-hover-tooltip';
		this.layer.setAttribute('role', 'tooltip');
		Object.assign(this.layer.style, {
			position: 'fixed',
			zIndex: '10000',
			display: 'none',
			width: 'max-content',
			maxWidth: 'min(320px, calc(100vw - 16px))',
			maxHeight: 'min(240px, calc(100vh - 16px))',
			overflow: 'auto',
			whiteSpace: 'pre-wrap',
			overflowWrap: 'anywhere',
			padding: '7px 9px',
			border: '1px solid rgba(255, 255, 255, 0.1)',
			borderRadius: '6px',
			background: 'rgba(12, 15, 20, 0.97)',
			color: '#eef3f6',
			font: '500 11px/1.45 Inter, ui-sans-serif, system-ui, sans-serif',
			boxShadow: '0 12px 28px rgba(0, 0, 0, 0.42)',
			opacity: '0',
			transition: 'opacity 100ms ease',
			pointerEvents: 'auto'
		});
		document.body.appendChild(this.layer);
		this.session = new TooltipSession({
			show: (target) => this.show(target),
			hide: () => this.hide()
		});
		this.layer.addEventListener('pointerenter', () => this.session.enterLayer());
		this.layer.addEventListener('pointerleave', () => this.session.leaveLayer());
		document.addEventListener('pointerdown', this.handleDocumentPointerDown, true);
		document.addEventListener('keydown', this.handleDocumentKeydown, true);
		window.addEventListener('resize', this.reposition);
		window.addEventListener('scroll', this.reposition, true);
	}

	register(node: HTMLElement, value: TooltipValue) {
		this.update(node, value);
		let hovered = false;
		const enter = () => {
			if (this.content.has(node)) this.session.enterTarget(node);
		};
		const leave = () => {
			if (!hovered) this.session.leaveTarget(node);
		};
		const pointerEnter = () => { hovered = true; enter(); };
		const pointerLeave = () => { hovered = false; leave(); };
		node.addEventListener('pointerenter', pointerEnter);
		node.addEventListener('pointerleave', pointerLeave);
		return () => {
			node.removeEventListener('pointerenter', pointerEnter);
			node.removeEventListener('pointerleave', pointerLeave);
			this.content.delete(node);
			if (this.session.isActive(node)) this.session.close();
		};
	}

	update(node: HTMLElement, value: TooltipValue) {
		const text = value?.trim() ?? '';
		if (text) this.content.set(node, text);
		else this.content.delete(node);
		if (this.session.isActive(node)) {
			if (text) this.show(node);
			else this.session.close();
		}
	}

	private show(target: HTMLElement) {
		const text = this.content.get(target);
		if (!text) return;
		if (this.fadeTimer !== null) clearTimeout(this.fadeTimer);
		this.activeTarget?.removeAttribute('aria-describedby');
		this.activeTarget = target;
		this.layer.textContent = text;
		this.layer.style.display = 'block';
		this.layer.style.opacity = '0';
		this.reposition();
		target.setAttribute('aria-describedby', this.layer.id);
		requestAnimationFrame(() => {
			if (this.activeTarget === target) this.layer.style.opacity = '1';
		});
	}

	private hide() {
		this.activeTarget?.removeAttribute('aria-describedby');
		this.activeTarget = null;
		this.layer.style.opacity = '0';
		this.fadeTimer = setTimeout(() => {
			if (!this.activeTarget) this.layer.style.display = 'none';
		}, 100);
	}

	private reposition = () => {
		if (!this.activeTarget || this.layer.style.display === 'none') return;
		const targetRect = this.activeTarget.getBoundingClientRect();
		const position = computeTooltipPosition(
			targetRect,
			{ width: this.layer.offsetWidth, height: this.layer.offsetHeight },
			{ width: window.innerWidth, height: window.innerHeight }
		);
		this.layer.dataset.placement = position.placement;
		this.layer.style.left = `${position.left}px`;
		this.layer.style.top = `${position.top}px`;
	};

	private handleDocumentPointerDown = (event: PointerEvent) => {
		const target = event.target as Node | null;
		if (target && (this.activeTarget?.contains(target) || this.layer.contains(target))) return;
		this.session.close();
	};

	private handleDocumentKeydown = (event: KeyboardEvent) => {
		if (event.key === 'Escape') this.session.close();
	};
}

function tooltipManager() {
	if (!manager) manager = new TooltipManager();
	return manager;
}

export const hoverTooltip: Action<HTMLElement, TooltipValue> = (node, value) => {
	if (typeof document === 'undefined') return {};
	const activeManager = tooltipManager();
	const unregister = activeManager.register(node, value);
	return {
		update(nextValue) {
			activeManager.update(node, nextValue);
		},
		destroy: unregister
	};
};
