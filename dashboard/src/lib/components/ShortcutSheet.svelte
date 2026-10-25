<script lang="ts">
	import { available, palette } from '../palette.svelte';
	import { MOD } from '../util';
	import Icon from './Icon.svelte';

	let dialog: HTMLDialogElement | undefined = $state();
	$effect(() => {
		if (palette.keys && !dialog?.open) dialog?.showModal();
		if (!palette.keys && dialog?.open) dialog.close();
	});

	const withKeys = $derived(palette.keys ? available().filter((c) => c.shortcut) : []);
</script>

<dialog
	class="keys"
	aria-labelledby="keysT"
	bind:this={dialog}
	onclose={() => (palette.keys = false)}
	onclick={(e) => e.target === dialog && (palette.keys = false)}
>
	<div class="sheet">
		<div class="rowline">
			<h2 id="keysT">Keyboard shortcuts</h2>
			<button class="btn ghost sm" aria-label="Close shortcuts" onclick={() => (palette.keys = false)}>
				<Icon name="x" />
			</button>
		</div>
		<dl>
			<dt>Command palette</dt><dd><kbd>{MOD}</kbd><kbd>K</kbd></dd>
			<dt>This sheet</dt><dd><kbd>?</kbd></dd>
			<dt>Search this page</dt><dd><kbd>/</kbd></dd>
			{#if withKeys.length}<div class="kgrp">Go to</div>{/if}
			{#each withKeys as c (c.id)}
				<dt>{c.title}</dt>
				<dd>{#each c.shortcut!.split(' ') as k, i (i)}<kbd>{k}</kbd>{/each}</dd>
			{/each}
			<div class="kgrp">Tables and lists</div>
			<dt>Next / previous row</dt><dd><kbd>j</kbd><kbd>k</kbd></dd>
			<dt>Open row</dt><dd><kbd>↵</kbd></dd>
			<dt>Close / cancel</dt><dd><kbd>esc</kbd></dd>
		</dl>
	</div>
</dialog>
