export const IS_MAC =
	typeof navigator !== 'undefined' && /Mac|iPhone|iPad/.test(navigator.platform || navigator.userAgent);
export const MOD = IS_MAC ? '⌘' : 'Ctrl';

/** Stable hue for a name, used for initials avatars. */
export function hue(s: string): number {
	let h = 0;
	for (const c of s) h = (h * 31 + c.charCodeAt(0)) >>> 0;
	return h % 360;
}

export function initials(name: string, words = false): string {
	return words
		? name.split(/\s+/).map((w) => w[0] ?? '').join('').slice(0, 2)
		: name.replace(/[^a-z0-9]/gi, '').slice(0, 2).toUpperCase();
}

export function ago(ts: number): string {
	const m = (Date.now() - ts) / 60_000;
	if (m < 1) return 'now';
	if (m < 60) return `${Math.floor(m)}m`;
	if (m < 1440) return `${Math.floor(m / 60)}h`;
	return `${Math.floor(m / 1440)}d`;
}

/** True when a keyboard event comes from somewhere the user is typing. */
export function isTyping(e: KeyboardEvent): boolean {
	const t = e.target as HTMLElement | null;
	return !!t && (t.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(t.tagName));
}
