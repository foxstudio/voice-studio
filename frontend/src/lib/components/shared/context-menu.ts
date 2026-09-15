export type ContextMenuItem = {
	id: string;
	label: string;
	description?: string;
	icon?: any;
	disabled?: boolean;
	tone?: 'default' | 'danger';
	separatorBefore?: boolean;
	onSelect: () => void | Promise<void>;
};
