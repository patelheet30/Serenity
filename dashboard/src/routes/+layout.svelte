<script lang="ts">
	import '#lib/styles.css';
	import { goto } from '$app/navigation';
	import { LOGIN_URL } from '#lib/api.ts';
	import Palette from '#lib/components/Palette.svelte';
	import ShortcutSheet from '#lib/components/ShortcutSheet.svelte';
	import Toasts from '#lib/components/Toasts.svelte';
	import { byShortcut, openPalette, palette, registerCommands } from '#lib/palette.svelte.ts';
	import { logout, session } from '#lib/session.svelte.ts';
	import { isTyping } from '#lib/util.ts';
	import type { LayoutProps } from './$types';

	let { children }: LayoutProps = $props();

	$effect(() =>
		registerCommands([
			{ id: 'servers', group: 'Navigate', title: 'All servers', icon: 'modules', shortcut: 'g p', available: () => !!session.me, run: () => goto('/servers') },
			{ id: 'login', group: 'Navigate', title: 'Login with Discord', icon: 'arrow', available: () => !session.me, run: () => (location.href = LOGIN_URL) },
			{ id: 'keys', group: 'Help', title: 'Keyboard shortcuts', icon: 'key', run: () => (palette.keys = true) },
			{ id: 'logout', group: 'Account', title: 'Log out', icon: 'logout', available: () => !!session.me, run: logout }
		])
	);

	// "g" starts a two-key jump (g o, g p…); the next key within 1.2 s picks the command.
	let pendingG: ReturnType<typeof setTimeout> | null = null;

	function onkeydown(e: KeyboardEvent) {
		if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') {
			e.preventDefault();
			if (palette.open) palette.open = false;
			else openPalette();
			return;
		}
		if (isTyping(e) || e.metaKey || e.ctrlKey || e.altKey || palette.open || palette.keys) return;

		if (pendingG) {
			clearTimeout(pendingG);
			pendingG = null;
			const cmd = byShortcut(`g ${e.key}`);
			if (cmd) {
				e.preventDefault();
				cmd.run?.();
			}
			return;
		}
		if (e.key === 'g') pendingG = setTimeout(() => (pendingG = null), 1200);
		else if (e.key === '?') palette.keys = true;
		else if (e.key === '/') {
			const search = document.querySelector<HTMLElement>('[data-search]');
			if (search) {
				e.preventDefault();
				search.focus();
			}
		}
	}
</script>

<svelte:window {onkeydown} />

<a class="skip" href="#main">Skip to content</a>
{@render children()}
<Palette />
<ShortcutSheet />
<Toasts />
