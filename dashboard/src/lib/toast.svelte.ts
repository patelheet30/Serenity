export type Toast = { id: number; message: string; error?: boolean; undo?: () => void };

export const toasts = $state<Toast[]>([]);
let next = 0;
const MAX_TOASTS = 5; // oldest go first, so a burst can't pile up

/** Show a toast for 5 s. Pass `undo` to offer an Undo button. */
export function toast(message: string, opts: { error?: boolean; undo?: () => void } = {}) {
	const t = { id: next++, message, ...opts };
	toasts.push(t);
	if (toasts.length > MAX_TOASTS) toasts.splice(0, toasts.length - MAX_TOASTS);
	setTimeout(() => dismiss(t.id), 5000);
}

export function dismiss(id: number) {
	const i = toasts.findIndex((t) => t.id === id);
	if (i >= 0) toasts.splice(i, 1);
}
