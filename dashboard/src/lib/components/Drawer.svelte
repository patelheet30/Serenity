<script lang="ts">
	import type { Snippet } from 'svelte';
	import Icon from './Icon.svelte';

	let {
		open = $bindable(false),
		title,
		children
	}: { open?: boolean; title: string; children: Snippet } = $props();

	let panel: HTMLElement | undefined = $state();
	let returnTo: Element | null = null;

	$effect(() => {
		if (!open) return;
		returnTo = document.activeElement;
		panel?.focus();
		return () => (returnTo as HTMLElement | null)?.focus?.();
	});
</script>

<svelte:window onkeydown={(e) => open && e.key === 'Escape' && (open = false)} />

{#if open}
	<div class="scrim" onclick={() => (open = false)} aria-hidden="true"></div>
	<div class="drawer" role="dialog" aria-modal="true" aria-label={title} tabindex="-1" bind:this={panel}>
		<div class="dhead">
			<h2>{title}</h2>
			<button class="btn ghost sm" aria-label="Close" onclick={() => (open = false)}>
				<Icon name="x" />
			</button>
		</div>
		<div class="dbody">{@render children()}</div>
	</div>
{/if}
