<script lang="ts">
	import type { Snippet } from 'svelte';

	let {
		pressed = $bindable(false),
		count,
		children,
		onclick
	}: { pressed?: boolean; count?: number; children: Snippet; onclick?: () => void } = $props();
</script>

<button
	class="chip"
	aria-pressed={pressed}
	onclick={() => {
		pressed = !pressed;
		onclick?.();
	}}
>
	{@render children()}
	{#if count !== undefined}<span class="n">{count}</span>{/if}
</button>
