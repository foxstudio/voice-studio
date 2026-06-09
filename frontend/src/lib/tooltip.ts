/**
 * 全局 Tooltip 管理器
 *
 * 替换所有 CSS ::after 伪元素方案，解决：
 * 1. 文本达到最大宽度时正确折行
 * 2. 自动避开浏览器视口边缘
 * 3. 鼠标可移入悬浮框滚动长内容
 * 4. 全页面统一行为（text-pop / desc-pop / description-pop）
 */

const SELECTOR = '.text-pop, .meta-pop, .desc-pop, .description-pop.has-tooltip';
const HIDE_DELAY = 280; // ms — 给鼠标从触发器移到悬浮框留够时间

let container: HTMLDivElement | null = null;
let currentTarget: Element | null = null;
let hideTimer: ReturnType<typeof setTimeout> | null = null;
let hideRaf: ReturnType<typeof setTimeout> | null = null;

function ensureContainer() {
	if (container) return;
	container = document.createElement('div');
	container.id = 'tooltip-container';
	container.setAttribute('role', 'tooltip');
	document.body.appendChild(container);

	// 鼠标进入悬浮框 → 保持显示
	container.addEventListener('mouseenter', () => {
		if (hideTimer) clearTimeout(hideTimer);
	});
	// 鼠标离开悬浮框 → 延迟隐藏
	container.addEventListener('mouseleave', () => {
		scheduleHide();
	});
}

function show(target: Element) {
	const text = target.dataset.text;
	if (!text) return;

	if (hideTimer) clearTimeout(hideTimer);
	if (hideRaf) clearTimeout(hideRaf);
	currentTarget = target;

	ensureContainer();
	container!.textContent = text;
	container!.style.display = 'block';
	container!.style.opacity = '0';

	// 强制浏览器计算布局，然后定位
	void container!.offsetHeight;
	position(target);
	container!.style.opacity = '1';
}

function position(target: Element) {
	if (!container || !currentTarget) return;
	const rect = target.getBoundingClientRect();
	const gap = 10;
	const pad = 8;
	const vw = window.innerWidth;
	const vh = window.innerHeight;

	const tw = container.offsetWidth;
	const th = container.offsetHeight;

	// 优先显示在上方
	let top = rect.top - th - gap;
	// 上方空间不足 → 切换到下方
	if (top < pad) {
		top = rect.bottom + gap;
	}
	// 下方也不够 → 限制在视口内
	if (top + th > vh - pad) {
		top = Math.max(pad, vh - th - pad);
	}

	// 水平：左对齐触发器，超出右边界时右移
	let left = rect.left;
	if (left + tw > vw - pad) left = vw - tw - pad;
	if (left < pad) left = pad;

	container.style.top = `${Math.round(top)}px`;
	container.style.left = `${Math.round(left)}px`;
}

function hide() {
	if (!container) return;
	container.style.opacity = '0';
	hideRaf = setTimeout(() => {
		if (container && !currentTarget) container.style.display = 'none';
	}, 120);
	currentTarget = null;
}

function scheduleHide() {
	hideTimer = setTimeout(hide, HIDE_DELAY);
}

/* --- 事件处理 --- */

function onMouseOver(e: Event) {
	const el = (e.target as Element).closest(SELECTOR);
	if (!el) return;
	show(el);
}

function onMouseOut(e: MouseEvent) {
	const el = (e.target as Element).closest(SELECTOR);
	if (!el) return;
	// 还在元素内部（子元素间移动）→ 不隐藏
	if (el.contains(e.relatedTarget as Node)) return;
	// 移向悬浮框本身 → 不隐藏
	if (container && (e.relatedTarget === container || container.contains(e.relatedTarget as Node))) return;
	scheduleHide();
}

function onFocusIn(e: Event) {
	const el = (e.target as Element).closest(SELECTOR);
	if (el) show(el);
}

function onFocusOut(e: FocusEvent) {
	const el = (e.target as Element).closest(SELECTOR);
	if (el) scheduleHide();
}

function onScroll() {
	if (currentTarget) position(currentTarget);
}

export function initTooltips() {
	if (typeof window === 'undefined') return;
	document.addEventListener('mouseover', onMouseOver);
	document.addEventListener('mouseout', onMouseOut);
	document.addEventListener('focusin', onFocusIn);
	document.addEventListener('focusout', onFocusOut);
	window.addEventListener('scroll', onScroll, true);
}
